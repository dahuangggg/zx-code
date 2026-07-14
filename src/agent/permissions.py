"""工具执行的权限策略引擎。

``PermissionManager.decide`` 组合按工具配置的策略、可选文件规则和内置安全检查。
它返回 ``allow``、``deny`` 或 ``ask``，让 registry 执行和 CLI 审批保持分离。
"""

from __future__ import annotations

import fnmatch
import re
import tomllib
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

PermissionDecision = Literal["allow", "deny", "ask"]
ApprovalCallback = Callable[["PermissionCheck"], bool | Awaitable[bool]]

if TYPE_CHECKING:
    from agent.config.sections import CapabilityName
    from agent.security.policy import EffectivePolicy


_TOOL_CAPABILITIES: dict[str, "CapabilityName"] = {
    "bash": "shell",
    "read_file": "read_fs",
    "grep": "read_fs",
    "glob": "read_fs",
    "write_file": "write_fs",
    "edit_file": "write_fs",
    "worktree_cleanup": "vcs_delete",
}


class PermissionCheck(BaseModel):
    """权限判断结果。"""

    model_config = ConfigDict(extra="forbid")

    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    decision: PermissionDecision
    reason: str


class PermissionRule(BaseModel):
    """从规则文件加载的单条 fnmatch 权限规则。"""

    model_config = ConfigDict(extra="forbid")

    tool: str = "*"
    path: str | None = None
    command: str | None = None
    decision: PermissionDecision

    def matches(self, tool_name: str, arguments: dict[str, Any]) -> bool:
        if not fnmatch.fnmatch(tool_name, self.tool):
            return False
        if self.path is not None:
            arg_path = str(arguments.get("path", ""))
            if not fnmatch.fnmatch(arg_path, self.path):
                return False
        if self.command is not None:
            arg_cmd = str(arguments.get("command", ""))
            if not fnmatch.fnmatch(arg_cmd, self.command):
                return False
        return True


async def maybe_await_bool(value: bool | Awaitable[bool]) -> bool:
    """兼容同步和异步审批回调。"""
    if hasattr(value, "__await__"):
        return bool(await value)
    return bool(value)


