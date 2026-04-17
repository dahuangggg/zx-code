from __future__ import annotations

import asyncio
import shlex
import shutil
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from agent.tools.base import Tool


class GrepInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pattern: str
    path: str = "."
    glob: str | None = None
    case_sensitive: bool = True
    max_results: int = Field(default=50, ge=1, le=200)


class GrepTool(Tool):
    name = "grep"
    description = "Search files with ripgrep and return matching lines."
    input_model = GrepInput

    async def run(self, arguments: GrepInput) -> dict[str, object]:
        search_root = Path(arguments.path).expanduser().resolve()
        if not search_root.exists():
            raise FileNotFoundError(search_root)

        if shutil.which("rg"):
            command = ["rg", "-n", "--color", "never", "-m", str(arguments.max_results)]
            if not arguments.case_sensitive:
                command.append("-i")
            if arguments.glob:
                command.extend(["-g", arguments.glob])
            command.extend([arguments.pattern, str(search_root)])
        else:
            command = ["grep", "-RIn"]
            if not arguments.case_sensitive:
                command.append("-i")
            command.extend([arguments.pattern, str(search_root)])

        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        output = stdout.decode("utf-8", errors="replace")
        lines = output.splitlines()
        if process.returncode not in (0, 1):
            raise RuntimeError(stderr.decode("utf-8", errors="replace").strip())

        return {
            "command": " ".join(shlex.quote(part) for part in command),
            "matches": lines[: arguments.max_results],
        }

