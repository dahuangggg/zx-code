"""CodeContext 工具组。

这些工具把本地代码索引能力暴露给模型：建索引、查语义结果、看索引状态和清理索引。
它们只操作本地索引数据，不直接修改源码文件。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agent.code_context.indexer import CodeContextIndexer
from agent.models import ToolEnvelope
from agent.tools.base import Tool


class CodeIndexInput(BaseModel):
    """code_index 参数；background=True 时只启动任务并立即返回状态。"""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(default="", description="Codebase path to index. Defaults to cwd.")
    background: bool = Field(default=False, description="Start indexing in the background and return progress status immediately.")
    custom_extensions: list[str] = Field(default_factory=list, description="Additional file extensions to include, for example .vue.")
    ignore_patterns: list[str] = Field(default_factory=list, description="Additional ignore patterns to exclude from indexing.")


class CodeSearchInput(BaseModel):
    """code_search 参数；query 是自然语言检索词，top_k 控制返回片段数。"""

    model_config = ConfigDict(extra="forbid")

    query: str
    path: str = Field(default="", description="Codebase path to search. Defaults to cwd.")
    top_k: int = Field(default=5, ge=1, le=20)


class CodeIndexStatusInput(BaseModel):
    """code_index_status 参数。空 path 表示当前工作目录。"""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(default="", description="Codebase path to inspect. Defaults to cwd.")


class CodeIndexListInput(BaseModel):
    """code_index_list 无参数输入模型。"""

    model_config = ConfigDict(extra="forbid")


class CodeIndexInfoInput(BaseModel):
    """code_index_info 参数。"""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(default="", description="Codebase path to inspect. Defaults to cwd.")


class CodeIndexClearInput(BaseModel):
    """code_index_clear 参数；清理的是索引，不删除源码。"""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(default="", description="Codebase path whose local index should be cleared.")


class CodeIndexTool(Tool):
    """构建或刷新 CodeContext 索引的工具。"""

    name = "code_index"
    description = "Index a codebase into the local semantic CodeContext store, optionally in the background."
    input_model = CodeIndexInput

    def __init__(self, indexer: CodeContextIndexer) -> None:
        self.indexer = indexer

    async def run(self, arguments: CodeIndexInput) -> ToolEnvelope:
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
    """在已索引代码库中做语义搜索；结果放在 data.results。"""

    name = "code_search"
    description = (
        "Search an indexed codebase semantically. Use for unfamiliar architecture, "
        "natural-language code locations, and broad codebase understanding."
    )
    input_model = CodeSearchInput

    def __init__(self, indexer: CodeContextIndexer) -> None:
        self.indexer = indexer

    async def run(self, arguments: CodeSearchInput) -> ToolEnvelope:
        path = _path_arg(arguments.path)
        results = self.indexer.search_code(path, arguments.query, top_k=arguments.top_k)
        status = self.indexer.get_status(path)
        return self.success(
            data={"query": arguments.query, "codebase_path": status.codebase_path, "results": [r.model_dump() for r in results]},
            text=f"Found {len(results)} result{'s' if len(results) != 1 else ''} for '{arguments.query}'.",
        )

    def is_concurrency_safe(self, arguments: dict[str, object] | BaseModel) -> bool:
        """搜索只读索引状态，可以并发。"""
        return True


class CodeIndexStatusTool(Tool):
    """查询索引任务状态，适合 background index 之后轮询。"""

    name = "code_index_status"
    description = "Return local CodeContext indexing status for a codebase."
    input_model = CodeIndexStatusInput

    def __init__(self, indexer: CodeContextIndexer) -> None:
        self.indexer = indexer

    async def run(self, arguments: CodeIndexStatusInput) -> ToolEnvelope:
        data = self.indexer.get_status(_path_arg(arguments.path)).model_dump()
        return self.success(data=data, text=f"Index status for '{arguments.path or '.'}': {data.get('status', '?')}.")

    def is_concurrency_safe(self, arguments: dict[str, object] | BaseModel) -> bool:
        """状态查询只读，可以并发。"""
        return True


class CodeIndexListTool(Tool):
    """列出当前本地存储已知的代码库索引。"""

    name = "code_index_list"
    description = "List codebases known to the local CodeContext index."
    input_model = CodeIndexListInput

    def __init__(self, indexer: CodeContextIndexer) -> None:
        self.indexer = indexer

    async def run(self, arguments: CodeIndexListInput) -> ToolEnvelope:
        codebases = self.indexer.list_indexes()
        return self.success(data={"codebases": codebases}, text=f"Found {len(codebases)} indexed codebase{'s' if len(codebases) != 1 else ''}.")

    def is_concurrency_safe(self, arguments: dict[str, object] | BaseModel) -> bool:
        """索引列表查询只读，可以并发。"""
        return True


class CodeIndexInfoTool(Tool):
    """返回某个代码库索引的详细元数据。"""

    name = "code_index_info"
    description = "Return detailed local CodeContext index metadata for a codebase."
    input_model = CodeIndexInfoInput

    def __init__(self, indexer: CodeContextIndexer) -> None:
        self.indexer = indexer

    async def run(self, arguments: CodeIndexInfoInput) -> ToolEnvelope:
        data = self.indexer.get_index_info(_path_arg(arguments.path))
        return self.success(data=data, text=f"Index info for '{arguments.path or '.'}'.")

    def is_concurrency_safe(self, arguments: dict[str, object] | BaseModel) -> bool:
        """索引详情查询只读，可以并发。"""
        return True


class CodeIndexClearTool(Tool):
    """清理本地索引数据；不会删除或修改源码文件。"""

    name = "code_index_clear"
    description = "Clear local CodeContext index data for a codebase. Does not modify source files."
    input_model = CodeIndexClearInput

    def __init__(self, indexer: CodeContextIndexer) -> None:
        self.indexer = indexer

    async def run(self, arguments: CodeIndexClearInput) -> ToolEnvelope:
        data = self.indexer.clear_index(_path_arg(arguments.path)).model_dump()
        return self.success(data=data, text=f"Cleared index for '{arguments.path or '.'}'.")


def _path_arg(value: str) -> str | None:
    """把空字符串 path 规范化为 None，让 indexer 使用 cwd 默认值。"""
    return value if value.strip() else None
