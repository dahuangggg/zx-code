"""runtime.runner — 运行模式选择与主入口函数。

``_run_cli()`` 根据参数选择四种运行模式：

  1. 单次任务（-t "task"）     → ``_run_once()``
     直接处理一个任务字符串，完成后退出

  2. 频道单次（--channel X）   → ``_run_channel_once()``
     从指定通道取一条消息处理，完成后退出

  3. 频道循环（--channel X --watch）→ ``_run_channel_loop()``
     持续轮询消息，同时运行心跳和 cron 后台任务
     后台任务通过 BackgroundTaskManager 管理，Ctrl-C 优雅退出

  4. REPL 模式（无参数，channel=cli）→ ``_run_repl()``
     交互式命令行，readline 行编辑支持
"""

from __future__ import annotations


import asyncio
from datetime import UTC, datetime
from pathlib import Path
import uuid
from typing import Any

from rich.panel import Panel
from rich.table import Table

from agent.scheduling.background import BackgroundTaskManager
from agent.channels import InboundMessage
from agent.channels.gateway import build_session_key
from agent.config import AgentSettings
from agent.channels.delivery import DeliveryDaemon
from agent.errors import AgentError
from agent.scheduling.lanes import LaneScheduler
from agent.runtime.builder import _build_runtime, _attach_mcp_tools
from agent.runtime.infra import _build_gateway, _build_heartbeat_runner, _build_cron_scheduler
from agent.runtime.utils import (
    _configure_readline,
    _resolve_project_path,
    _validate_channel_settings,
    console,
)
from agent.state.sessions import SessionStore


def _new_cli_session_id() -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"cli:{stamp}:{uuid.uuid4().hex[:8]}"


def _prompt_context_name() -> str:
    return Path.cwd().name or "workspace"


def _format_repl_prompt(context_name: str) -> str:
    return (
        "\033[36mzx-code\033[0m "
        "\033[2m[\033[0m"
        f"\033[32m{context_name}\033[0m"
        "\033[2m]\033[0m "
        "\033[36m>\033[0m "
    )


def _print_resume_hint(session_id: str) -> None:
    console.print(f"[dim]Resume with:[/dim] uv run agent --resume {session_id}")


def _print_repl_banner(
    *,
    settings: AgentSettings,
    session_id: str,
    resumed: bool,
) -> None:
    table = Table.grid(padding=(0, 1))
    table.add_column(style="bold cyan", no_wrap=True)
    table.add_column()
    table.add_row("model", settings.model.name)
    table.add_row("session", session_id)
    table.add_row("mode", "resumed" if resumed else "new")
    table.add_row("cwd", str(Path.cwd()))
    console.print(
        Panel.fit(
            table,
            title="[bold]zx-code[/bold]",
            border_style="cyan",
        )
    )


def _print_repl_help() -> None:
    table = Table.grid(padding=(0, 2))
    table.add_column(style="bold cyan", no_wrap=True)
    table.add_column()
    table.add_row("/help", "Show REPL commands.")
    table.add_row("/session", "Show the current session id.")
    table.add_row("/clear", "Clear the terminal and redraw the status banner.")
    table.add_row("exit, quit, /exit, /quit", "Leave the REPL.")
    console.print(Panel.fit(table, title="commands", border_style="dim"))


def _print_repl_session(session_id: str, resumed: bool) -> None:
    mode = "resumed" if resumed else "new"
    console.print(f"[bold cyan]session[/bold cyan] {session_id} [dim]({mode})[/dim]")


def _cli_gateway_session_key(settings: AgentSettings, peer_id: str) -> str:
    return build_session_key(
        agent_id=settings.routing.force_agent_id or settings.routing.default_agent_id,
        channel="cli",
        account_id=settings.channel.account_id,
        peer_id=peer_id,
        dm_scope=settings.routing.dm_scope,
    )


def _recent_resume_messages(
    *,
    settings: AgentSettings,
    resume_session_id: str,
    limit: int = 6,
) -> list[tuple[str, str]]:
    project_root = Path.cwd()
    data_dir = _resolve_project_path(project_root, settings.state.data_dir)
    session_key = _cli_gateway_session_key(settings, resume_session_id)
    messages = SessionStore(data_dir / "sessions").rebuild_messages(session_key)
    visible_messages: list[tuple[str, str]] = []
    for message in messages:
        if message.role not in {"user", "assistant"}:
            continue
        content = _truncate_preview(message.content)
        if not content:
            continue
        visible_messages.append((message.role, content))
    return visible_messages[-limit:]


