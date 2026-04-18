from __future__ import annotations

import asyncio
import json
import shlex
import tomllib
from pathlib import Path
from typing import Any


class HookResult:
    __slots__ = ("denied", "reason")

    def __init__(self, *, denied: bool = False, reason: str = "") -> None:
        self.denied = denied
        self.reason = reason


class HookRunner:
    """Run user-defined hooks via subprocess + JSON protocol.

    Hook scripts receive a JSON payload on stdin and may write a JSON response
    to stdout.  A response with ``{"decision": "deny"}`` blocks the action for
    ``pre_tool_use`` events; all other events are fire-and-forget.

    Config file format (.zx-code/hooks.toml):

        [[pre_tool_use]]
        command = "python .zx-code/hooks/security.py"

        [[post_tool_use]]
        command = "bash .zx-code/hooks/audit.sh"
    """

    def __init__(self, hooks: dict[str, list[dict[str, str]]]) -> None:
        self._hooks = hooks

    @classmethod
    def from_file(cls, path: str | Path) -> "HookRunner":
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
        """Run all hooks registered for *event*.

        Returns a ``HookResult`` with ``denied=True`` if any hook returned
        ``{"decision": "deny"}``; otherwise returns an allowing result.
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
            pass
        except (json.JSONDecodeError, OSError):
            pass
        return None
