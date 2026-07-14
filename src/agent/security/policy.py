"""把带来源的配置编译为不可放宽的 workspace capability policy。"""

from __future__ import annotations

from dataclasses import dataclass
from copy import deepcopy
from pathlib import Path
from typing import Literal, cast

from agent.config.loader import ConfigSource, LoadedConfig, _deep_merge
from agent.config.sections import AgentSettings, CapabilityName
from agent.permissions import PermissionDecision

TrustLevel = Literal["trusted", "untrusted"]

CAPABILITIES: tuple[CapabilityName, ...] = (
    "read_fs",
    "write_fs",
    "shell",
    "network",
    "secrets",
    "mcp",
    "plugin",
    "hook",
    "vcs_delete",
    "publish",
)

_RANK: dict[PermissionDecision, int] = {"deny": 0, "ask": 1, "allow": 2}

_UNTRUSTED_DEFAULTS: dict[CapabilityName, PermissionDecision] = {
    "read_fs": "allow",
    "write_fs": "allow",
    "shell": "ask",
    "network": "deny",
    "secrets": "deny",
    "mcp": "deny",
    "plugin": "deny",
    "hook": "deny",
    "vcs_delete": "ask",
    "publish": "ask",
}

_TRUSTED_DEFAULTS: dict[CapabilityName, PermissionDecision] = {
    **_UNTRUSTED_DEFAULTS,
    "shell": "allow",
    "network": "allow",
    "mcp": "allow",
    "plugin": "allow",
    "hook": "allow",
}


@dataclass(frozen=True)
class CapabilityDecision:
    capability: CapabilityName
    state: PermissionDecision
    reason: str
    sources: tuple[str, ...]


@dataclass(frozen=True)
class PolicyDiagnostic:
    code: str
    message: str
    source: ConfigSource


@dataclass(frozen=True)
class EffectivePolicy:
    settings: AgentSettings
    trust: TrustLevel
    read_roots: tuple[Path, ...]
    write_roots: tuple[Path, ...]
    capabilities: tuple[CapabilityDecision, ...]
    diagnostics: tuple[PolicyDiagnostic, ...]

    def decide(self, capability: CapabilityName) -> CapabilityDecision:
        for decision in self.capabilities:
            if decision.capability == capability:
                return decision
        raise KeyError(capability)


class PolicyCompiler:
    """以 user/CLI 为 authority、以 project 为单调约束编译策略。"""

    def compile(self, loaded: LoadedConfig, *, workspace_root: Path) -> EffectivePolicy:
        workspace_root = workspace_root.expanduser().resolve()
        security_by_source = [
            (document.source, _security_data(document.data))
            for document in loaded.documents
        ]

        trust: TrustLevel = "untrusted"
        for source, security in security_by_source:
            if source in {"user", "cli"} and "workspace_trust" in security:
                trust = cast(TrustLevel, security["workspace_trust"])

        defaults = _TRUSTED_DEFAULTS if trust == "trusted" else _UNTRUSTED_DEFAULTS
        states = dict(defaults)
        sources: dict[CapabilityName, list[str]] = {
            capability: ["default"] for capability in CAPABILITIES
        }
        diagnostics: list[PolicyDiagnostic] = []

        for source, security in security_by_source:
            if source not in {"user", "cli"}:
                continue
            for capability, requested in _capability_items(security):
                states[capability] = requested
                sources[capability].append(source)

        for source, security in security_by_source:
            if source != "project":
                continue
            for capability, requested in _capability_items(security):
                current = states[capability]
                if _RANK[requested] <= _RANK[current]:
                    states[capability] = requested
                    sources[capability].append(source)
                else:
                    diagnostics.append(
                        PolicyDiagnostic(
                            code="PROJECT_CAPABILITY_WIDENING_IGNORED",
                            message=(
                                f"project requested {capability}={requested}, "
                                f"authority permits only {current}"
                            ),
                            source="project",
                        )
                    )

        read_roots = self._compile_roots(
            "read_roots",
            security_by_source,
            workspace_root,
            diagnostics,
        )
        write_roots = self._compile_roots(
            "write_roots",
            security_by_source,
            workspace_root,
            diagnostics,
        )
        decisions = tuple(
            CapabilityDecision(
                capability=capability,
                state=states[capability],
                reason=f"effective {capability} policy is {states[capability]}",
                sources=tuple(sources[capability]),
            )
            for capability in CAPABILITIES
        )
        effective_settings = _compile_effective_settings(
            loaded,
            diagnostics,
            workspace_root,
        )
        return EffectivePolicy(
            settings=effective_settings,
            trust=trust,
            read_roots=read_roots,
            write_roots=write_roots,
            capabilities=decisions,
            diagnostics=tuple(diagnostics),
        )

    def _compile_roots(
        self,
        key: Literal["read_roots", "write_roots"],
        security_by_source: list[tuple[ConfigSource, dict[str, object]]],
        workspace_root: Path,
        diagnostics: list[PolicyDiagnostic],
    ) -> tuple[Path, ...]:
        authority_roots = (workspace_root,)
        for source, security in security_by_source:
            if source in {"user", "cli"} and key in security:
                authority_roots = _resolve_roots(security[key], workspace_root)

        effective = authority_roots
        for source, security in security_by_source:
            if source != "project" or key not in security:
                continue
            requested = _resolve_roots(security[key], workspace_root)
            allowed: list[Path] = []
            for root in requested:
                if any(root.is_relative_to(authority) for authority in authority_roots):
                    allowed.append(root)
                else:
                    diagnostics.append(
                        PolicyDiagnostic(
                            code="PROJECT_ROOT_WIDENING_IGNORED",
                            message=f"project requested {key} outside authority: {root}",
                            source="project",
                        )
                    )
            effective = tuple(allowed)
        return tuple(dict.fromkeys(effective))


