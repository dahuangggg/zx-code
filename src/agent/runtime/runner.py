"""runtime.runner — CLI-only 运行模式编排。

本模块负责单轮任务和 REPL 控制流：session 选择、prompt 打印、用户输入和
LaneScheduler 生命周期。它不直接构造运行时依赖，而是委托 runtime.builder 装配。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
import uuid

from rich.panel import Panel
from rich.table import Table

from agent.config import AgentSettings
from agent.errors import AgentError
from agent.core.concurrency import LaneScheduler
from agent.operations import CheckpointConflict
from agent.runtime.builder import _build_runtime, _run_agent_text
from agent.runtime.types import AgentRuntime
from agent.runtime.session import AgentSession
from agent.runtime.utils import _configure_readline, _resolve_project_path, console
from agent.security import EffectivePolicy
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
    console.print(Panel.fit(table, title="[bold]zx-code[/bold]", border_style="cyan"))


def _print_repl_help() -> None:
    table = Table.grid(padding=(0, 2))
    table.add_column(style="bold cyan", no_wrap=True)
    table.add_column()
    table.add_row("/help", "Show REPL commands.")
    table.add_row("/session", "Show the current session id.")
    table.add_row("/undo [operation-id]", "Undo the latest or a named file change.")
    table.add_row("/clear", "Clear the terminal and redraw the status banner.")
    table.add_row("exit, quit, /exit, /quit", "Leave the REPL.")
    console.print(Panel.fit(table, title="commands", border_style="dim"))


def _print_repl_session(session_id: str, resumed: bool) -> None:
    mode = "resumed" if resumed else "new"
    console.print(f"[bold cyan]session[/bold cyan] {session_id} [dim]({mode})[/dim]")


def _recent_resume_messages(
    *,
    settings: AgentSettings,
    resume_session_id: str,
    limit: int = 6,
) -> list[tuple[str, str]]:
    project_root = Path.cwd()
    data_dir = _resolve_project_path(project_root, settings.state.data_dir)
    messages = SessionStore(data_dir / "sessions").rebuild_messages(resume_session_id)
    visible_messages: list[tuple[str, str]] = []
    for message in messages:
        if message.role not in {"user", "assistant"}:
            continue
        content = _truncate_preview(message.content)
        if content:
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
    messages = _recent_resume_messages(settings=settings, resume_session_id=resume_session_id)
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
    policy: EffectivePolicy | None = None,
) -> int:
    session_id = resume_session_id or _new_cli_session_id()
    if print_system_prompt:
        runtime = _build_runtime(settings, session_id=session_id, policy=policy)
        console.print(_render_system_prompt(runtime))
        return 0

    lane_scheduler = LaneScheduler()
    try:
        kwargs = {"policy": policy} if policy is not None else {}
        final_text = await _run_agent_text(
            task,
            settings=settings,
            session_id=session_id,
            lane_scheduler=lane_scheduler,
            **kwargs,
        )
        if not settings.model.stream and final_text:
            console.print(final_text)
    except AgentError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        return 1
    finally:
        await lane_scheduler.close()
    return 0


async def _run_repl(
    *,
    settings: AgentSettings,
    print_system_prompt: bool,
    resume_session_id: str | None = None,
    policy: EffectivePolicy | None = None,
) -> int:
    _configure_readline()
    session_id = resume_session_id or _new_cli_session_id()
    resumed = resume_session_id is not None
    if print_system_prompt:
        runtime = _build_runtime(settings, session_id=session_id, policy=policy)
        console.print(_render_system_prompt(runtime))
        return 0

    _print_repl_banner(settings=settings, session_id=session_id, resumed=resumed)
    if resumed and resume_session_id is not None:
        _print_recent_resume_messages(settings=settings, resume_session_id=resume_session_id)
    console.print("[dim]Type /help for commands. Type exit or quit to stop.[/dim]")
    prompt = _format_repl_prompt(_prompt_context_name())
    lane_scheduler = LaneScheduler()
    agent_session: AgentSession | None = None
    try:
        agent_session = await AgentSession.open(
            settings,
            session_id=session_id,
            lane_scheduler=lane_scheduler,
            policy=policy,
        )
        while True:
            try:
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
            if task == "/undo" or task.startswith("/undo "):
                operation_id = task.removeprefix("/undo").strip() or None
                try:
                    undone_id, restored = agent_session.undo(operation_id)
                except (CheckpointConflict, KeyError, ValueError) as exc:
                    message = str(exc.args[0]) if exc.args else str(exc)
                    console.print(f"[red]Undo failed:[/red] {message}")
                else:
                    console.print(
                        f"[green]Undid[/green] {undone_id} [dim]({restored})[/dim]"
                    )
                continue
            if task == "/clear":
                console.clear()
                _print_repl_banner(settings=settings, session_id=session_id, resumed=resumed)
                continue
            if task.startswith("/"):
                console.print(f"[red]Unknown command:[/red] {task}. Type /help for commands.")
                continue
            console.rule("[bold cyan]assistant[/bold cyan]")
            try:
                run_result = await agent_session.run(task)
                final_text = run_result.final_text
                if not settings.model.stream and final_text:
                    console.print(final_text)
            except AgentError as exc:
                console.print(f"[red]Error:[/red] {exc}")
            console.rule(style="dim")
    finally:
        if agent_session is not None:
            await agent_session.close()
        await lane_scheduler.close()


def _render_system_prompt(runtime: AgentRuntime) -> str:
    return str(runtime.system_prompt)


def _run_cli(
    *,
    task: str | None,
    settings: AgentSettings,
    print_system_prompt: bool,
    resume_session_id: str | None = None,
    policy: EffectivePolicy | None = None,
) -> int:
    if not task:
        return asyncio.run(
            _run_repl(
                settings=settings,
                print_system_prompt=print_system_prompt,
                resume_session_id=resume_session_id,
                policy=policy,
            )
        )
    return asyncio.run(
        _run_once(
            task,
            settings=settings,
            print_system_prompt=print_system_prompt,
            resume_session_id=resume_session_id,
            policy=policy,
        )
    )
