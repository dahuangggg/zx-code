"""工具生命周期事件的子进程 hook runner。

Hook 通过 TOML 配置，并作为外部命令运行。它们通过 stdin/stdout 传递 JSON，
可以拒绝 ``pre_tool_use`` 事件，其余情况下与核心运行时保持进程隔离。
"""

from __future__ import annotations


import asyncio
import json
import logging
import shlex
import tomllib
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class HookResult:
    """单个 hook 链的决策结果。"""

    __slots__ = ("denied", "reason")

    def __init__(self, *, denied: bool = False, reason: str = "") -> None:
        self.denied = denied
        self.reason = reason


class HookRunner:
    """通过子进程和 JSON 协议运行用户定义的 hook。

    Hook 脚本从 stdin 接收 JSON payload，并可向 stdout 写 JSON 响应。对于
    ``pre_tool_use`` 事件，``{"decision": "deny"}`` 会阻止本次动作；其他事件按
    fire-and-forget 处理。

    配置文件格式（.zx-code/hooks.toml）：

        [[pre_tool_use]]
        command = "python .zx-code/hooks/security.py"

        [[post_tool_use]]
        command = "bash .zx-code/hooks/audit.sh"
    """

    def __init__(self, hooks: dict[str, list[dict[str, str]]]) -> None:
        self._hooks = hooks

    @classmethod
    def from_file(cls, path: str | Path) -> "HookRunner":
        """从 TOML 文件加载 hook 配置；文件不存在时返回空 runner。"""
        hooks_path = Path(path)
        if not hooks_path.exists():
            return cls({})
        with hooks_path.open("rb") as fh:
            data = tomllib.load(fh)
        return cls(data)

    @classmethod
    def empty(cls) -> "HookRunner":
        return cls({})

    async def run(self, event: str, payload: dict[str, Any]) -> HookResult:
        """运行指定事件注册的所有 hook。

        如果任意 hook 返回 ``{"decision": "deny"}``，则返回 ``denied=True`` 的
        ``HookResult``；否则返回允许结果。
        """
        for hook in self._hooks.get(event, []):
            command = hook.get("command", "").strip()
            if not command:
                continue
            raw = await self._exec(command, payload)
            if raw and raw.get("decision") == "deny":
                return HookResult(
                    denied=True,
                    reason=str(raw.get("reason", f"hook denied {event}")),
                )
        return HookResult()

    async def _exec(self, command: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        """执行单个 hook 命令，失败或超时都按 allow 降级。"""
        try:
            proc = await asyncio.create_subprocess_exec(
                *shlex.split(command),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(
                proc.communicate(json.dumps(payload).encode()),
                timeout=10.0,
            )
            if stdout.strip():
                return json.loads(stdout)
        except TimeoutError:
            logger.warning("Hook timed out (command=%r), treating as allow", command)
        except json.JSONDecodeError as exc:
            logger.warning("Hook returned invalid JSON (command=%r): %s", command, exc)
        except OSError as exc:
            logger.warning("Hook failed to start (command=%r): %s", command, exc)
        return None
