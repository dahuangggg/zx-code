from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from agent.models import ToolResult
from agent.tools.base import Tool


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def schemas(self) -> list[dict[str, Any]]:
        return [tool.schema() for tool in self._tools.values()]

    async def execute(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        call_id: str,
    ) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(
                call_id=call_id,
                name=name,
                content=f"unknown tool: {name}",
                is_error=True,
            )

        try:
            return await tool.execute(arguments, call_id=call_id)
        except ValidationError as exc:
            return ToolResult(
                call_id=call_id,
                name=name,
                content=f"invalid arguments for {name}: {exc.errors(include_url=False)}",
                is_error=True,
            )
        except Exception as exc:
            return ToolResult(
                call_id=call_id,
                name=name,
                content=f"{name} failed: {exc}",
                is_error=True,
            )

