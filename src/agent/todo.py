from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


TodoStatus = Literal["pending", "in_progress", "completed"]


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class TodoItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: uuid4().hex[:8])
    title: str
    status: TodoStatus = "pending"
    notes: str = ""
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)


class TodoManager:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path).expanduser()

    def list(self) -> list[TodoItem]:
        if not self.path.exists():
            return []
        raw = json.loads(self.path.read_text(encoding="utf-8") or "[]")
        return [TodoItem.model_validate(item) for item in raw]

    def create(self, title: str, *, notes: str = "") -> TodoItem:
        item = TodoItem(title=title, notes=notes)
        items = self.list()
        items.append(item)
        self._save(items)
        return item

    def update(
        self,
        todo_id: str,
        *,
        title: str | None = None,
        status: TodoStatus | None = None,
        notes: str | None = None,
    ) -> TodoItem:
        items = self.list()
        for index, item in enumerate(items):
            if item.id != todo_id:
                continue
            updated = item.model_copy(
                update={
                    "title": title if title is not None else item.title,
                    "status": status if status is not None else item.status,
                    "notes": notes if notes is not None else item.notes,
                    "updated_at": _now(),
                }
            )
            items[index] = updated
            self._save(items)
            return updated
        raise KeyError(f"todo not found: {todo_id}")

    def complete(self, todo_id: str) -> TodoItem:
        return self.update(todo_id, status="completed")

    def render_for_prompt(self) -> str:
        items = self.list()
        if not items:
            return ""

        lines = ["Current todos:"]
        for item in items:
            suffix = f" - {item.notes}" if item.notes else ""
            lines.append(f"- [{item.status}] {item.id}: {item.title}{suffix}")
        return "\n".join(lines)

    def _save(self, items: list[TodoItem]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = [item.model_dump(mode="json") for item in items]
        self.path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

