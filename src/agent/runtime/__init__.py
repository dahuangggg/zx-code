"""agent.runtime — 组合根（Composition Root）。

将框架所有部件装配成可运行的 agent，对外暴露三个入口：

  builder.py — ``_build_runtime()``：实例化所有依赖（model client、工具注册表、prompt builder 等）
  infra.py   — ``_build_gateway()``：构建消息网关、心跳、cron 调度器
  runner.py  — ``_run_cli()``：顶层运行模式选择（单次任务 / 频道单次 / 频道循环 / REPL）
  utils.py   — CLI 辅助函数（readline、审批提示、流式打印等）
"""
from agent.runtime.builder import _attach_mcp_tools, _build_runtime
from agent.runtime.runner import _run_cli
from agent.runtime.utils import _configure_readline, console

__all__ = [
    "_attach_mcp_tools",
    "_build_runtime",
    "_configure_readline",
    "_run_cli",
    "console",
]
