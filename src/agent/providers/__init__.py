"""agent.providers — LLM 客户端提供商抽象层。

  base.py         — ``ModelClient`` Protocol：定义 run_turn() 接口，解耦循环与具体 SDK
  litellm_client.py — ``LiteLLMModelClient``：基于 litellm 的统一实现，支持 100+ 模型

新增供应商只需实现 base.py 中的 ModelClient Protocol，无需修改核心循环。
"""
from agent.providers.litellm_client import LiteLLMModelClient

__all__ = ["LiteLLMModelClient"]

