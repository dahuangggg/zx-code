"""tool_search 工具。

当工具 schema 没有全部暴露给模型时，tool_search 负责按名称或自然语言查询激活
后续轮次需要的工具。它是工具发现入口，因此自身始终保持可见。
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agent.models import ToolEnvelope
from agent.tools.base import Tool


TOOL_DISPLAY_NAMES = {
    "read_file": "Read",
    "write_file": "Write",
    "edit_file": "Edit",
    "grep": "Grep",
    "glob": "Glob",
}


class ToolSearchInput(BaseModel):
    """tool_search 参数；names 精确激活，query 做简单文本匹配。"""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(
        ...,
        description="Natural language description of what you need, e.g. 'read a file', 'run shell command', 'search code'.",
    )
    names: list[str] = Field(
        default_factory=list,
        description="Exact tool names or display names to activate, if known.",
    )
    limit: int = Field(default=5, ge=1, le=20)


class ToolSearchTool(Tool):
    """搜索可用工具 schema，并在 registry 中激活匹配项。"""

    name = "tool_search"
    description = "Search available tools and activate matching tool schemas for the next model turn."
    input_model = ToolSearchInput

    def __init__(self, registry: Any) -> None:
        self.registry = registry

    async def run(self, arguments: ToolSearchInput) -> ToolEnvelope:
        schemas = self.registry.schemas()
        matches = self._find_matches(schemas, arguments)
        activated = [self._tool_name(schema) for schema in matches]
        self.registry.activate_schemas(activated)
        return self.success(
            data={
                "activated": activated,
                "tools": [self._compact_tool(schema) for schema in matches],
            },
            text=f"Activated {len(activated)} tool(s): {', '.join(activated)}." if activated else "No matching tools found.",
        )

    def _find_matches(
        self,
        schemas: list[dict[str, Any]],
        arguments: ToolSearchInput,
    ) -> list[dict[str, Any]]:
        """按 exact names 优先，再用 query term 对 schema 文本打分。"""
        by_name = {
            self._tool_name(schema): schema
            for schema in schemas
            if self._tool_name(schema) and self._tool_name(schema) != self.name
        }
        by_alias = {
            display_name.casefold(): name
            for name, display_name in TOOL_DISPLAY_NAMES.items()
            if name in by_name
        }

        ordered: list[dict[str, Any]] = []
        for name in arguments.names:
            resolved_name = by_alias.get(name.casefold(), name)
            schema = by_name.get(resolved_name)
            if schema is not None and schema not in ordered:
                ordered.append(schema)

        query_terms = [
            term.casefold()
            for term in arguments.query.replace("_", " ").split()
            if term.strip()
        ]
        if query_terms:
            scored: list[tuple[int, str, dict[str, Any]]] = []
            for name, schema in by_name.items():
                if schema in ordered:
                    continue
                haystack = self._search_text(schema)
                score = sum(1 for term in query_terms if term in haystack)
                if score:
                    scored.append((score, name, schema))
            scored.sort(key=lambda item: (-item[0], item[1]))
            if scored:
                best_score = scored[0][0]
                min_score = max(1, best_score - 1)
                ordered.extend(
                    schema for score, _, schema in scored if score >= min_score
                )

        return ordered[: arguments.limit]

    def _search_text(self, schema: dict[str, Any]) -> str:
        """把工具名、描述和参数名拼成可搜索文本。"""
        function = schema.get("function", {})
        parameters = function.get("parameters", {})
        properties = parameters.get("properties", {}) if isinstance(parameters, dict) else {}
        arg_names = " ".join(properties) if isinstance(properties, dict) else ""
        return " ".join(
            [
                str(function.get("name", "")),
                TOOL_DISPLAY_NAMES.get(str(function.get("name", "")), ""),
                str(function.get("description", "")),
                arg_names,
            ]
        ).casefold()

    def _compact_tool(self, schema: dict[str, Any]) -> dict[str, Any]:
        """返回给模型的简化 schema，保留名称、描述和参数。"""
        function = schema.get("function", {})
        return {
            "name": self._tool_name(schema),
            "description": function.get("description", ""),
            "parameters": function.get("parameters", {}),
        }

    def _tool_name(self, schema: dict[str, Any]) -> str:
        """从 OpenAI function tool schema 中提取工具名。"""
        function = schema.get("function", {})
        return str(function.get("name", "")).strip()
