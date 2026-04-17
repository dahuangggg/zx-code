from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from agent.memory import MemoryStore
from agent.tools.base import Tool


class MemoryAppendInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    source: str = "agent"


class MemoryAppendTool(Tool):
    name = "memory_append"
    description = "Append a durable memory note after user approval."
    input_model = MemoryAppendInput

    def __init__(self, store: MemoryStore) -> None:
        self.store = store

    async def run(self, arguments: MemoryAppendInput) -> dict[str, object]:
        self.store.append(arguments.text, source=arguments.source)
        return {"path": str(self.store.path), "appended": arguments.text}

