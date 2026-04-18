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
    model_config = ConfigDict(extra="forbid")

    frontmatter: dict[str, Any] = Field(default_factory=dict)
    body: str = ""


class MemoryStore:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path).expanduser()

    def ensure(self) -> None:
        if self.path.exists():
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(self._serialize(MemoryDocument(body="# Memory\n")), encoding="utf-8")

    def load(self) -> MemoryDocument:
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
        document = self.load()
        body = document.body.rstrip()
        entry = f"- **[{memory_type}]** {text.strip()} _(source: {source}, at: {self._now()})_"
        document.frontmatter["format"] = "agent-memory-v1"
        document.frontmatter["updated_at"] = self._now()
        document.body = f"{body}\n{entry}\n" if body else f"# Memory\n{entry}\n"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_write(self._serialize(document))

    def render_for_prompt(self, *, max_chars: int = 4000) -> str:
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
        fd, tmp_path = tempfile.mkstemp(
            dir=self.path.parent, suffix=".tmp", prefix=".memory-"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.rename(tmp_path, self.path)
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def _now(self) -> str:
        return datetime.now(UTC).isoformat(timespec="seconds")

