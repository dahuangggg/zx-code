"""将 trace JSONL 事件渲染成小型独立 HTML 报告。"""
from __future__ import annotations

import html
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any


def write_trace_html(path: Path, events: Sequence[dict[str, Any]]) -> None:
    """写入转义后的、无外部依赖的 trace HTML 视图。"""
    rows: list[str] = []
    for event in events:
        payload = json.dumps(event.get("payload", {}), ensure_ascii=False, indent=2)
        rows.append(
            "<section>"
            f"<h2>step {html.escape(str(event.get('step', 0)))} "
            f"{html.escape(str(event.get('event', '')))}</h2>"
            f"<pre>{html.escape(payload)}</pre>"
            "</section>"
        )
    path.write_text(
        "<!doctype html><meta charset='utf-8'>"
        "<title>Agent Trace</title>"
        "<style>"
        "body{font-family:system-ui,-apple-system,sans-serif;margin:24px;line-height:1.4}"
        "section{border-bottom:1px solid #ddd;padding:12px 0}"
        "h2{font-size:16px;margin:0 0 8px}"
        "pre{white-space:pre-wrap;background:#f6f8fa;padding:12px;border-radius:6px}"
        "</style>"
        + "\n".join(rows),
        encoding="utf-8",
    )
