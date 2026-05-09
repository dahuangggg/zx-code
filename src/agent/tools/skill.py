from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from agent.state.skills import SkillStore
from agent.tools.base import Tool


class LoadSkillInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    max_chars: int = Field(default=12000, ge=1, le=50000)


class LoadSkillTool(Tool):
    name = "load_skill"
    description = "Load the full markdown body for a named skill."
    input_model = LoadSkillInput

    def __init__(self, store: SkillStore) -> None:
        self.store = store

    async def run(self, arguments: LoadSkillInput) -> dict[str, object]:
        document = self.store.load(arguments.name)
        content = document.content
        truncated = False
        if len(content) > arguments.max_chars:
            content = content[: arguments.max_chars]
            truncated = True
        return {
            "name": document.metadata.name,
            "title": document.metadata.title,
            "description": document.metadata.description,
            "path": str(document.metadata.path),
            "content": content,
            "truncated": truncated,
        }