class PermissionManager:
    """多层权限决策器。"""

    dangerous_bash_patterns = [
        re.compile(pattern, re.IGNORECASE)
        for pattern in (
            r"\brm\s+-[^;&|]*r[^;&|]*f\b",
            r"\bsudo\b",
            r"\bchmod\s+777\b",
            r"\bchown\b",
            r"\bmkfs\b",
            r"\bdd\s+if=",
            r":\(\)\s*\{",
        )
    ]

    sensitive_paths = [
        re.compile(pattern)
        for pattern in (
            r"^/etc/",
            r"^/usr/",
            r"^/bin/",
            r"^/sbin/",
            r"^/var/",
            r"^/sys/",
            r"^/proc/",
            r"^/boot/",
            r"^/dev/",
            r"^/root/",
            r"\.ssh/",
            r"\.env$",
            r"\.env\.",
            r"credentials",
            r"\.pem$",
            r"\.key$",
        )
    ]

    def __init__(
        self,
        *,
        tool_policies: dict[str, PermissionDecision] | None = None,
        rules: list[PermissionRule] | None = None,
        default_decision: PermissionDecision = "allow",
        working_dir: str | Path | None = None,
        policy: "EffectivePolicy | None" = None,
    ) -> None:
        self.tool_policies = tool_policies or {}
        self.default_decision = default_decision
        self.working_dir = Path(working_dir).resolve() if working_dir else None
        self.policy = policy
        _rules = rules or []
        self._deny_rules = [r for r in _rules if r.decision == "deny"]
        self._allow_rules = [r for r in _rules if r.decision == "allow"]
        self._ask_rules = [r for r in _rules if r.decision == "ask"]

    @classmethod
    def from_rules_file(
        cls,
        path: str | Path,
        *,
        tool_policies: dict[str, PermissionDecision] | None = None,
        default_decision: PermissionDecision = "allow",
        working_dir: str | Path | None = None,
        policy: "EffectivePolicy | None" = None,
        include_allow_rules: bool = True,
    ) -> "PermissionManager":
        """从包含 [[rules]] 数组的 TOML 文件加载规则。"""
        rules_path = Path(path)
        rules: list[PermissionRule] = []
        if rules_path.exists():
            with rules_path.open("rb") as fh:
                data = tomllib.load(fh)
            for entry in data.get("rules", []):
                rule = PermissionRule.model_validate(entry)
                if include_allow_rules or rule.decision != "allow":
                    rules.append(rule)
        return cls(
            tool_policies=tool_policies,
            rules=rules,
            default_decision=default_decision,
            working_dir=working_dir,
            policy=policy,
        )

    def decide(self, tool_name: str, arguments: dict[str, Any]) -> PermissionCheck:
        # deny 是单调策略：后续 workspace 护栏或 allow 规则不能把它降级。
        configured = self.tool_policies.get(tool_name)
        if configured == "deny":
            return PermissionCheck(
                tool_name=tool_name,
                arguments=arguments,
                decision=configured,
                reason=f"configured policy for {tool_name}: {configured}",
            )
        for rule in self._deny_rules:
            if rule.matches(tool_name, arguments):
                return PermissionCheck(
                    tool_name=tool_name,
                    arguments=arguments,
                    decision="deny",
                    reason=f"matched deny rule: tool={rule.tool!r} path={rule.path!r} command={rule.command!r}",
                )

        capability_check = self._check_capability(tool_name, arguments)
        if capability_check is not None and capability_check.decision != "allow":
            return capability_check

        # workspace root 是不可被项目级 allow 放宽的内置护栏。
        path_check = self._check_path(tool_name, arguments)
        if path_check is not None:
            return path_check

        # 非 deny 的工具策略，以及 allow/ask 文件规则。
        if configured:
            return PermissionCheck(
                tool_name=tool_name,
                arguments=arguments,
                decision=configured,
                reason=f"configured policy for {tool_name}: {configured}",
            )
        for rule in self._allow_rules:
            if rule.matches(tool_name, arguments):
                return PermissionCheck(
                    tool_name=tool_name,
                    arguments=arguments,
                    decision="allow",
                    reason=f"matched allow rule: tool={rule.tool!r}",
                )
        for rule in self._ask_rules:
            if rule.matches(tool_name, arguments):
                return PermissionCheck(
                    tool_name=tool_name,
                    arguments=arguments,
                    decision="ask",
                    reason=f"matched ask rule: tool={rule.tool!r} path={rule.path!r} command={rule.command!r}",
                )

        # 3. 内置安全模式（没有文件规则命中时兜底）
        if tool_name == "bash":
            command = str(arguments.get("command", ""))
            for pattern in self.dangerous_bash_patterns:
                if pattern.search(command):
                    return PermissionCheck(
                        tool_name=tool_name,
                        arguments=arguments,
                        decision="ask",
                        reason=f"bash command looks dangerous: {pattern.pattern}",
                    )

        if tool_name == "memory_append":
            return PermissionCheck(
                tool_name=tool_name,
                arguments=arguments,
                decision="ask",
                reason=f"{tool_name} changes durable local state and should be approved",
            )

        return PermissionCheck(
            tool_name=tool_name,
            arguments=arguments,
            decision=self.default_decision,
            reason=f"default policy: {self.default_decision}",
        )

    def _check_capability(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> PermissionCheck | None:
        if self.policy is None:
            return None
        capability = _TOOL_CAPABILITIES.get(tool_name)
        if capability is None:
            return None
        decision = self.policy.decide(capability)
        return PermissionCheck(
            tool_name=tool_name,
            arguments=arguments,
            decision=decision.state,
            reason=f"capability {capability}: {decision.state}",
        )

    def _check_path(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> PermissionCheck | None:
        capability = _TOOL_CAPABILITIES.get(tool_name)
        if capability not in {"read_fs", "write_fs"}:
            return None
        file_path = str(arguments.get("path", ""))
        if not file_path:
            return None

        candidate = Path(file_path).expanduser()
        if not candidate.is_absolute():
            candidate = (self.working_dir or Path.cwd()) / candidate
        resolved = candidate.resolve(strict=False)

        effective_roots: tuple[Path, ...] = ()
        if self.policy is not None:
            effective_roots = (
                self.policy.read_roots
                if capability == "read_fs"
                else self.policy.write_roots
            )
        elif self.working_dir is not None:
            effective_roots = (self.working_dir,)

        if effective_roots and not any(
            resolved.is_relative_to(root) for root in effective_roots
        ):
            root_label = (
                f"effective {capability.removesuffix('_fs')} roots"
                if self.policy is not None
                else "working directory"
            )
            return PermissionCheck(
                tool_name=tool_name,
                arguments=arguments,
                decision="ask",
                reason=f"path is outside {root_label}: {resolved}",
            )
        if self.policy is not None and not effective_roots:
            return PermissionCheck(
                tool_name=tool_name,
                arguments=arguments,
                decision="deny",
                reason=f"effective {capability.removesuffix('_fs')} roots are empty",
            )
        if _path_contains_symlink(candidate):
            return PermissionCheck(
                tool_name=tool_name,
                arguments=arguments,
                decision="ask",
                reason=f"path contains a symbolic link: {file_path}",
            )
        for pattern in self.sensitive_paths:
            if pattern.search(str(resolved)):
                return PermissionCheck(
                    tool_name=tool_name,
                    arguments=arguments,
                    decision="ask",
                    reason=f"path looks sensitive: {resolved}",
                )
        return None


def _path_contains_symlink(path: Path) -> bool:
    current = path
    while True:
        if current.is_symlink():
            return True
        if current == current.parent:
            return False
        current = current.parent
