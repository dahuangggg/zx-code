"""bash 工具。

执行 shell 命令并捕获 stdout/stderr/exit_code。返回 data 保留完整输出，text 只给
模型一个摘要；超大输出会在 ToolCallExecutor 后续经过 ObservationTruncator。
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from agent.models import ToolEnvelope
from agent.tools.base import ErrorCode, Tool


class BashInput(BaseModel):
    """bash 参数。workdir 为空时使用当前进程 cwd，timeout_s 防止长期挂起。"""

    model_config = ConfigDict(extra="forbid")

    command: str
    workdir: str | None = None
    timeout_s: float = Field(default=30.0, gt=0)


class BashTool(Tool):
    """运行 shell 命令的工具；因为有外部副作用，默认串行执行。"""

    name = "bash"
    description = "Run a shell command and capture stdout, stderr, and exit code."
    input_model = BashInput

    async def run(self, arguments: BashInput) -> ToolEnvelope:
        workdir = str(Path(arguments.workdir).resolve()) if arguments.workdir else None
        process = await asyncio.create_subprocess_shell(
            arguments.command,
            cwd=workdir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=arguments.timeout_s,
            )
        except TimeoutError:
            process.kill()
            await process.wait()
            return self.error(
                code=ErrorCode.TIMEOUT,
                message=f"Command timed out after {arguments.timeout_s:.1f}s: {arguments.command}",
            )

        stdout_str = stdout.decode("utf-8", errors="replace")
        stderr_str = stderr.decode("utf-8", errors="replace")
        rc = process.returncode or 0
        output_lines = stdout_str.count("\n") + (1 if stdout_str.strip() else 0)
        summary = f"Exit code {rc}. {output_lines} lines of stdout."
        if stderr_str.strip():
            summary += f" stderr: {stderr_str.splitlines()[0][:120]}"

        return self.success(
            data={
                "command": arguments.command,
                "workdir": workdir or str(Path.cwd()),
                "exit_code": rc,
                "stdout": stdout_str,
                "stderr": stderr_str,
            },
            text=summary,
        )
