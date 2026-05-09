from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from agent.state.memory import MemoryStore
from agent.tools.base import Tool


class MemoryAppendInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    memory_type: Literal["user", "feedback", "project", "reference"] = Field(
        default="user",
        description=(
            "Category of the memory. "
            "'user' — user role, preferences, background knowledge; "
            "'feedback' — guidance on how to approach work (corrections or confirmed patterns); "
            "'project' — ongoing work, goals, decisions, deadlines; "
            "'reference' — pointers to external resources (URLs, Linear projects, dashboards)."
        ),
    )
    source: str = "agent"


class MemoryAppendTool(Tool):
    name = "memory_append"
    description = (
        "Append a durable cross-session memory note. "
        "Choose memory_type carefully: "
        "user (who the user is), feedback (how to work), "
        "project (what is being built), reference (where things live)."
    )
    input_model = MemoryAppendInput

    def __init__(self, store: MemoryStore) -> None:
        self.store = store

    async def run(self, arguments: MemoryAppendInput) -> dict[str, object]:
        self.store.append(
            arguments.text,
            source=arguments.source,
            memory_type=arguments.memory_type,
        )
        return {
            "path": str(self.store.path),
            "appended": arguments.text,
            "type": arguments.memory_type,
        }

