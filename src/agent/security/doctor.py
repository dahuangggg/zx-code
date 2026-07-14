"""`agent doctor` 的纯数据报告与稳定渲染。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent.config import LoadedConfig
from agent.security.policy import CAPABILITIES, EffectivePolicy


def build_doctor_report(
    loaded: LoadedConfig,
    policy: EffectivePolicy,
    *,
    workspace_root: Path,
) -> dict[str, Any]:
    """生成不包含 secret 值的 workspace trust 报告。"""
    settings = policy.settings
    profiles = settings.resolved_model_profiles()
    return {
        "workspace": {
            "root": str(workspace_root.expanduser().resolve()),
            "trust": policy.trust,
        },
        "sources": [
            {
                "source": document.source,
                "path": str(document.path) if document.path is not None else None,
            }
            for document in loaded.documents
        ],
        "capabilities": {
            capability: {
                "state": policy.decide(capability).state,
                "sources": list(policy.decide(capability).sources),
            }
            for capability in CAPABILITIES
        },
        "roots": {
            "read": [str(path) for path in policy.read_roots],
            "write": [str(path) for path in policy.write_roots],
        },
        "models": [
            {
                "name": profile.name,
                "model": profile.model,
                "base_url": profile.extra_kwargs.get("base_url", "<provider-default>"),
                "api_key_env": profile.api_key_env or "<provider-default>",
            }
            for profile in profiles
        ],
        "extensions": {
            "hook": {
                "path": settings.extensions.hooks_path or None,
                "state": policy.decide("hook").state,
            },
            "plugin_dirs": [
                {"path": path, "state": policy.decide("plugin").state}
                for path in settings.extensions.plugin_dirs
            ],
            "mcp_servers": [
                {
                    "name": server.name,
                    "command": server.command,
                    "args": list(server.args),
                    "env_keys": sorted(server.env),
                    "state": policy.decide("mcp").state,
                }
                for server in settings.extensions.mcp_servers
            ],
        },
        "diagnostics": [
            {
                "code": diagnostic.code,
                "message": diagnostic.message,
                "source": diagnostic.source,
            }
            for diagnostic in policy.diagnostics
        ],
        "limitations": [
            "Capability checks are application policy, not an OS sandbox.",
            "Shell commands may have side effects that static policy cannot infer.",
        ],
    }


def render_doctor_report(report: dict[str, Any], *, json_output: bool) -> str:
    if json_output:
        return json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)

    capabilities = report["capabilities"]
    roots = report["roots"]
    lines = [
        f"Workspace: {report['workspace']['root']}",
        f"Workspace trust: {report['workspace']['trust']}",
        "",
        "Config sources:",
    ]
    sources = report["sources"]
    lines.extend(
        f"  - {source['source']}: {source['path'] or '<command line>'}"
        for source in sources
    )
    if not sources:
        lines.append("  - <defaults only>")
    lines.extend(["", "Capabilities:"])
    lines.extend(
        f"  - {name}: {capabilities[name]['state']} "
        f"({', '.join(capabilities[name]['sources'])})"
        for name in CAPABILITIES
    )
    lines.extend(["", "Read roots:"])
    lines.extend(f"  - {path}" for path in roots["read"])
    lines.extend(["Write roots:"])
    lines.extend(f"  - {path}" for path in roots["write"])
    lines.extend(["", "Model endpoints:"])
    lines.extend(
        f"  - {profile['name']} ({profile['model']}): {profile['base_url']} "
        f"[key env: {profile['api_key_env']}]"
        for profile in report["models"]
    )
    extensions = report["extensions"]
    lines.extend(["", "External commands:"])
    hook = extensions["hook"]
    if hook["path"]:
        lines.append(f"  - hook [{hook['state']}]: {hook['path']}")
    lines.extend(
        f"  - plugin [{item['state']}]: {item['path']}"
        for item in extensions["plugin_dirs"]
    )
    lines.extend(
        f"  - mcp:{item['name']} [{item['state']}]: "
        f"{item['command']} {' '.join(item['args'])}".rstrip()
        for item in extensions["mcp_servers"]
    )
    if not hook["path"] and not extensions["plugin_dirs"] and not extensions["mcp_servers"]:
        lines.append("  - none")
    lines.extend(["", "Diagnostics:"])
    diagnostics = report["diagnostics"]
    lines.extend(
        f"  - {item['code']}: {item['message']}" for item in diagnostics
    )
    if not diagnostics:
        lines.append("  - none")
    lines.extend(["", "Limitations:"])
    lines.extend(f"  - {item}" for item in report["limitations"])
    return "\n".join(lines)