def _security_data(data: dict[str, object]) -> dict[str, object]:
    security = data.get("security")
    return dict(security) if isinstance(security, dict) else {}


def _capability_items(
    security: dict[str, object],
) -> list[tuple[CapabilityName, PermissionDecision]]:
    raw = security.get("capabilities")
    if not isinstance(raw, dict):
        return []
    return [
        (cast(CapabilityName, capability), cast(PermissionDecision, decision))
        for capability, decision in raw.items()
    ]


def _resolve_roots(value: object, workspace_root: Path) -> tuple[Path, ...]:
    if not isinstance(value, list):
        return ()
    roots: list[Path] = []
    for raw in value:
        root = Path(str(raw)).expanduser()
        if not root.is_absolute():
            root = workspace_root / root
        roots.append(root.resolve())
    return tuple(dict.fromkeys(roots))


def _compile_effective_settings(
    loaded: LoadedConfig,
    diagnostics: list[PolicyDiagnostic],
    workspace_root: Path,
) -> AgentSettings:
    raw: dict[str, object] = {}
    for document in loaded.documents:
        data = deepcopy(document.data)
        if document.source == "project":
            data = _filter_project_security_fields(
                data,
                raw,
                diagnostics,
                workspace_root,
            )
        raw = _deep_merge(raw, data)
    return AgentSettings.model_validate(raw)


def _filter_project_security_fields(
    project: dict[str, object],
    authority_raw: dict[str, object],
    diagnostics: list[PolicyDiagnostic],
    workspace_root: Path,
) -> dict[str, object]:
    filtered = deepcopy(project)

    model = filtered.get("model")
    if isinstance(model, dict) and "profiles" in model:
        model.pop("profiles")
        _ignored(diagnostics, "PROJECT_ENDPOINT_IGNORED", "model.profiles")
    if "model_profiles" in filtered:
        filtered.pop("model_profiles")
        _ignored(diagnostics, "PROJECT_ENDPOINT_IGNORED", "model_profiles")

    _filter_project_paths(filtered, workspace_root, diagnostics)

    authority = AgentSettings.model_validate(authority_raw)
    permissions = filtered.get("permissions")
    if isinstance(permissions, dict):
        requested_default = permissions.get("default")
        if isinstance(requested_default, str) and _is_widening(
            requested_default,
            authority.permissions.default,
        ):
            permissions.pop("default")
            _ignored(diagnostics, "PROJECT_PERMISSION_WIDENING_IGNORED", "permissions.default")
        requested_tools = permissions.get("tools")
        if isinstance(requested_tools, dict):
            kept: dict[str, object] = {}
            for tool, requested in requested_tools.items():
                current = authority.permissions.tools.get(
                    str(tool),
                    authority.permissions.default,
                )
                if isinstance(requested, str) and _is_widening(requested, current):
                    _ignored(
                        diagnostics,
                        "PROJECT_PERMISSION_WIDENING_IGNORED",
                        f"permissions.tools.{tool}",
                    )
                else:
                    kept[str(tool)] = requested
            permissions["tools"] = kept

    requested_default = filtered.get("permission_default")
    if isinstance(requested_default, str) and _is_widening(
        requested_default,
        authority.permissions.default,
    ):
        filtered.pop("permission_default")
        _ignored(diagnostics, "PROJECT_PERMISSION_WIDENING_IGNORED", "permission_default")
    requested_tools = filtered.get("permission_tools")
    if isinstance(requested_tools, dict):
        kept_tools: dict[str, object] = {}
        for tool, requested in requested_tools.items():
            current = authority.permissions.tools.get(str(tool), authority.permissions.default)
            if isinstance(requested, str) and _is_widening(requested, current):
                _ignored(
                    diagnostics,
                    "PROJECT_PERMISSION_WIDENING_IGNORED",
                    f"permission_tools.{tool}",
                )
            else:
                kept_tools[str(tool)] = requested
        filtered["permission_tools"] = kept_tools
    return filtered


