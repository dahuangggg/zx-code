"""glob 工具。

按 glob 模式列出文件路径，适合先发现候选文件，再用 read_file 精读。
"""
from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from agent.models import ToolEnvelope
from agent.tools.base import ErrorCode, Tool


class GlobInput(BaseModel):
    """glob 参数；pattern 使用 pathlib glob 语法，默认从当前目录开始。"""

    model_config = ConfigDict(extra="forbid")

    pattern: str = Field(..., description="Glob pattern, e.g. '**/*.py' or 'src/**/*.py'.")
    path: str = "."
    max_results: int = Field(default=100, ge=1, le=500)


class GlobTool(Tool):
    """只读文件发现工具；返回匹配路径列表。"""

    name = "glob"
    description = "Find files by glob pattern."
    input_model = GlobInput

    async def run(self, arguments: GlobInput) -> ToolEnvelope:
        root = Path(arguments.path).expanduser().resolve()
        if not root.exists():
            return self.error(code=ErrorCode.NOT_FOUND, message=f"path not found: {arguments.path}")
        if not root.is_dir():
            return self.error(code=ErrorCode.INVALID_PARAM, message=f"path is not a directory: {arguments.path}")

        matches = [
            str(path)
            for path in sorted(root.glob(arguments.pattern))
            if path.is_file()
        ]
        limited = matches[: arguments.max_results]
        truncated = len(matches) > len(limited)
        text = (
            f"Found {len(limited)} files matching '{arguments.pattern}' (limit reached)."
            if truncated
            else f"Found {len(limited)} file{'s' if len(limited) != 1 else ''} matching '{arguments.pattern}'."
        )
        return (self.partial if truncated else self.success)(
            data={
                "path": str(root),
                "pattern": arguments.pattern,
                "matches": limited,
                "truncated": truncated,
                "total_matches": len(matches),
            },
            text=text,
        )

    def is_concurrency_safe(self, arguments: dict[str, object] | BaseModel) -> bool:
        """文件发现是只读操作，可以与其他只读工具并发。"""
        return True
