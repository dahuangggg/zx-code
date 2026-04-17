from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from agent.tools.base import Tool


class ReadFileInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    start_line: int = Field(default=1, ge=1)
    end_line: int | None = Field(default=None, ge=1)


class ReadFileTool(Tool):
    name = "read_file"
    description = "Read a text file, optionally by line range."
    input_model = ReadFileInput

    async def run(self, arguments: ReadFileInput) -> dict[str, object]:
        path = Path(arguments.path).expanduser().resolve()
        if not path.exists():
            raise FileNotFoundError(path)
        if path.is_dir():
            raise IsADirectoryError(path)

        text = path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        end_line = arguments.end_line or len(lines)
        selected = lines[arguments.start_line - 1 : end_line]
        numbered = [
            f"{line_no}: {line}"
            for line_no, line in enumerate(selected, start=arguments.start_line)
        ]
        return {
            "path": str(path),
            "start_line": arguments.start_line,
            "end_line": end_line,
            "content": "\n".join(numbered),
        }

