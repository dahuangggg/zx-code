from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class MemoryDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    frontmatter: dict[str, str] = Field(default_factory=dict)
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

    def append(self, text: str, *, source: str = "user") -> None:
        document = self.load()
        body = document.body.rstrip()
        entry = f"- {text.strip()} _(source: {source}, at: {self._now()})_"
        document.frontmatter["format"] = "agent-memory-v1"
        document.frontmatter["updated_at"] = self._now()
        document.body = f"{body}\n{entry}\n" if body else f"# Memory\n{entry}\n"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(self._serialize(document), encoding="utf-8")

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
        if not text.startswith("---\n"):
            return MemoryDocument(body=text)
        parts = text.split("---\n", 2)
        if len(parts) < 3:
            return MemoryDocument(body=text)

        frontmatter: dict[str, str] = {}
        for line in parts[1].splitlines():
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            frontmatter[key.strip()] = value.strip()
        return MemoryDocument(frontmatter=frontmatter, body=parts[2].lstrip())

    def _serialize(self, document: MemoryDocument) -> str:
        frontmatter = {
            "format": "agent-memory-v1",
            "updated_at": self._now(),
            **document.frontmatter,
        }
        lines = ["---"]
        for key, value in frontmatter.items():
            lines.append(f"{key}: {value}")
        lines.append("---")
        lines.append(document.body.rstrip() + "\n")
        return "\n".join(lines)

    def _now(self) -> str:
        return datetime.now(UTC).isoformat(timespec="seconds")

