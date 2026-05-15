from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agent.agents.team import Team
from agent.tools.base import Tool


class TeamListParams(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TeamDispatchParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    to_agent: str = Field(min_length=1)
    task: str = Field(min_length=1)
    timeout_s: float | None = Field(default=None, gt=0)


class TeamBroadcastParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1)


class TeamListTool(Tool):
    name = "team_list"
    description = "List available team agents and their roles."
    input_model = TeamListParams

    def __init__(self, team: Team) -> None:
        self.team = team

    async def run(self, arguments: TeamListParams) -> dict[str, Any]:
        return {"members": self.team.members_summary()}

    def is_concurrency_safe(self, arguments: dict[str, object] | BaseModel) -> bool:
        return True


class TeamDispatchTool(Tool):
    name = "team_dispatch"
    description = (
        "Ask a named team agent to perform a focused subtask and return its reply."
    )
    input_model = TeamDispatchParams

    def __init__(self, team: Team, *, default_timeout_s: float = 30.0) -> None:
        self.team = team
        self.default_timeout_s = default_timeout_s

    async def run(self, arguments: TeamDispatchParams) -> dict[str, Any]:
        timeout_s = arguments.timeout_s or self.default_timeout_s
        self.team.ensure_started()
        try:
            reply = await self.team.dispatch(
                arguments.to_agent,
                arguments.task,
                timeout_s=timeout_s,
            )
        except Exception as exc:
            return {
                "to_agent": arguments.to_agent,
                "task": arguments.task,
                "reply": "",
                "error": str(exc),
            }
        return {
            "to_agent": arguments.to_agent,
            "task": arguments.task,
            "reply": reply,
            "error": "",
        }


class TeamBroadcastTool(Tool):
    name = "team_broadcast"
    description = "Broadcast context to all team agents without waiting for a reply."
    input_model = TeamBroadcastParams

    def __init__(self, team: Team) -> None:
        self.team = team

    async def run(self, arguments: TeamBroadcastParams) -> dict[str, Any]:
        self.team.ensure_started()
        await self.team.broadcast(arguments.content)
        return {
            "broadcast": arguments.content,
            "members": self.team.members_summary(),
        }