def _is_widening(requested: str, current: PermissionDecision) -> bool:
    return requested in _RANK and _RANK[cast(PermissionDecision, requested)] > _RANK[current]


def _ignored(
    diagnostics: list[PolicyDiagnostic],
    code: str,
    field: str,
) -> None:
    diagnostics.append(
        PolicyDiagnostic(
            code=code,
            message=f"project value ignored for protected field: {field}",
            source="project",
        )
    )


_NESTED_PROJECT_PATH_FIELDS: dict[str, tuple[str, ...]] = {
    "state": ("data_dir", "memory_path", "skills_dir", "tasks_dir"),
    "teams": ("log_path",),
    "worktree": ("dir",),
    "debug": ("log_path",),
    "trace": ("dir",),
    "permissions": ("rules_path",),
    "extensions": ("hooks_path",),
}

_FLAT_PROJECT_PATH_FIELDS = (
    "data_dir",
    "memory_path",
    "skills_dir",
    "tasks_dir",
    "team_log_path",
    "worktree_dir",
    "debug_log_path",
    "trace_dir",
    "permission_rules_path",
    "hooks_path",
)


def _filter_project_paths(
    filtered: dict[str, object],
    workspace_root: Path,
    diagnostics: list[PolicyDiagnostic],
) -> None:
    for section_name, keys in _NESTED_PROJECT_PATH_FIELDS.items():
        section = filtered.get(section_name)
        if not isinstance(section, dict):
            continue
        for key in keys:
            value = section.get(key)
            if isinstance(value, str) and value and not _is_workspace_path(value, workspace_root):
                section.pop(key)
                _ignored(
                    diagnostics,
                    "PROJECT_PATH_ESCAPE_IGNORED",
                    f"{section_name}.{key}",
                )

    for key in _FLAT_PROJECT_PATH_FIELDS:
        value = filtered.get(key)
        if isinstance(value, str) and value and not _is_workspace_path(value, workspace_root):
            filtered.pop(key)
            _ignored(diagnostics, "PROJECT_PATH_ESCAPE_IGNORED", key)

    extensions = filtered.get("extensions")
    if isinstance(extensions, dict):
        plugin_dirs = extensions.get("plugin_dirs")
        if isinstance(plugin_dirs, list):
            extensions["plugin_dirs"] = _filter_project_path_list(
                plugin_dirs,
                workspace_root,
                diagnostics,
                "extensions.plugin_dirs",
            )
    flat_plugin_dirs = filtered.get("plugin_dirs")
    if isinstance(flat_plugin_dirs, list):
        filtered["plugin_dirs"] = _filter_project_path_list(
            flat_plugin_dirs,
            workspace_root,
            diagnostics,
            "plugin_dirs",
        )


def _filter_project_path_list(
    values: list[object],
    workspace_root: Path,
    diagnostics: list[PolicyDiagnostic],
    field: str,
) -> list[object]:
    kept: list[object] = []
    for value in values:
        if isinstance(value, str) and _is_workspace_path(value, workspace_root):
            kept.append(value)
        else:
            _ignored(diagnostics, "PROJECT_PATH_ESCAPE_IGNORED", field)
    return kept


def _is_workspace_path(value: str, workspace_root: Path) -> bool:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = workspace_root / path
    return path.resolve(strict=False).is_relative_to(workspace_root)
