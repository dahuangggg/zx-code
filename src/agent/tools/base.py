"""tools.base — 所有工具的抽象基类。

``Tool`` 定义工具的统一接口：
  - ``name`` / ``description`` / ``input_model`` — 类变量，自动生成 JSON Schema
  - ``schema()`` — 返回 LLM tool_use 格式的 dict
  - ``execute()`` — 验证参数（pydantic）→ 调用 ``run()`` → 封装为 ToolResult
  - ``run()`` — 子类实现，返回 str 或 dict（dict 自动 JSON 序列化）
  - ``is_concurrency_safe()`` — 只读工具可返回 True，允许并发执行

实现新工具只需继承 Tool，声明三个类变量，实现 run() 即可。
"""

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

    def is_concurrency_safe(self, arguments: dict[str, Any] | BaseModel) -> bool:
        return False
