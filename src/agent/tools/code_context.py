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
    custom_extensions: list[str] = Field(default_factory=list, description="Additional file extensions to include, for example .vue.")
    ignore_patterns: list[str] = Field(default_factory=list, description="Additional ignore patterns to exclude from indexing.")


class CodeSearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    path: str = Field(default="", description="Codebase path to search. Defaults to cwd.")
    top_k: int = Field(default=5, ge=1, le=20)


class CodeIndexStatusInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(default="", description="Codebase path to inspect. Defaults to cwd.")


class CodeIndexListInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CodeIndexInfoInput(BaseModel):
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

    async def run(self, arguments: CodeIndexInput) -> str:
        if arguments.background:
            result = self.indexer.start_background_index(
                _path_arg(arguments.path),
                custom_extensions=arguments.custom_extensions,
                ignore_patterns=arguments.ignore_patterns,
            )
            return self.success(data=result.model_dump(), text=f"Started background indexing of '{arguments.path or '.'}'.")
        result = self.indexer.index_codebase(
            _path_arg(arguments.path),
            custom_extensions=arguments.custom_extensions,
            ignore_patterns=arguments.ignore_patterns,
        )
        data = result.model_dump()
        return self.success(data=data, text=f"Indexed '{arguments.path or '.'}': {data.get('file_count', '?')} files.")


class CodeSearchTool(Tool):
    name = "code_search"
    description = (
        "Search an indexed codebase semantically. Use for unfamiliar architecture, "
        "natural-language code locations, and broad codebase understanding."
    )
    input_model = CodeSearchInput

    def __init__(self, indexer: CodeContextIndexer) -> None:
        self.indexer = indexer

    async def run(self, arguments: CodeSearchInput) -> str:
        path = _path_arg(arguments.path)
        results = self.indexer.search_code(path, arguments.query, top_k=arguments.top_k)
        status = self.indexer.get_status(path)
        return self.success(
            data={"query": arguments.query, "codebase_path": status.codebase_path, "results": [r.model_dump() for r in results]},
            text=f"Found {len(results)} result{'s' if len(results) != 1 else ''} for '{arguments.query}'.",
        )

    def is_concurrency_safe(self, arguments: dict[str, object] | BaseModel) -> bool:
        return True


class CodeIndexStatusTool(Tool):
    name = "code_index_status"
    description = "Return local CodeContext indexing status for a codebase."
    input_model = CodeIndexStatusInput

    def __init__(self, indexer: CodeContextIndexer) -> None:
        self.indexer = indexer

    async def run(self, arguments: CodeIndexStatusInput) -> str:
        data = self.indexer.get_status(_path_arg(arguments.path)).model_dump()
        return self.success(data=data, text=f"Index status for '{arguments.path or '.'}': {data.get('status', '?')}.")

    def is_concurrency_safe(self, arguments: dict[str, object] | BaseModel) -> bool:
        return True


class CodeIndexListTool(Tool):
    name = "code_index_list"
    description = "List codebases known to the local CodeContext index."
    input_model = CodeIndexListInput

    def __init__(self, indexer: CodeContextIndexer) -> None:
        self.indexer = indexer

    async def run(self, arguments: CodeIndexListInput) -> str:
        codebases = self.indexer.list_indexes()
        return self.success(data={"codebases": codebases}, text=f"Found {len(codebases)} indexed codebase{'s' if len(codebases) != 1 else ''}.")

    def is_concurrency_safe(self, arguments: dict[str, object] | BaseModel) -> bool:
        return True


class CodeIndexInfoTool(Tool):
    name = "code_index_info"
    description = "Return detailed local CodeContext index metadata for a codebase."
    input_model = CodeIndexInfoInput

    def __init__(self, indexer: CodeContextIndexer) -> None:
        self.indexer = indexer

    async def run(self, arguments: CodeIndexInfoInput) -> str:
        data = self.indexer.get_index_info(_path_arg(arguments.path))
        return self.success(data=data, text=f"Index info for '{arguments.path or '.'}'.")

    def is_concurrency_safe(self, arguments: dict[str, object] | BaseModel) -> bool:
        return True


class CodeIndexClearTool(Tool):
    name = "code_index_clear"
    description = "Clear local CodeContext index data for a codebase. Does not modify source files."
    input_model = CodeIndexClearInput

    def __init__(self, indexer: CodeContextIndexer) -> None:
        self.indexer = indexer

    async def run(self, arguments: CodeIndexClearInput) -> str:
        data = self.indexer.clear_index(_path_arg(arguments.path)).model_dump()
        return self.success(data=data, text=f"Cleared index for '{arguments.path or '.'}'.")


def _path_arg(value: str) -> str | None:
    return value if value.strip() else None
