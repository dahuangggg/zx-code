from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
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

    def __init__(
        self,
        *,
        tool_policies: dict[str, PermissionDecision] | None = None,
        default_decision: PermissionDecision = "allow",
    ) -> None:
        self.tool_policies = tool_policies or {}
        self.default_decision = default_decision

    def decide(self, tool_name: str, arguments: dict[str, Any]) -> PermissionCheck:
        configured = self.tool_policies.get(tool_name)
        if configured:
            return PermissionCheck(
                tool_name=tool_name,
                arguments=arguments,
                decision=configured,
                reason=f"configured policy for {tool_name}: {configured}",
            )

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

        if tool_name in {"memory_append"}:
            return PermissionCheck(
                tool_name=tool_name,
                arguments=arguments,
                decision="ask",
                reason="memory changes should be approved",
            )

        return PermissionCheck(
            tool_name=tool_name,
            arguments=arguments,
            decision=self.default_decision,
            reason=f"default policy: {self.default_decision}",
        )

