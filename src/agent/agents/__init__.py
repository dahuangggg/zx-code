"""agent.agents — 多代理模式：子代理、团队协作、工作区隔离。

模块说明：
  subagent.py  — ``SubagentRunner``：在独立消息历史中运行嵌套 agent，支持深度限制和泳道调度
  team.py      — ``Team`` / ``MessageBus``：多 agent 消息总线（asyncio.Queue + JSONL 持久化）
  worktree.py  — ``WorktreeManager``：Git worktree 隔离，每个子代理在独立分支上操作文件
"""
