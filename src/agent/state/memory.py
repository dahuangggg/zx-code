"""state.memory — 跨会话记忆存储（s09）。

记忆文件格式：YAML frontmatter + Markdown 正文，使用 ``python-frontmatter`` 解析。
每条记忆存为独立的 .md 文件，索引维护在 MEMORY.md。

记忆类型（frontmatter 中的 type 字段）：
  user      — 用户角色、偏好、背景知识
  feedback  — 用户对 agent 行为的反馈（正向/负向）
  project   — 当前项目的背景、决策、里程碑
  reference — 外部资源的指针（Linear 项目、Grafana 面板等）

System prompt 注入：
  ``render_for_prompt()`` 加载 MEMORY.md 索引（前 200 行），
  agent 通过 ``load_skill`` 工具按需加载完整记忆正文。
"""

from __future__ import annotations


import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import frontmatter

from pydantic import BaseModel, ConfigDict, Field


MemoryType = Literal["user", "feedback", "project", "reference"]


class MemoryDocument(BaseModel):
    """MEMORY.md 的解析结果。"""

    model_config = ConfigDict(extra="forbid")

    frontmatter: dict[str, Any] = Field(default_factory=dict)
    body: str = ""


class MemoryRecord(BaseModel):
    """独立记忆文档的输入模型。"""

    model_config = ConfigDict(extra="forbid")

    name: str
    description: str
    type: MemoryType
    content: str


class MemoryStore:
    """Markdown/frontmatter 记忆存储。

    默认 path 通常是 MEMORY.md 索引文件；save_record() 还会在同目录写独立记忆文档。
    render_for_prompt() 只注入截断后的索引内容，避免长期记忆无上限进入上下文。
    """

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path).expanduser()

    def ensure(self) -> None:
        """确保 MEMORY.md 存在。"""
        if self.path.exists():
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(self._serialize(MemoryDocument(body="# Memory\n")), encoding="utf-8")

    def load(self) -> MemoryDocument:
        """读取 MEMORY.md；不存在时返回默认空文档。"""
        if not self.path.exists():
            return MemoryDocument(
                frontmatter={
                    "format": "agent-memory-v1",
                    "updated_at": self._now(),
                },
                body="# Memory\n",
            )
        text = self.path.read_text(encoding="utf-8")
        return self._parse(text)

    def append(
        self,
        text: str,
        *,
        source: str = "user",
        memory_type: MemoryType | str = "user",
    ) -> None:
        """把一条简短记忆追加到 MEMORY.md 索引中。"""
        document = self.load()
        body = document.body.rstrip()
        entry = f"- **[{memory_type}]** {text.strip()} _(source: {source}, at: {self._now()})_"
        document.frontmatter["format"] = "agent-memory-v1"
        document.frontmatter["updated_at"] = self._now()
        document.body = f"{body}\n{entry}\n" if body else f"# Memory\n{entry}\n"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_write(self._serialize(document))

    def load_index(self, *, max_lines: int = 200) -> str:
        """读取 MEMORY.md 前 max_lines 行，供轻量预览使用。"""
        if not self.path.exists():
            return ""
        text = self.path.read_text(encoding="utf-8")
        document = self._parse(text)
        lines = document.body.splitlines()[:max_lines]
        return "\n".join(lines)

    def save_record(self, record: MemoryRecord) -> Path:
        """保存独立记忆文档，并把链接追加到 MEMORY.md 索引。"""
        record_path = self.path.parent / f"{_safe_memory_name(record.name)}.md"
        post = frontmatter.Post(
            record.content.rstrip() + "\n",
            name=record.name,
            description=record.description,
            type=record.type,
            updated_at=self._now(),
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_write_path(record_path, frontmatter.dumps(post))
        self._append_record_to_index(record, record_path.name)
        return record_path

    def render_for_prompt(self, *, max_chars: int = 4000) -> str:
        """渲染注入 system prompt 的记忆文本，超长时截断。"""
        document = self.load()
        body = document.body.strip()
        if not body:
            return ""
        if len(body) <= max_chars:
            return body
        omitted = len(body) - max_chars
        return body[:max_chars] + f"\n\n[memory truncated, omitted {omitted} chars]"

    def _parse(self, text: str) -> MemoryDocument:
        post = frontmatter.loads(text)
        return MemoryDocument(frontmatter=dict(post.metadata), body=post.content)

    def _serialize(self, document: MemoryDocument) -> str:
        metadata = {
            "format": "agent-memory-v1",
            "updated_at": self._now(),
            **document.frontmatter,
        }
        post = frontmatter.Post(document.body.rstrip() + "\n", **metadata)
        return frontmatter.dumps(post)

    def _atomic_write(self, content: str) -> None:
        self._atomic_write_path(self.path, content)

    def _atomic_write_path(self, path: Path, content: str) -> None:
        """原子写 Markdown 文件，避免崩溃留下半写内容。"""
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            dir=path.parent, suffix=".tmp", prefix=".memory-"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, path)
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def _now(self) -> str:
        return datetime.now(UTC).isoformat(timespec="seconds")

    def _append_record_to_index(self, record: MemoryRecord, filename: str) -> None:
        document = self.load()
        body = document.body.rstrip()
        if not body:
            body = "# Memory"
        link = f"- [{record.name}]({filename}) — {record.description}"
        if link not in body:
            body = f"{body}\n{link}"
        document.frontmatter["format"] = "agent-memory-v1"
        document.frontmatter["updated_at"] = self._now()
        document.body = body + "\n"
        self._atomic_write(self._serialize(document))


def _safe_memory_name(name: str) -> str:
    """把记忆名转为安全文件名。"""
    safe = "".join(char if char.isalnum() or char in "._-" else "-" for char in name.strip())
    return safe.strip(".-_") or "memory"
