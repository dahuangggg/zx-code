"""runtime.markdown_stream — chunked Markdown rendering for streamed output."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.text import Text


LiveFactory = Callable[[object], Any]


class MarkdownStreamRenderer:
    """Render streamed Markdown once stable block boundaries are reached."""

    def __init__(
        self,
        console: Console,
        *,
        live_factory: LiveFactory | None = None,
    ) -> None:
        self.console = console
        self.buffer = ""
        self._live: Any | None = None
        self._live_factory = live_factory or (
            lambda renderable: Live(
                renderable,
                console=console,
                transient=True,
                refresh_per_second=12,
            )
        )

    def write(self, chunk: str) -> None:
        self.buffer += chunk
        ready_blocks = self._pop_ready_blocks()
        if ready_blocks:
            self._stop_live("".join(ready_blocks))
        for block in ready_blocks:
            self.console.print(Markdown(block))
        self._update_live()

    def flush(self) -> None:
        if not self.buffer:
            return
        self._stop_live(self.buffer)
        block = self.buffer
        self.buffer = ""
        self.console.print(Markdown(block))

    def _pop_ready_blocks(self) -> list[str]:
        ready: list[str] = []
        while True:
            boundary = self._next_ready_boundary()
            if boundary is None:
                return ready
            ready.append(self.buffer[:boundary])
            self.buffer = self.buffer[boundary:]

    def _next_ready_boundary(self) -> int | None:
        in_fence = False
        line_start = 0
        index = 0

        while index < len(self.buffer):
            if self.buffer.startswith("```", line_start):
                in_fence = not in_fence

            newline = self.buffer.find("\n", index)
            if newline == -1:
                return None

            line = self.buffer[line_start : newline + 1]
            next_line_start = newline + 1

            if not in_fence and line.strip() == "":
                return next_line_start

            line_start = next_line_start
            index = next_line_start

        return None

    def _update_live(self) -> None:
        if not self.buffer:
            return
        renderable = Text(self.buffer)
        if self._live is None:
            self._live = self._live_factory(renderable)
            self._live.start()
            return
        self._live.update(renderable)

    def _stop_live(self, final_renderable: str | None = None) -> None:
        if self._live is None:
            return
        if final_renderable is not None:
            self._live.update(Text(final_renderable))
        self._live.stop()
        self._live = None
