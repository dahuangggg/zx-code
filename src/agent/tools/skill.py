"""load_skill 工具。

按名称读取 SkillStore 中的技能 Markdown。技能内容可能很长，所以本工具自己做
max_chars 截断，并设置 truncation_skip，避免通用截断器再次改写内容。
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from agent.models import ToolEnvelope
from agent.state.skills import SkillStore
from agent.tools.base import Tool


class LoadSkillInput(BaseModel):
    """load_skill 参数；max_chars 限制返回给模型的技能正文长度。"""

    model_config = ConfigDict(extra="forbid")

    name: str
    max_chars: int = Field(default=12000, ge=1, le=50000)


class LoadSkillTool(Tool):
    """加载技能正文；截断时返回 partial envelope。"""

    name = "load_skill"
    description = "Load the full markdown body for a named skill."
    input_model = LoadSkillInput

    def __init__(self, store: SkillStore) -> None:
        self.store = store

    async def run(self, arguments: LoadSkillInput) -> ToolEnvelope:
        document = self.store.load(arguments.name)
        content = document.content
        truncated = len(content) > arguments.max_chars
        if truncated:
            content = content[: arguments.max_chars]
        note = f" (truncated to {arguments.max_chars} chars)" if truncated else ""
        return (self.partial if truncated else self.success)(
            data={
                "name": document.metadata.name,
                "title": document.metadata.title,
                "description": document.metadata.description,
                "path": str(document.metadata.path),
                "content": content,
                "truncated": truncated,
            },
            text=f"Loaded skill '{document.metadata.name}'{note}.",
            extra_context={"truncation_skip": True},
        )
