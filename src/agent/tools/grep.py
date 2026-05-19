from __future__ import annotations

import asyncio
import shlex
import shutil
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from agent.models import ToolEnvelope
from agent.tools.base import ErrorCode, Tool


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

    async def run(self, arguments: GrepInput) -> ToolEnvelope:
        search_root = Path(arguments.path).expanduser().resolve()
        if not search_root.exists():
            return self.error(code=ErrorCode.NOT_FOUND, message=f"path not found: {arguments.path}")

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
            *command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        if process.returncode not in (0, 1):
            return self.error(
                code=ErrorCode.EXECUTION_ERROR,
                message=stderr.decode("utf-8", errors="replace").strip(),
            )

        matches = stdout.decode("utf-8", errors="replace").splitlines()[:arguments.max_results]
        truncated = len(matches) == arguments.max_results
        status_text = (
            f"Found {len(matches)} matches for '{arguments.pattern}' (limit reached, use a narrower pattern)."
            if truncated
            else f"Found {len(matches)} match{'es' if len(matches) != 1 else ''} for '{arguments.pattern}'."
            if matches
            else f"No matches for '{arguments.pattern}'."
        )
        return (self.partial if truncated else self.success)(
            data={"command": " ".join(shlex.quote(p) for p in command), "matches": matches, "truncated": truncated},
            text=status_text,
        )

    def is_concurrency_safe(self, arguments: dict[str, object] | BaseModel) -> bool:
        return True
