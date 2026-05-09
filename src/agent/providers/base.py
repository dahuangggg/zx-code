"""providers.base — LLM 客户端 Protocol 定义。

``ModelClient`` 是一个 Protocol（结构化子类型），定义了核心循环与
具体 LLM SDK 之间的唯一接口：

  run_turn(system_prompt, messages, tools, stream_handler) -> ModelTurn

好处：
  - 核心循环（loop.py）不依赖任何具体 SDK
  - 可通过 mock 实现进行单元测试，无需真实 API 调用
  - 未来新增供应商只需实现这一个方法

``StreamHandler`` 是可选的流式输出回调，每收到一个文本 chunk 调用一次。
"""

from __future__ import annotations


from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Protocol

from agent.models import Message, ModelTurn

StreamHandler = Callable[[str], Awaitable[None] | None]


class ModelClient(Protocol):
    async def run_turn(
        self,
        *,
        system_prompt: str,
        messages: Sequence[Message],
        tools: list[dict[str, Any]],
        stream_handler: StreamHandler | None = None,
    ) -> ModelTurn:
        ...

