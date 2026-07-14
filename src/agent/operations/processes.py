"""外部 Adapter 的跨工具进程组清理。"""

from __future__ import annotations

import os
import signal
from typing import Any


async def kill_process_group(process: Any) -> None:
    """终止新 session 中的整个进程组，并等待父进程回收。"""
    pid = getattr(process, "pid", None)
    killed_group = False
    if pid is not None:
        try:
            os.killpg(pid, signal.SIGKILL)
            killed_group = True
        except (ProcessLookupError, PermissionError, OSError):
            pass
    if not killed_group:
        try:
            process.kill()
        except ProcessLookupError:
            pass
    try:
        await process.wait()
    except ProcessLookupError:
        pass
