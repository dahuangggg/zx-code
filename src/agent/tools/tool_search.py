from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agent.tools.base import Tool


class ToolSearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = ""
    names: list[str] = Field(default_factory=list)
    limit: int = Field(default=5, ge=1, le=20)


class ToolSearchTool(Tool):
    name = "tool_search"
    description = "Search available tools and activate matching tool schemas for the next model turn."
    input_model = ToolSearchInput

    def __init__(self, registry: Any) -> None:
        self.registry = registry

    async def run(self, arguments: ToolSearchInput) -> dict[str, Any]:
        schemas = self.registry.schemas()
        matches = self._find_matches(schemas, arguments)
        activated = [self._tool_name(schema) for schema in matches]
        self.registry.activate_schemas(activated)
        return {
            "activated": activated,
            "tools": [self._compact_tool(schema) for schema in matches],
        }

    def _find_matches(
        self,
        schemas: list[dict[str, Any]],
        arguments: ToolSearchInput,
    ) -> list[dict[str, Any]]:
        by_name = {
            self._tool_name(schema): schema
            for schema in schemas
            if self._tool_name(schema) and self._tool_name(schema) != self.name
        }

        ordered: list[dict[str, Any]] = []
        for name in arguments.names:
            schema = by_name.get(name)
            if schema is not None:
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
        function = schema.get("function", {})
        parameters = function.get("parameters", {})
        properties = parameters.get("properties", {}) if isinstance(parameters, dict) else {}
        arg_names = " ".join(properties) if isinstance(properties, dict) else ""
        return " ".join(
            [
                str(function.get("name", "")),
                str(function.get("description", "")),
                arg_names,
            ]
        ).casefold()

    def _compact_tool(self, schema: dict[str, Any]) -> dict[str, Any]:
        function = schema.get("function", {})
        return {
            "name": self._tool_name(schema),
            "description": function.get("description", ""),
            "parameters": function.get("parameters", {}),
        }

    def _tool_name(self, schema: dict[str, Any]) -> str:
        function = schema.get("function", {})
        return str(function.get("name", "")).strip()
