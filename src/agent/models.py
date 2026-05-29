"""共享 agent 模型类型的兼容 re-export。

新代码可以从 :mod:`agent.model_types` 下的聚焦模块导入；已有的
``from agent.models import ...`` 调用方仍会拿到同一组类。
"""
from __future__ import annotations

from agent.model_types import *  # noqa: F403
from agent.model_types import __all__
