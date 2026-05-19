"""read_file 工具。

读取文本文件并返回带行号的片段。data.content 给模型和后续工具看具体内容，
extra_context 记录 mtime/size/path，供 ToolRegistry 的写入冲突保护使用。
"""
from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agent.models import ToolEnvelope
from agent.tools.base import ErrorCode, Tool


class ReadFileInput(BaseModel):
    """read_file 参数，兼容 path/file_path 与 offset/limit 两组别名。"""

    model_config = ConfigDict(extra="forbid")

    path: str = ""
    file_path: str = ""
    start_line: int = Field(default=1, ge=1)
    end_line: int | None = Field(default=None, ge=1)
    offset: int | None = Field(default=None, ge=0)
    limit: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def normalize_documented_aliases(self) -> "ReadFileInput":
        """把文档化别名规范化为内部使用的 path/start_line/end_line。"""
        if not self.path and self.file_path:
            self.path = self.file_path
        if not self.path:
            raise ValueError("path or file_path is required")
        if self.offset is not None:
            self.start_line = self.offset + 1
        if self.limit is not None:
            self.end_line = self.start_line + self.limit - 1
        return self


class ReadFileTool(Tool):
    """只读文件工具；成功读取会把文件元数据写入 envelope.context。"""

    name = "read_file"
    description = "Read a text file, optionally by line range."
    input_model = ReadFileInput

    async def run(self, arguments: ReadFileInput) -> ToolEnvelope:
        raw_path = Path(arguments.path).expanduser()
        if raw_path.is_symlink():
            return self.error(code=ErrorCode.ACCESS_DENIED, message=f"refusing to follow symbolic link: {arguments.path}")
        path = raw_path.resolve()
        if not path.exists():
            return self.error(code=ErrorCode.NOT_FOUND, message=f"file not found: {arguments.path}")
        if path.is_dir():
            return self.error(code=ErrorCode.IS_DIRECTORY, message=f"path is a directory: {arguments.path}")

        text = path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        end_line = arguments.end_line or len(lines)
        selected = lines[arguments.start_line - 1 : end_line]
        numbered = [f"{n}: {line}" for n, line in enumerate(selected, start=arguments.start_line)]
        stat = path.stat()

        return self.success(
            data={"path": str(path), "content": "\n".join(numbered), "start_line": arguments.start_line, "end_line": end_line},
            text=f"Read {arguments.path} lines {arguments.start_line}-{end_line} ({len(selected)} lines). File has {len(lines)} lines total.",
            extra_context={
                "file_mtime_ms": int(stat.st_mtime * 1000),
                "file_size_bytes": stat.st_size,
                "path_resolved": str(path),
            },
        )

    def is_concurrency_safe(self, arguments: dict[str, object] | BaseModel) -> bool:
        """纯读取没有副作用，可以和其他只读工具并发。"""
        return True
