from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict

from agent.tools.base import Tool


class EditFileInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    old_text: str
    new_text: str
    replace_all: bool = False


class EditFileTool(Tool):
    name = "edit_file"
    description = "Replace text in a file."
    input_model = EditFileInput

    async def run(self, arguments: EditFileInput) -> dict[str, object]:
        raw_path = Path(arguments.path).expanduser()
        if raw_path.is_symlink():
            raise PermissionError(f"refusing to follow symbolic link: {arguments.path}")
        path = raw_path.resolve()
        if not path.exists():
            raise FileNotFoundError(path)
        original = path.read_text(encoding="utf-8", errors="replace")
        matches = original.count(arguments.old_text)
        if matches == 0:
            raise ValueError("old_text was not found")
        if matches > 1 and not arguments.replace_all:
            raise ValueError(
                "old_text matched multiple locations; set replace_all=true to replace all"
            )

        if arguments.replace_all:
            updated = original.replace(arguments.old_text, arguments.new_text)
        else:
            updated = original.replace(arguments.old_text, arguments.new_text, 1)
        path.write_text(updated, encoding="utf-8")
        return {
            "path": str(path),
            "matches": matches,
            "replaced_all": arguments.replace_all,
        }

