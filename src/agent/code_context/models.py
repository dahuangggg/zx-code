from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class CodeChunk(BaseModel):
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
    model_config = ConfigDict(extra="forbid")

    relative_path: str
    start_line: int
    end_line: int
    language: str
    score: float
    content: str


class CodeIndexStats(BaseModel):
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
    model_config = ConfigDict(extra="forbid")

    codebase_id: str
    codebase_path: str
    deleted_chunks: int = 0
