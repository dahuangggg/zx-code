from __future__ import annotations

from pathlib import Path

from agent.code_context.splitter import split_file


def test_python_splitter_prefers_class_and_function_chunks(tmp_path: Path) -> None:
    path = tmp_path / "sample.py"
    path.write_text(
        "\n".join(
            [
                "import os",
                "",
                "class Service:",
                "    def run(self):",
                "        return 'ok'",
                "",
                "async def load():",
                "    return 42",
            ]
        ),
        encoding="utf-8",
    )

    chunks = split_file(path, codebase_path=tmp_path, max_chars=500, overlap_lines=0)

    assert [chunk.symbol for chunk in chunks] == ["Service", "load"]
    assert chunks[0].start_line == 3
    assert chunks[0].end_line == 5
    assert chunks[1].start_line == 7
    assert chunks[1].end_line == 8


def test_line_splitter_preserves_line_ranges_and_overlap(tmp_path: Path) -> None:
    path = tmp_path / "notes.md"
    path.write_text("\n".join(f"line {index}" for index in range(1, 8)), encoding="utf-8")

    chunks = split_file(path, codebase_path=tmp_path, max_chars=20, overlap_lines=1)

    assert len(chunks) >= 3
    assert chunks[0].start_line == 1
    assert chunks[0].end_line == 2
    assert chunks[1].start_line == 2
    assert "line 2" in chunks[1].content
