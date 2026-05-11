"""permissions — 多层规则引擎权限系统（s07）。

``PermissionManager.decide()`` 按优先级依次评估三层规则：

  1. 每工具策略（``tool_policies`` 配置项，优先级最高）
  2. 文件规则（``.zx-code/permissions.toml`` 的 [[rules]] 数组，fnmatch 匹配）
     - 规则类型：deny → allow → ask（deny 最先检查）
  3. 内置安全模式（兜底，无需配置）
     - bash：正则匹配危险命令（rm -rf、sudo、dd 等）
     - write_file/edit_file：符号链接检查、敏感路径检查、工作目录越界检查
     - memory_append：默认需审批

结果为三态：
  allow — 直接执行
  deny  — 返回错误 ToolResult
  ask   — 调用 ApprovalCallback（CLI 模式下弹出 y/N 提示）
"""

from __future__ import annotations


import fnmatch
import re
import tomllib
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


PermissionDecision = Literal["allow", "deny", "ask"]
ApprovalCallback = Callable[["PermissionCheck"], bool | Awaitable[bool]]


class PermissionCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    decision: PermissionDecision
    reason: str


class PermissionRule(BaseModel):
    """A single fnmatch-based permission rule loaded from a rules file."""

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
    if hasattr(value, "__await__"):
        return bool(await value)
    return bool(value)


class PermissionManager:
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
    ) -> None:
        self.tool_policies = tool_policies or {}
        self.default_decision = default_decision
        self.working_dir = Path(working_dir).resolve() if working_dir else None
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
    ) -> "PermissionManager":
        """Load rules from a TOML file with a [[rules]] array."""
        rules_path = Path(path)
        rules: list[PermissionRule] = []
        if rules_path.exists():
            with rules_path.open("rb") as fh:
                data = tomllib.load(fh)
            for entry in data.get("rules", []):
                rules.append(PermissionRule.model_validate(entry))
        return cls(
            tool_policies=tool_policies,
            rules=rules,
            default_decision=default_decision,
            working_dir=working_dir,
        )

    def decide(self, tool_name: str, arguments: dict[str, Any]) -> PermissionCheck:
        # 1. Per-tool policy overrides (from config permission_tools table)
        configured = self.tool_policies.get(tool_name)
        if configured:
            return PermissionCheck(
                tool_name=tool_name,
                arguments=arguments,
                decision=configured,
                reason=f"configured policy for {tool_name}: {configured}",
            )

        # 2. File-based rules: deny → allow → ask
        for rule in self._deny_rules:
            if rule.matches(tool_name, arguments):
                return PermissionCheck(
                    tool_name=tool_name,
                    arguments=arguments,
                    decision="deny",
                    reason=f"matched deny rule: tool={rule.tool!r} path={rule.path!r} command={rule.command!r}",
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

        # 3. Built-in safety patterns (fallback when no file rules match)
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

        if tool_name in {"write_file", "edit_file"}:
            file_path = str(arguments.get("path", ""))
            if file_path:
                resolved = Path(file_path).expanduser().resolve()
                if resolved.is_symlink():
                    return PermissionCheck(
                        tool_name=tool_name,
                        arguments=arguments,
                        decision="ask",
                        reason=f"path is a symbolic link: {file_path}",
                    )
                for pattern in self.sensitive_paths:
                    if pattern.search(str(resolved)):
                        return PermissionCheck(
                            tool_name=tool_name,
                            arguments=arguments,
                            decision="ask",
                            reason=f"path looks sensitive: {resolved}",
                        )
                if self.working_dir and not str(resolved).startswith(str(self.working_dir)):
                    return PermissionCheck(
                        tool_name=tool_name,
                        arguments=arguments,
                        decision="ask",
                        reason=f"path is outside working directory: {resolved}",
                    )

        if tool_name in {"memory_append", "code_index_clear"}:
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
