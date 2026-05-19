"""edit_file 工具。

基于精确 old_text/new_text 做文本替换，并在 data.diff 返回 unified diff。
它有写副作用，执行前由 ToolRegistry 负责权限和读后写冲突检查。
"""
from __future__ import annotations

import difflib
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from agent.models import ToolEnvelope
from agent.tools.base import ErrorCode, Tool


class EditFileInput(BaseModel):
    """edit_file 参数；默认只允许 old_text 命中一次，避免误改多处。"""

    model_config = ConfigDict(extra="forbid")

    path: str
    old_text: str
    new_text: str
    replace_all: bool = False


class EditFileTool(Tool):
    """精确文本替换工具；多处命中时要求调用者显式 replace_all。"""

    name = "edit_file"
    description = "Replace text in a file."
    input_model = EditFileInput

    async def run(self, arguments: EditFileInput) -> ToolEnvelope:
        raw_path = Path(arguments.path).expanduser()
        if raw_path.is_symlink():
            return self.error(code=ErrorCode.ACCESS_DENIED, message=f"refusing to follow symbolic link: {arguments.path}")
        path = raw_path.resolve()
        if not path.exists():
            return self.error(code=ErrorCode.NOT_FOUND, message=f"file not found: {arguments.path}")

        original = path.read_text(encoding="utf-8", errors="replace")
        matches = original.count(arguments.old_text)
        if matches == 0:
            return self.error(code=ErrorCode.INVALID_PARAM, message="old_text was not found in file.")
        if matches > 1 and not arguments.replace_all:
            return self.error(
                code=ErrorCode.INVALID_PARAM,
                message=f"old_text matched {matches} locations; set replace_all=true to replace all.",
            )

        updated = (
            original.replace(arguments.old_text, arguments.new_text)
            if arguments.replace_all
            else original.replace(arguments.old_text, arguments.new_text, 1)
        )
        path.write_text(updated, encoding="utf-8")

        diff = "".join(
            difflib.unified_diff(
                original.splitlines(keepends=True),
                updated.splitlines(keepends=True),
                fromfile=f"a/{path.name}",
                tofile=f"b/{path.name}",
            )
        )
        replaced = matches if arguments.replace_all else 1
        return self.success(
            data={"path": str(path), "matches": matches, "replaced": replaced, "diff": diff},
            text=f"Edited {arguments.path}: replaced {replaced} occurrence{'s' if replaced != 1 else ''} of old_text.",
        )
