"""agent.providers — LLM 客户端提供商抽象层。

  base.py          — ``ModelClient`` Protocol：定义 run_turn() 接口，解耦循环与具体 SDK
  openai_client.py — ``OpenAIModelClient``：基于 openai SDK 的原生实现
"""
from agent.providers.openai_client import OpenAIModelClient

__all__ = ["OpenAIModelClient"]
