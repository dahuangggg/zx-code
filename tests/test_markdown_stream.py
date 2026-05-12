from __future__ import annotations

from rich.markdown import Markdown
from rich.text import Text

from agent.runtime.markdown_stream import MarkdownStreamRenderer


class FakeConsole:
    def __init__(self) -> None:
        self.rendered: list[object] = []

    def print(self, item: object, **kwargs: object) -> None:
        self.rendered.append(item)


def _rendered_text(console: FakeConsole) -> list[str]:
    return [
        item.markup if isinstance(item, Markdown) else str(item)
        for item in console.rendered
    ]


class FakeLive:
    def __init__(self, renderable: object, events: list[tuple[str, str]]) -> None:
        self.events = events
        self.renderable = renderable

    def start(self) -> None:
        self.events.append(("start", str(self.renderable)))

    def update(self, renderable: object) -> None:
        self.renderable = renderable
        self.events.append(("update", str(renderable)))

    def stop(self) -> None:
        self.events.append(("stop", str(self.renderable)))


def _renderer_with_fake_live(
    console: FakeConsole,
    live_events: list[tuple[str, str]] | None = None,
) -> MarkdownStreamRenderer:
    events = live_events if live_events is not None else []
    return MarkdownStreamRenderer(
        console,
        live_factory=lambda renderable: FakeLive(renderable, events),
    )


def test_markdown_stream_shows_incomplete_current_block_as_live_text() -> None:
    console = FakeConsole()
    live_events: list[tuple[str, str]] = []
    renderer = _renderer_with_fake_live(console, live_events)

    renderer.write("Streaming")
    renderer.write(" paragraph")

    assert _rendered_text(console) == []
    assert live_events == [
        ("start", "Streaming"),
        ("update", "Streaming paragraph"),
    ]


def test_markdown_stream_replaces_completed_live_block_with_markdown() -> None:
    console = FakeConsole()
    live_events: list[tuple[str, str]] = []
    renderer = _renderer_with_fake_live(console, live_events)

    renderer.write("## Plan")
    renderer.write("\n\n")

    assert live_events == [
        ("start", "## Plan"),
        ("update", "## Plan\n\n"),
        ("stop", "## Plan\n\n"),
    ]
    assert _rendered_text(console) == ["## Plan\n\n"]


def test_markdown_stream_renders_paragraphs_at_blank_line_boundaries() -> None:
    console = FakeConsole()
    renderer = _renderer_with_fake_live(console)

    renderer.write("## Plan\n\n")
    renderer.write("- first item\n")
    renderer.write("- second item\n\n")

    assert _rendered_text(console) == [
        "## Plan\n\n",
        "- first item\n- second item\n\n",
    ]


def test_markdown_stream_waits_for_fenced_code_block_to_close() -> None:
    console = FakeConsole()
    renderer = _renderer_with_fake_live(console)

    renderer.write("```python\n")
    renderer.write('print("hi")\n')

    assert _rendered_text(console) == []

    renderer.write("```\n\n")

    assert _rendered_text(console) == [
        '```python\nprint("hi")\n```\n\n',
    ]


def test_markdown_stream_flush_renders_remaining_partial_paragraph() -> None:
    console = FakeConsole()
    renderer = _renderer_with_fake_live(console)

    renderer.write("Final sentence without trailing blank line.")

    assert _rendered_text(console) == []

    renderer.flush()

    assert _rendered_text(console) == [
        "Final sentence without trailing blank line.",
    ]
