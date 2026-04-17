from __future__ import annotations

import asyncio
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from agent.tools.base import Tool


class BashInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command: str
    workdir: str | None = None
    timeout_s: float = Field(default=30.0, gt=0)


class BashTool(Tool):
    name = "bash"
    description = "Run a shell command and capture stdout, stderr, and exit code."
    input_model = BashInput

    async def run(self, arguments: BashInput) -> dict[str, object]:
        workdir = str(Path(arguments.workdir).resolve()) if arguments.workdir else None
        process = await asyncio.create_subprocess_shell(
            arguments.command,
            cwd=workdir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=arguments.timeout_s,
            )
        except TimeoutError:
            process.kill()
            await process.wait()
            raise RuntimeError(f"command timed out after {arguments.timeout_s:.1f}s")

        return {
            "command": arguments.command,
            "workdir": workdir or str(Path.cwd()),
            "exit_code": process.returncode,
            "stdout": stdout.decode("utf-8", errors="replace"),
            "stderr": stderr.decode("utf-8", errors="replace"),
        }

