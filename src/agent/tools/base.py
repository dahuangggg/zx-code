from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any, ClassVar

from pydantic import BaseModel

from agent.models import ToolResult


class Tool(ABC):
    name: ClassVar[str]
    description: ClassVar[str]
    input_model: ClassVar[type[BaseModel]]

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_model.model_json_schema(),
            },
        }

    async def execute(self, arguments: dict[str, Any], call_id: str) -> ToolResult:
        parsed = self.input_model.model_validate(arguments)
        output = await self.run(parsed)
        if isinstance(output, str):
            content = output
        else:
            content = json.dumps(output, indent=2, sort_keys=True)
        return ToolResult(
            call_id=call_id,
            name=self.name,
            content=content,
        )

    @abstractmethod
    async def run(self, arguments: BaseModel) -> str | dict[str, Any]:
        raise NotImplementedError