def _truncate_preview(text: str, limit: int = 220) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return f"{compact[: limit - 3]}..."


def _print_recent_resume_messages(
    *,
    settings: AgentSettings,
    resume_session_id: str,
) -> None:
    messages = _recent_resume_messages(
        settings=settings,
        resume_session_id=resume_session_id,
    )
    if not messages:
        return
    table = Table.grid(padding=(0, 1))
    table.add_column(style="bold cyan", no_wrap=True)
    table.add_column()
    for role, content in messages:
        label = "you" if role == "user" else "agent"
        table.add_row(label, content)
    console.print(Panel.fit(table, title="recent conversation", border_style="dim"))


async def _run_once(
    task: str,
    *,
    settings: AgentSettings,
    print_system_prompt: bool,
    resume_session_id: str | None = None,
) -> int:
    if print_system_prompt:
        runtime = _build_runtime(settings, session_id=resume_session_id or _new_cli_session_id())
        console.print(_render_system_prompt(runtime))
        return 0

    lane_scheduler = LaneScheduler()
    gateway = _build_gateway(
        settings,
        emit_cli=not settings.model.stream,
        lane_scheduler=lane_scheduler,
    )
    inbound = InboundMessage.cli(
        task,
        account_id=settings.channel.account_id,
        peer_id=resume_session_id or _new_cli_session_id(),
    )
    try:
        await gateway.handle_inbound(
            inbound,
            force_agent_id=settings.routing.force_agent_id or None,
        )
    except AgentError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        return 1
    finally:
        await lane_scheduler.close()
    return 0


async def _run_channel_once(
    *,
    settings: AgentSettings,
    print_system_prompt: bool,
) -> int:
    runtime = _build_runtime(settings)

    if print_system_prompt:
        console.print(_render_system_prompt(runtime))
        return 0

    if not _validate_channel_settings(settings):
        return 1

    lane_scheduler = LaneScheduler()
    gateway = _build_gateway(
        settings,
        emit_cli=not settings.model.stream,
        lane_scheduler=lane_scheduler,
    )
    try:
        result = await gateway.receive_once(
            settings.channel.name,
            force_agent_id=settings.routing.force_agent_id or None,
        )
    except AgentError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        return 1
    except KeyError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        return 1
    finally:
        await lane_scheduler.close()

    if result is None:
        console.print(f"No inbound message on channel: {settings.channel.name}")
    await gateway.drain_delivery()
    return 0


async def _run_channel_loop(
    *,
    settings: AgentSettings,
    print_system_prompt: bool,
) -> int:
    runtime = _build_runtime(settings)

    if print_system_prompt:
        console.print(_render_system_prompt(runtime))
        return 0

    if not _validate_channel_settings(settings):
        return 1

    lane_scheduler = LaneScheduler()
    gateway = _build_gateway(
        settings,
        emit_cli=not settings.model.stream,
        lane_scheduler=lane_scheduler,
    )
    heartbeat_runner = _build_heartbeat_runner(
        settings,
        gateway,
        lane_scheduler=lane_scheduler,
    )
    cron_scheduler = _build_cron_scheduler(
        settings,
        gateway,
        lane_scheduler=lane_scheduler,
    )
    delivery_daemon = (
        DeliveryDaemon(
            runner=gateway.delivery_runner,
            interval_s=settings.delivery.daemon_interval_s,
        )
        if gateway.delivery_runner is not None
        else None
    )
    if delivery_daemon is not None:
        delivery_daemon.start()
    bg_manager = BackgroundTaskManager()
    tick_counter = 0
    console.print(f"Listening on channel: {settings.channel.name}. Press Ctrl-C to stop.")
    try:
        while True:
            try:
                result = await gateway.receive_once(
                    settings.channel.name,
                    force_agent_id=settings.routing.force_agent_id or None,
                )
                if result is None:
                    await asyncio.sleep(0.2)
                if heartbeat_runner is not None:
                    tick_counter += 1
                    bg_manager.start(f"heartbeat:{tick_counter}", heartbeat_runner.tick())
                if cron_scheduler is not None:
                    tick_counter += 1
                    bg_manager.start(f"cron:{tick_counter}", cron_scheduler.tick())
            except AgentError as exc:
                console.print(f"[red]Error:[/red] {exc}")
                return 1
            except KeyError as exc:
                console.print(f"[red]Error:[/red] {exc}")
                return 1
    except KeyboardInterrupt:
        console.print()
        return 0
    finally:
        await bg_manager.cancel_all()
        if delivery_daemon is not None:
            await delivery_daemon.stop()
        await lane_scheduler.close()


