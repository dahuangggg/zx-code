"""tools.truncator — 工具输出截断和完整结果落盘。

主循环不能把超大工具输出原样塞回模型上下文。ObservationTruncator 在保持
ToolEnvelope 协议的前提下，把完整 envelope JSON 保存到本地文件，并把模型可见
结果替换成 partial/error envelope 摘要。
"""
from __future__ import annotations

import os
import random
from uuid import uuid4
from datetime import datetime, timedelta
from pathlib import Path

from agent.models import ToolEnvelope

_MAX_LINES: int = int(os.getenv("TOOL_OUTPUT_MAX_LINES", "2000"))
_MAX_BYTES: int = int(os.getenv("TOOL_OUTPUT_MAX_BYTES", "51200"))
_DIRECTION: str = os.getenv("TOOL_OUTPUT_TRUNCATE_DIRECTION", "head")
_HEAD_TAIL_LINES: int = int(os.getenv("TOOL_OUTPUT_HEAD_TAIL_LINES", "40"))
_OUTPUT_DIR: str = os.getenv("TOOL_OUTPUT_DIR", ".zx-code/artifacts/tool-output")
_RETENTION_DAYS: int = int(os.getenv("TOOL_OUTPUT_RETENTION_DAYS", "7"))


class ObservationTruncator:
    """截断过大的 ToolEnvelope，同时保留完整输出可追溯。

    截断判断优先看工具 data 中常见的原始文本字段（content/stdout/stderr），这样
    bash 等工具不会因为 envelope 外壳较小而漏截断。没有可抽取文本时，退回按完整
    envelope JSON 计算大小。
    """

    def __init__(self, project_root: Path | str | None = None) -> None:
        self._root = Path(project_root) if project_root else Path.cwd()
        self._output_dir = self._root / _OUTPUT_DIR

    def truncate(self, tool_name: str, envelope: ToolEnvelope) -> ToolEnvelope:
        """必要时返回截断后的 envelope；不需要截断则原样返回。"""
        if envelope.context.get("truncation_skip"):
            return envelope

        full_json = envelope.model_dump_json(exclude_none=True)
        extracted_text = self._extract_text(envelope)
        inner_text = extracted_text if extracted_text is not None else full_json
        orig_lines = inner_text.count("\n") + 1
        orig_bytes = len(inner_text.encode("utf-8"))

        if orig_lines <= _MAX_LINES and orig_bytes <= _MAX_BYTES:
            return envelope

        return self._do_truncate(
            tool_name,
            full_json,
            envelope,
            inner_text,
            orig_lines,
            orig_bytes,
        )

    def _extract_text(self, envelope: ToolEnvelope) -> str | None:
        """从常见工具 data 字段提取模型真正关心的原始文本。"""
        parts: list[str] = []
        for key in ("content", "stdout", "stderr"):
            val = envelope.data.get(key)
            if isinstance(val, str):
                parts.append(val)
        if parts:
            return "\n".join(parts)
        return None

    def _do_truncate(
        self,
        tool_name: str,
        raw: str,
        envelope: ToolEnvelope,
        inner_text: str,
        orig_lines: int,
        orig_bytes: int,
    ) -> ToolEnvelope:
        """保存完整 JSON，并构造模型可见的截断 envelope。"""
        output_path = self._save(tool_name, raw)
        rel_path = (
            str(output_path.relative_to(self._root)) if output_path else None
        )

        direction = _DIRECTION if _DIRECTION in ("head", "tail", "head_tail") else "head"
        preview, kept_lines, kept_bytes = self._truncate_text(inner_text, direction)

        status = envelope.status
        if status != "error":
            status = "partial"

        if random.random() < 0.1:
            self._cleanup()

        return ToolEnvelope(
            status=status,
            data={
                "truncated": True,
                "truncation": {
                    "direction": direction,
                    "max_lines": _MAX_LINES,
                    "max_bytes": _MAX_BYTES,
                    "original_lines": orig_lines,
                    "original_bytes": orig_bytes,
                    "kept_lines": kept_lines,
                    "kept_bytes": kept_bytes,
                    "full_output_path": rel_path,
                },
                "preview": preview,
            },
            text=self._hint(rel_path, orig_lines, orig_bytes),
            error=envelope.error if status == "error" else None,
            stats=envelope.stats,
            context=envelope.context,
        )

    def _truncate_text(self, text: str, direction: str) -> tuple[str, int, int]:
        """按配置保留 head/tail/head_tail，并再次应用字节上限。"""
        lines = text.split("\n")
        if direction == "head_tail":
            n = _HEAD_TAIL_LINES
            kept = (
                lines[:n] + ["... (truncated) ..."] + lines[-n:]
                if len(lines) > n * 2
                else lines
            )
        elif direction == "tail":
            kept = lines[-_MAX_LINES:] if len(lines) > _MAX_LINES else lines
        else:
            kept = lines[:_MAX_LINES] if len(lines) > _MAX_LINES else lines

        result = "\n".join(kept)
        encoded = result.encode("utf-8")
        if len(encoded) > _MAX_BYTES:
            if direction == "tail":
                result = encoded[-_MAX_BYTES:].decode("utf-8", errors="ignore")
            else:
                result = encoded[:_MAX_BYTES].decode("utf-8", errors="ignore")

        return result, result.count("\n") + 1, len(result.encode("utf-8"))

    def _save(self, tool_name: str, content: str) -> Path | None:
        """保存完整 envelope JSON；失败时静默降级为只返回摘要。"""
        try:
            self._output_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            suffix = uuid4().hex[:8]
            path = self._output_dir / f"tool_{ts}_{tool_name}_{suffix}.json"
            path.write_text(content, encoding="utf-8")
            return path
        except Exception:
            return None

    def _hint(self, rel_path: str | None, lines: int, byte_size: int) -> str:
        """生成给模型看的截断说明和完整文件路径。"""
        kb = byte_size / 1024
        hint = f"⚠️ Output truncated ({lines} lines, {kb:.1f}KB)."
        if rel_path:
            hint += f"\nFull output: {rel_path}"
            hint += "\nUse Read with pagination or Grep to search the full output file."
        return hint

    def _cleanup(self) -> None:
        """按保留天数机会性清理旧输出文件，避免每次工具调用都扫目录。"""
        try:
            if not self._output_dir.exists():
                return
            cutoff = datetime.now() - timedelta(days=_RETENTION_DAYS)
            for f in self._output_dir.glob("tool_*.json"):
                try:
                    if datetime.fromtimestamp(f.stat().st_mtime) < cutoff:
                        f.unlink()
                except Exception:
                    pass
        except Exception:
            pass
