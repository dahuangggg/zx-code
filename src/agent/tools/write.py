"""write_file 工具。

创建、覆盖或追加文本文件。这个工具有文件系统副作用，因此默认不声明并发安全；
ToolRegistry 会在执行前做权限检查和读后写冲突保护。
"""
from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict

from agent.models import ToolEnvelope
from agent.tools.base import ErrorCode, Tool


class WriteFileInput(BaseModel):
    """write_file 参数。create_parents 默认开启，便于一次性创建目标目录。"""

    model_config = ConfigDict(extra="forbid")

    path: str
    content: str
    append: bool = False
    create_parents: bool = True


class WriteFileTool(Tool):
    """写文件工具；data 返回落盘路径、字节数和是否追加。"""

    name = "write_file"
    description = "Write or append text content to a file."
    input_model = WriteFileInput

    async def run(self, arguments: WriteFileInput) -> ToolEnvelope:
        raw_path = Path(arguments.path).expanduser()
        if raw_path.is_symlink():
            return self.error(code=ErrorCode.ACCESS_DENIED, message=f"refusing to follow symbolic link: {arguments.path}")
        path = raw_path.resolve()
        if arguments.create_parents:
            path.parent.mkdir(parents=True, exist_ok=True)

        mode = "a" if arguments.append else "w"
        with path.open(mode, encoding="utf-8") as handle:
            handle.write(arguments.content)

        bytes_written = len(arguments.content.encode("utf-8"))
        lines_written = arguments.content.count("\n") + 1
        action = "Appended" if arguments.append else "Wrote"
        return self.success(
            data={"path": str(path), "bytes_written": bytes_written, "append": arguments.append},
            text=f"{action} {lines_written} lines ({bytes_written} bytes) to {arguments.path}.",
        )
