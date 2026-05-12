"""runtime.utils — CLI 辅助函数集合。

提供 runner.py 和 builder.py 使用的辅助工具：
  console            — Rich Console 实例，全局共享
  _configure_readline— 配置终端行编辑（UTF-8、方向键等）
  _stream_printer    — LLM 流式输出的回调函数
  _resolve_project_path — 将相对路径解析为相对于项目根的绝对路径
  _approval_prompt   — ask 权限的 CLI 审批提示（y/N）
  _validate_channel_settings — 频道配置合法性检查（token 是否提供等）
"""

from __future__ import annotations


import contextlib
import io
from pathlib import Path

from rich.console import Console

from agent.config import AgentSettings
from agent.permissions import PermissionCheck

console = Console()


def _configure_readline() -> None:
    try:
        import readline
    except ImportError:
        return

    bindings = [
        "set bind-tty-special-chars off",
        "set input-meta on",
        "set output-meta on",
        "set convert-meta off",
    ]
    if "libedit" in (readline.__doc__ or "").lower():
        bindings.append("set enable-meta-keybindings on")
    for binding in bindings:
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                readline.parse_and_bind(binding)
        except Exception:
            continue


def _stream_printer(chunk: str) -> None:
    console.print(chunk, end="")


def _resolve_project_path(project_root: Path, value: str) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    return project_root / path


def _approval_prompt(check: PermissionCheck) -> bool:
    console.print(f"[yellow]Permission required:[/yellow] {check.reason}")
    console.print(f"Tool: {check.tool_name}")
    answer = input("Allow this tool call? [y/N] ").strip().lower()
    return answer in {"y", "yes"}


def _validate_channel_settings(settings: AgentSettings) -> bool:
    if settings.channel.name == "telegram" and not settings.channel.telegram.token:
        console.print("[red]Error:[/red] --telegram-token is required for Telegram")
        return False
    if settings.scheduling.heartbeat_enabled and not settings.scheduling.heartbeat_to:
        console.print("[red]Error:[/red] --heartbeat-to is required when heartbeat is enabled")
        return False
    return True
