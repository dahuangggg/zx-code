"""runtime.markdown_stream — 流式输出的 Markdown 分块渲染。

模型流式返回时，Markdown 语法块可能跨多个 chunk。MarkdownStreamRenderer 只在空行
等稳定边界处把内容交给 Rich Markdown，未闭合部分先用 Live 文本临时显示，避免半个
代码块被反复错误渲染。
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.text import Text


LiveFactory = Callable[[object], Any]


class MarkdownStreamRenderer:
    """在稳定 block 边界渲染 Markdown，未完成内容用 Live 预览。"""

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
        """追加一个模型 chunk，并渲染已经完整的 Markdown block。"""
        self.buffer += chunk
        ready_blocks = self._pop_ready_blocks()
        if ready_blocks:
            self._stop_live("".join(ready_blocks))
        for block in ready_blocks:
            self.console.print(Markdown(block))
        self._update_live()

    def flush(self) -> None:
        """任务结束或工具开始前强制渲染剩余 buffer。"""
        if not self.buffer:
            return
        self._stop_live(self.buffer)
        block = self.buffer
        self.buffer = ""
        self.console.print(Markdown(block))

    def _pop_ready_blocks(self) -> list[str]:
        """从 buffer 头部弹出所有已经到达稳定边界的 block。"""
        ready: list[str] = []
        while True:
            boundary = self._next_ready_boundary()
            if boundary is None:
                return ready
            ready.append(self.buffer[:boundary])
            self.buffer = self.buffer[boundary:]

    def _next_ready_boundary(self) -> int | None:
        """寻找下一个可安全渲染边界，避免在 fenced code block 中切开。"""
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
        """用纯文本 Live 展示尚未形成完整 Markdown block 的内容。"""
        if not self.buffer:
            return
        renderable = Text(self.buffer)
        if self._live is None:
            self._live = self._live_factory(renderable)
            self._live.start()
            return
        self._live.update(renderable)

    def _stop_live(self, final_renderable: str | None = None) -> None:
        """停止 Live 预览；必要时先展示最后一帧文本。"""
        if self._live is None:
            return
        if final_renderable is not None:
            self._live.update(Text(final_renderable))
        self._live.stop()
        self._live = None