async def _run_repl(
    *,
    settings: AgentSettings,
    print_system_prompt: bool,
    resume_session_id: str | None = None,
) -> int:
    _configure_readline()
    session_id = resume_session_id or _new_cli_session_id()
    resumed = resume_session_id is not None
    if print_system_prompt:
        runtime = _build_runtime(settings, session_id=session_id)
        console.print(_render_system_prompt(runtime))
        return 0

    _print_repl_banner(settings=settings, session_id=session_id, resumed=resumed)
    if resumed and resume_session_id is not None:
        _print_recent_resume_messages(
            settings=settings,
            resume_session_id=resume_session_id,
        )
    console.print("[dim]Type /help for commands. Type exit or quit to stop.[/dim]")
    prompt = _format_repl_prompt(_prompt_context_name())

    # Gateway 和 LaneScheduler 在整个 REPL 会话期间只创建一次，
    # 避免每轮重建事件循环导致的 MCP 连接重建和调度器状态丢失
    lane_scheduler = LaneScheduler()
    gateway = _build_gateway(
        settings,
        emit_cli=not settings.model.stream,
        lane_scheduler=lane_scheduler,
    )
    try:
        while True:
            try:
                # 用 asyncio.to_thread 将阻塞的 input() 移出事件循环线程，
                # 让 asyncio 在等待用户输入期间仍能调度其他协程
                task = await asyncio.to_thread(input, prompt)
            except EOFError:
                console.print()
                return 0
            task = task.strip()
            if not task:
                continue
            if task in {"exit", "quit", "/exit", "/quit"}:
                _print_resume_hint(session_id)
                return 0
            if task == "/help":
                _print_repl_help()
                continue
            if task == "/session":
                _print_repl_session(session_id, resumed)
                continue
            if task == "/clear":
                console.clear()
                _print_repl_banner(settings=settings, session_id=session_id, resumed=resumed)
                continue
            if task.startswith("/"):
                console.print(f"[red]Unknown command:[/red] {task}. Type /help for commands.")
                continue
            console.rule("[bold cyan]assistant[/bold cyan]")
            inbound = InboundMessage.cli(
                task,
                account_id=settings.channel.account_id,
                peer_id=session_id,
            )
            try:
                await gateway.handle_inbound(
                    inbound,
                    force_agent_id=settings.routing.force_agent_id or None,
                )
            except AgentError as exc:
                console.print(f"[red]Error:[/red] {exc}")
            console.rule(style="dim")
    finally:
        await lane_scheduler.close()


def _render_system_prompt(runtime: dict[str, Any]) -> str:
    return str(runtime["config"].system_prompt)


def _run_cli(
    *,
    task: str | None,
    settings: AgentSettings,
    print_system_prompt: bool,
    watch: bool,
    resume_session_id: str | None = None,
) -> int:
    if not task:
        if settings.channel.name != "cli":
            if watch:
                return asyncio.run(
                    _run_channel_loop(
                        settings=settings,
                        print_system_prompt=print_system_prompt,
                    )
                )
            return asyncio.run(
                _run_channel_once(
                    settings=settings,
                    print_system_prompt=print_system_prompt,
                )
            )
        return asyncio.run(
            _run_repl(
                settings=settings,
                print_system_prompt=print_system_prompt,
                resume_session_id=resume_session_id,
            )
        )
    return asyncio.run(
        _run_once(
            task,
            settings=settings,
            print_system_prompt=print_system_prompt,
            resume_session_id=resume_session_id,
        )
    )
