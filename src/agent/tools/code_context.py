from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agent.code_context.indexer import CodeContextIndexer
from agent.tools.base import Tool


class CodeIndexInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(default="", description="Codebase path to index. Defaults to cwd.")
    background: bool = Field(default=False, description="Start indexing in the background and return progress status immediately.")


class CodeSearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    path: str = Field(default="", description="Codebase path to search. Defaults to cwd.")
    top_k: int = Field(default=5, ge=1, le=20)


class CodeIndexStatusInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(default="", description="Codebase path to inspect. Defaults to cwd.")


class CodeIndexClearInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(default="", description="Codebase path whose local index should be cleared.")


class CodeIndexTool(Tool):
    name = "code_index"
    description = "Index a codebase into the local semantic CodeContext store, optionally in the background."
    input_model = CodeIndexInput

    def __init__(self, indexer: CodeContextIndexer) -> None:
        self.indexer = indexer

    async def run(self, arguments: CodeIndexInput) -> dict[str, Any]:
        if arguments.background:
            return self.indexer.start_background_index(_path_arg(arguments.path)).model_dump()
        return self.indexer.index_codebase(_path_arg(arguments.path)).model_dump()


class CodeSearchTool(Tool):
    name = "code_search"
    description = (
        "Search an indexed codebase semantically. Use for unfamiliar architecture, "
        "natural-language code locations, and broad codebase understanding."
    )
    input_model = CodeSearchInput

    def __init__(self, indexer: CodeContextIndexer) -> None:
        self.indexer = indexer

    async def run(self, arguments: CodeSearchInput) -> dict[str, Any]:
        path = _path_arg(arguments.path)
        results = self.indexer.search_code(path, arguments.query, top_k=arguments.top_k)
        status = self.indexer.get_status(path)
        return {
            "query": arguments.query,
            "codebase_path": status.codebase_path,
            "results": [result.model_dump() for result in results],
        }

    def is_concurrency_safe(self, arguments: dict[str, object] | BaseModel) -> bool:
        return True


class CodeIndexStatusTool(Tool):
    name = "code_index_status"
    description = "Return local CodeContext indexing status for a codebase."
    input_model = CodeIndexStatusInput

    def __init__(self, indexer: CodeContextIndexer) -> None:
        self.indexer = indexer

    async def run(self, arguments: CodeIndexStatusInput) -> dict[str, Any]:
        return self.indexer.get_status(_path_arg(arguments.path)).model_dump()

    def is_concurrency_safe(self, arguments: dict[str, object] | BaseModel) -> bool:
        return True


class CodeIndexClearTool(Tool):
    name = "code_index_clear"
    description = "Clear local CodeContext index data for a codebase. Does not modify source files."
    input_model = CodeIndexClearInput

    def __init__(self, indexer: CodeContextIndexer) -> None:
        self.indexer = indexer

    async def run(self, arguments: CodeIndexClearInput) -> dict[str, Any]:
        return self.indexer.clear_index(_path_arg(arguments.path)).model_dump()


def _path_arg(value: str) -> str | None:
    return value if value.strip() else None
