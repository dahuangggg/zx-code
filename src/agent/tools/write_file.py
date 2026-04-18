from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict

from agent.tools.base import Tool


class WriteFileInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    content: str
    append: bool = False
    create_parents: bool = True


class WriteFileTool(Tool):
    name = "write_file"
    description = "Write or append text content to a file."
    input_model = WriteFileInput

    async def run(self, arguments: WriteFileInput) -> dict[str, object]:
        raw_path = Path(arguments.path).expanduser()
        if raw_path.is_symlink():
            raise PermissionError(f"refusing to follow symbolic link: {arguments.path}")
        path = raw_path.resolve()
        if arguments.create_parents:
            path.parent.mkdir(parents=True, exist_ok=True)

        mode = "a" if arguments.append else "w"
        with path.open(mode, encoding="utf-8") as handle:
            handle.write(arguments.content)

        return {
            "path": str(path),
            "bytes_written": len(arguments.content.encode("utf-8")),
            "append": arguments.append,
        }

