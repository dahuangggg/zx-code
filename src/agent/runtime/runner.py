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
from typing import Any

from agent.scheduling.background import BackgroundTaskManager
from agent.channels import InboundMessage
from agent.config import AgentSettings
from agent.channels.delivery import DeliveryDaemon
from agent.errors import AgentError
from agent.scheduling.lanes import LaneScheduler
from agent.runtime.builder import _build_runtime, _attach_mcp_tools
from agent.runtime.infra import _build_gateway, _build_heartbeat_runner, _build_cron_scheduler
from agent.runtime.utils import (
    _configure_readline,
    _validate_channel_settings,
    console,
)


async def _run_once(
    task: str,
    *,
    settings: AgentSettings,
    print_system_prompt: bool,
) -> int:
    if print_system_prompt:
        runtime = _build_runtime(settings)
        console.print(_render_system_prompt(runtime))
        return 0

    lane_scheduler = LaneScheduler()
    gateway = _build_gateway(
        settings,
        emit_cli=not settings.stream,
        lane_scheduler=lane_scheduler,
    )
    inbound = InboundMessage.cli(
        task,
        account_id=settings.account_id,
        peer_id=settings.session_id,
    )
    try:
        await gateway.handle_inbound(
            inbound,
            force_agent_id=settings.force_agent_id or None,
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
        emit_cli=not settings.stream,
        lane_scheduler=lane_scheduler,
    )
    try:
        result = await gateway.receive_once(
            settings.channel,
            force_agent_id=settings.force_agent_id or None,
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
        console.print(f"No inbound message on channel: {settings.channel}")
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
        emit_cli=not settings.stream,
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
            interval_s=settings.delivery_daemon_interval_s,
        )
        if gateway.delivery_runner is not None
        else None
    )
    if delivery_daemon is not None:
        delivery_daemon.start()
    bg_manager = BackgroundTaskManager()
    tick_counter = 0
    console.print(f"Listening on channel: {settings.channel}. Press Ctrl-C to stop.")
    try:
        while True:
            try:
                result = await gateway.receive_once(
                    settings.channel,
                    force_agent_id=settings.force_agent_id or None,
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


def _run_repl(*, settings: AgentSettings, print_system_prompt: bool) -> int:
    _configure_readline()
    if print_system_prompt:
        runtime = _build_runtime(settings)
        console.print(_render_system_prompt(runtime))
        return 0

    console.print("Entering REPL. Type 'exit' or 'quit' to stop.")
    while True:
        try:
            task = input("zx-code> ").strip()
        except EOFError:
            console.print()
            return 0
        if not task:
            continue
        if task in {"exit", "quit"}:
            return 0
        exit_code = asyncio.run(
            _run_once(
                task,
                settings=settings,
                print_system_prompt=False,
            )
        )
        if exit_code != 0:
            return exit_code


def _render_system_prompt(runtime: dict[str, Any]) -> str:
    return str(runtime["config"].system_prompt)


def _run_cli(
    *,
    task: str | None,
    settings: AgentSettings,
    print_system_prompt: bool,
    watch: bool,
) -> int:
    if not task:
        if settings.channel != "cli":
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
        return _run_repl(settings=settings, print_system_prompt=print_system_prompt)
    return asyncio.run(
        _run_once(
            task,
            settings=settings,
            print_system_prompt=print_system_prompt,
        )
    )
