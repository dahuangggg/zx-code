"""CodeContext 数据模型。

这些模型只描述本地代码索引和搜索结果，不属于长期 Memory，也不代表对话历史。
CodeContext 的职责是把源码切块、索引、检索后返回带路径和行号的片段。
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class CodeChunk(BaseModel):
    """写入向量库/关键词索引的最小代码片段。"""

    model_config = ConfigDict(extra="forbid")

    id: str = ""
    codebase_id: str = ""
    codebase_path: str = ""
    relative_path: str
    language: str
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    content: str
    file_hash: str = ""
    chunk_index: int = Field(default=0, ge=0)
    symbol: str = ""


class CodeSearchResult(BaseModel):
    """返回给工具层的检索结果，保留路径、行号、分数和命中来源。"""

    model_config = ConfigDict(extra="forbid")

    relative_path: str
    start_line: int
    end_line: int
    language: str
    score: float
    content: str
    symbol: str = ""
    matched_by: list[str] = Field(default_factory=list)
    vector_rank: int = 0
    keyword_rank: int = 0


class CodeIndexStats(BaseModel):
    """一次索引运行结束后的统计摘要。"""

    model_config = ConfigDict(extra="forbid")

    codebase_id: str
    codebase_path: str
    status: str = "indexed"
    indexed_files: int = 0
    total_chunks: int = 0
    added_files: int = 0
    modified_files: int = 0
    removed_files: int = 0
    skipped_files: int = 0


class CodeIndexStatus(BaseModel):
    """索引状态文件的对外表示，供轮询和 UI 展示。"""

    model_config = ConfigDict(extra="forbid")

    codebase_id: str
    codebase_path: str
    status: str
    indexed_files: int = 0
    total_chunks: int = 0
    indexed_at: str = ""
    percentage: int = 0
    current: int = 0
    total: int = 0
    phase: str = ""
    error: str = ""


class CodeClearResult(BaseModel):
    """清理索引后的结果。"""

    model_config = ConfigDict(extra="forbid")

    codebase_id: str
    codebase_path: str
    deleted_chunks: int = 0
