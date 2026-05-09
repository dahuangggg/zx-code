"""agent.core — Agent 主循环及其直接依赖。

模块说明：
  loop.py     — ``run_task()``：驱动 LLM ↔ 工具的主循环，处理 hook、会话持久化
  context.py  — ``ContextGuard``：token 计数、工具结果截断、历史压缩
  recovery.py — ``ResilienceRunner``：单次 LLM 调用的弹性包装（限流退避、溢出压缩、截断续写）
"""
