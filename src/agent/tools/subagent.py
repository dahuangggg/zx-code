from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from agent.agents.subagent import SubagentRunner
from agent.models import ToolEnvelope
from agent.tools.base import Tool


class SubagentRunParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task: str = Field(min_length=1)
    label: str = "worker"


class SubagentRunTool(Tool):
    name = "subagent_run"
    description = (
        "Run a focused subagent in an isolated child session and return its final summary."
    )
    input_model = SubagentRunParams

    def __init__(self, runner: SubagentRunner) -> None:
        self.runner = runner

    async def run(self, arguments: SubagentRunParams) -> ToolEnvelope:
        result = await self.runner.run(arguments.task, label=arguments.label)
        data = result.model_dump(mode="json")
        summary = str(data.get("summary") or data.get("output") or "Subagent completed.")[:200]
        return self.success(data=data, text=f"Subagent '{arguments.label}' completed: {summary}")
