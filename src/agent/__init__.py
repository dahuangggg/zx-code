"""agent — 本地 Coding Agent 框架的顶层包。

公共入口只暴露 ``run_task``，其余功能通过子包按需导入：

- ``agent.core``        — Agent 主循环、上下文压缩、错误恢复
- ``agent.scheduling``  — 后台任务、定时调度、心跳、泳道调度
- ``agent.state``       — 持久化状态：记忆、Todo、Task DAG、技能、会话
- ``agent.agents``      — 子代理、多代理团队、Git worktree 隔离
- ``agent.channels``    — 外部消息通道（CLI / Telegram / Feishu）及路由网关
- ``agent.tools``       — 工具注册表及所有内置工具
- ``agent.mcp``         — MCP 协议客户端（官方 SDK 封装）
- ``agent.providers``   — LLM 提供商客户端（litellm 封装）
- ``agent.runtime``     — 组合根：将所有部件装配成可运行的 agent
"""

from agent.core.loop import run_task

__all__ = ["run_task"]

