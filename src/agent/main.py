from __future__ import annotations

import asyncio
import contextlib
import io
from pathlib import Path
from typing import Any

from rich.console import Console

from agent.channels import ChannelManager, CLIChannel, FeishuChannel, InboundMessage
from agent.channels.telegram import TelegramChannel
from agent.config import AgentSettings, ConfigLoader
from agent.context import ContextGuard
from agent.cron import CronScheduler
from agent.delivery import DeliveryQueue, DeliveryRunner
from agent.gateway import AgentRouteConfig, BindingTable, Gateway
from agent.heartbeat import ActivityTracker, HeartbeatConfig, HeartbeatRunner
from agent.hooks import HookRunner
from agent.loop import run_task
from agent.memory import MemoryStore
from agent.permissions import PermissionCheck, PermissionManager
from agent.prompt import SystemPromptBuilder
from agent.providers.litellm_client import LiteLLMModelClient
from agent.recovery import AgentError
from agent.sessions import SessionStore, safe_session_id
from agent.todo import TodoManager
from agent.tools import build_default_registry

import typer

console = Console()


def _configure_readline() -> None:
    try:
        import readline
    except ImportError:
        return

    # Improve UTF-8 input editing in REPLs. Different readline backends
    # support slightly different settings, so apply defensively.
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


def _build_runtime(settings: AgentSettings, *, session_id: str | None = None) -> dict[str, Any]:
    effective_settings = (
        settings.model_copy(update={"session_id": session_id})
        if session_id is not None
        else settings
    )
    project_root = Path.cwd()
    data_dir = _resolve_project_path(project_root, effective_settings.data_dir)
    memory_store = (
        MemoryStore(_resolve_project_path(project_root, effective_settings.memory_path))
        if effective_settings.enable_memory
        else None
    )
    if memory_store is not None:
        memory_store.ensure()

    todo_manager = (
        TodoManager(data_dir / "todos" / f"{safe_session_id(effective_settings.session_id)}.json")
        if effective_settings.enable_todos
        else None
    )
    prompt_builder = SystemPromptBuilder(
        project_root=project_root,
        memory_store=memory_store,
        todo_manager=todo_manager,
    )
    system_prompt = prompt_builder.build(effective_settings.to_agent_config())
    config = effective_settings.to_agent_config(system_prompt=system_prompt)
    # Resolve rules file: explicit setting > project default
    rules_path: Path | None = None
    if effective_settings.permission_rules_path:
        rules_path = _resolve_project_path(project_root, effective_settings.permission_rules_path)
    else:
        default_rules = project_root / ".zx-code" / "permissions.toml"
        if default_rules.exists():
            rules_path = default_rules
    permission_manager = PermissionManager.from_rules_file(
        rules_path or "",
        tool_policies=effective_settings.permission_tools,
        default_decision=effective_settings.permission_default,
    ) if rules_path else PermissionManager(
        tool_policies=effective_settings.permission_tools,
        default_decision=effective_settings.permission_default,
    )
    registry = build_default_registry(
        permission_manager=permission_manager,
        approval_callback=_approval_prompt,
        todo_manager=todo_manager,
        memory_store=memory_store,
    )
    # Resolve hooks file: explicit setting > project default
    hooks_path: Path | None = None
    if effective_settings.hooks_path:
        hooks_path = _resolve_project_path(project_root, effective_settings.hooks_path)
    else:
        default_hooks = project_root / ".zx-code" / "hooks.toml"
        if default_hooks.exists():
            hooks_path = default_hooks
    hook_runner = HookRunner.from_file(hooks_path) if hooks_path else HookRunner.empty()

    return {
        "config": config,
        "hook_runner": hook_runner,
        "context_guard": ContextGuard(
            max_tokens=effective_settings.context_max_tokens,
            keep_recent=effective_settings.context_keep_recent,
            tool_result_max_chars=effective_settings.context_tool_result_max_chars,
            compact_model=effective_settings.compact_model,
            model=effective_settings.model,
        ),
        "model_client": LiteLLMModelClient(model=effective_settings.model),
        "prompt_builder": prompt_builder,
        "session_store": SessionStore(data_dir / "sessions"),
        "tool_registry": registry,
    }


async def _run_agent_text(
    task: str,
    *,
    settings: AgentSettings,
    session_id: str | None = None,
) -> str:
    runtime = _build_runtime(settings, session_id=session_id)

    try:
        result = await run_task(
            task,
            model_client=runtime["model_client"],
            tool_registry=runtime["tool_registry"],
            config=runtime["config"],
            stream_handler=_stream_printer if settings.stream else None,
            session_store=runtime["session_store"],
            context_guard=runtime["context_guard"],
            prompt_builder=runtime["prompt_builder"],
            hook_runner=runtime["hook_runner"],
        )
    except AgentError as exc:
        raise exc

    if settings.stream and result.final_text:
        console.print()
    return result.final_text


def _build_gateway(settings: AgentSettings, *, emit_cli: bool) -> Gateway:
    channel_manager = ChannelManager()
    project_root = Path.cwd()
    data_dir = _resolve_project_path(project_root, settings.data_dir)
    channel_manager.register(
        CLIChannel(
            account_id=settings.account_id,
            emit=emit_cli,
            writer=console.print,
        )
    )
    if settings.telegram_token:
        channel_manager.register(
            TelegramChannel(
                token=settings.telegram_token,
                account_id=settings.account_id,
                offset=settings.telegram_offset,
                timeout_s=settings.telegram_timeout_s,
                state_dir=data_dir / "channels",
                allowed_chats=settings.telegram_allowed_chats,
                text_coalesce_s=settings.telegram_text_coalesce_s,
                media_group_coalesce_s=settings.telegram_media_group_coalesce_s,
            )
        )
    channel_manager.register(
        FeishuChannel(
            app_id=settings.feishu_app_id,
            app_secret=settings.feishu_app_secret,
            account_id=settings.account_id,
            verification_token=settings.feishu_verification_token,
            encrypt_key=settings.feishu_encrypt_key,
            bot_open_id=settings.feishu_bot_open_id,
            is_lark=settings.feishu_is_lark,
            webhook_host=settings.feishu_webhook_host,
            webhook_port=settings.feishu_webhook_port,
            receive_timeout_s=settings.feishu_receive_timeout_s,
        )
    )
    delivery_queue = DeliveryQueue(
        data_dir / "delivery",
        max_attempts=settings.delivery_max_attempts,
        base_delay_s=settings.delivery_base_delay_s,
        max_delay_s=settings.delivery_max_delay_s,
        jitter_s=settings.delivery_jitter_s,
    )
    delivery_runner = DeliveryRunner(
        queue=delivery_queue,
        channel_manager=channel_manager,
    )
    activity_tracker = ActivityTracker()

    binding_table = BindingTable(default_agent_id=settings.default_agent_id)
    agent_configs = {
        settings.agent_id: AgentRouteConfig(
            agent_id=settings.agent_id,
            dm_scope=settings.dm_scope,
        ),
        settings.default_agent_id: AgentRouteConfig(
            agent_id=settings.default_agent_id,
            dm_scope=settings.dm_scope,
        ),
    }

    async def run_agent_turn(
        inbound: InboundMessage,
        agent_id: str,
        session_id: str,
    ) -> str:
        return await _run_agent_text(
            inbound.text,
            settings=settings.model_copy(update={"agent_id": agent_id}),
            session_id=session_id,
        )

    return Gateway(
        channel_manager=channel_manager,
        binding_table=binding_table,
        agent_configs=agent_configs,
        run_agent_turn=run_agent_turn,
        delivery_queue=delivery_queue,
        delivery_runner=delivery_runner,
        activity_tracker=activity_tracker,
    )


def _build_heartbeat_runner(settings: AgentSettings, gateway: Gateway) -> HeartbeatRunner | None:
    if not settings.heartbeat_enabled or gateway.delivery_queue is None:
        return None
    channel = settings.heartbeat_channel or settings.channel
    to = settings.heartbeat_to
    if not channel or not to:
        return None

    async def run_agent_turn(prompt: str, session_id: str) -> str:
        return await _run_agent_text(
            prompt,
            settings=settings.model_copy(update={"stream": False}),
            session_id=session_id,
        )

    return HeartbeatRunner(
        config=HeartbeatConfig(
            enabled=settings.heartbeat_enabled,
            interval_s=settings.heartbeat_interval_s,
            min_idle_s=settings.heartbeat_min_idle_s,
            channel=channel,
            to=to,
            account_id=settings.account_id,
            prompt=settings.heartbeat_prompt,
            sentinel=settings.heartbeat_sentinel,
        ),
        delivery_queue=gateway.delivery_queue,
        run_agent_turn=run_agent_turn,
        activity_tracker=gateway.activity_tracker,
    )


def _build_cron_scheduler(settings: AgentSettings, gateway: Gateway) -> CronScheduler | None:
    if gateway.delivery_queue is None:
        return None

    project_root = Path.cwd()
    cron_path: Path | None = None
    if settings.cron_jobs_path:
        cron_path = _resolve_project_path(project_root, settings.cron_jobs_path)
    else:
        default_path = project_root / ".zx-code" / "cron.json"
        if default_path.exists():
            cron_path = default_path
    if cron_path is None or not cron_path.exists():
        return None

    async def run_agent_turn(prompt: str, session_id: str) -> str:
        return await _run_agent_text(
            prompt,
            settings=settings.model_copy(update={"stream": False}),
            session_id=session_id,
        )

    return CronScheduler.from_file(
        cron_path,
        delivery_queue=gateway.delivery_queue,
        run_agent_turn=run_agent_turn,
    )


def _validate_channel_settings(settings: AgentSettings) -> bool:
    if settings.channel == "telegram" and not settings.telegram_token:
        console.print("[red]Error:[/red] --telegram-token is required for Telegram")
        return False
    if settings.channel == "feishu":
        if not settings.feishu_app_id or not settings.feishu_app_secret:
            console.print(
                "[red]Error:[/red] --feishu-app-id and --feishu-app-secret are required for Feishu"
            )
            return False
        if settings.feishu_webhook_port <= 0:
            console.print("[red]Error:[/red] --feishu-webhook-port is required for Feishu")
            return False
    if settings.heartbeat_enabled and not settings.heartbeat_to:
        console.print("[red]Error:[/red] --heartbeat-to is required when heartbeat is enabled")
        return False
    return True


async def _run_once(
    task: str,
    *,
    settings: AgentSettings,
    print_system_prompt: bool,
) -> int:
    if print_system_prompt:
        runtime = _build_runtime(settings)
        console.print(runtime["prompt_builder"].debug(runtime["config"]))
        return 0

    gateway = _build_gateway(settings, emit_cli=not settings.stream)
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
    return 0


async def _run_channel_once(
    *,
    settings: AgentSettings,
    print_system_prompt: bool,
) -> int:
    runtime = _build_runtime(settings)

    if print_system_prompt:
        console.print(runtime["prompt_builder"].debug(runtime["config"]))
        return 0

    if not _validate_channel_settings(settings):
        return 1

    gateway = _build_gateway(settings, emit_cli=not settings.stream)
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
        console.print(runtime["prompt_builder"].debug(runtime["config"]))
        return 0

    if not _validate_channel_settings(settings):
        return 1

    gateway = _build_gateway(settings, emit_cli=not settings.stream)
    heartbeat_runner = _build_heartbeat_runner(settings, gateway)
    cron_scheduler = _build_cron_scheduler(settings, gateway)
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
                    await heartbeat_runner.tick()
                if cron_scheduler is not None:
                    await cron_scheduler.tick()
                await gateway.drain_delivery()
            except AgentError as exc:
                console.print(f"[red]Error:[/red] {exc}")
                return 1
            except KeyError as exc:
                console.print(f"[red]Error:[/red] {exc}")
                return 1
    except KeyboardInterrupt:
        console.print()
        return 0


def _run_repl(*, settings: AgentSettings, print_system_prompt: bool) -> int:
    _configure_readline()
    if print_system_prompt:
        runtime = _build_runtime(settings)
        console.print(runtime["prompt_builder"].debug(runtime["config"]))
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



def _settings_from_cli(
    *,
    model: str | None,
    max_turns: int | None,
    session_id: str | None,
    data_dir: str | None,
    context_max_tokens: int | None,
    compact_model: str | None,
    channel: str | None,
    account_id: str | None,
    agent_id: str | None,
    default_agent_id: str | None,
    force_agent_id: str | None,
    dm_scope: str | None,
    telegram_token: str | None,
    telegram_offset: int | None,
    telegram_timeout: int | None,
    telegram_allowed_chats: str | None,
    telegram_text_coalesce: float | None,
    telegram_media_group_coalesce: float | None,
    feishu_app_id: str | None,
    feishu_app_secret: str | None,
    feishu_verification_token: str | None,
    feishu_encrypt_key: str | None,
    feishu_bot_open_id: str | None,
    feishu_is_lark: bool,
    feishu_webhook_host: str | None,
    feishu_webhook_port: int | None,
    feishu_receive_timeout: float | None,
    delivery_max_attempts: int | None,
    delivery_base_delay: float | None,
    delivery_max_delay: float | None,
    delivery_jitter: float | None,
    heartbeat_enabled: bool,
    heartbeat_interval: float | None,
    heartbeat_min_idle: float | None,
    heartbeat_channel: str | None,
    heartbeat_to: str | None,
    heartbeat_prompt: str | None,
    heartbeat_sentinel: str | None,
    cron_jobs_path: str | None,
    no_stream: bool,
    no_memory: bool,
    no_todos: bool,
) -> AgentSettings:
    overrides: dict[str, Any] = {
        "model": model,
        "max_iterations": max_turns,
        "session_id": session_id,
        "data_dir": data_dir,
        "context_max_tokens": context_max_tokens,
        "compact_model": compact_model,
        "channel": channel,
        "account_id": account_id,
        "agent_id": agent_id,
        "default_agent_id": default_agent_id,
        "force_agent_id": force_agent_id,
        "dm_scope": dm_scope,
        "telegram_token": telegram_token,
        "telegram_offset": telegram_offset,
        "telegram_timeout_s": telegram_timeout,
        "telegram_allowed_chats": telegram_allowed_chats,
        "telegram_text_coalesce_s": telegram_text_coalesce,
        "telegram_media_group_coalesce_s": telegram_media_group_coalesce,
        "feishu_app_id": feishu_app_id,
        "feishu_app_secret": feishu_app_secret,
        "feishu_verification_token": feishu_verification_token,
        "feishu_encrypt_key": feishu_encrypt_key,
        "feishu_bot_open_id": feishu_bot_open_id,
        "feishu_webhook_host": feishu_webhook_host,
        "feishu_webhook_port": feishu_webhook_port,
        "feishu_receive_timeout_s": feishu_receive_timeout,
        "delivery_max_attempts": delivery_max_attempts,
        "delivery_base_delay_s": delivery_base_delay,
        "delivery_max_delay_s": delivery_max_delay,
        "delivery_jitter_s": delivery_jitter,
        "heartbeat_interval_s": heartbeat_interval,
        "heartbeat_min_idle_s": heartbeat_min_idle,
        "heartbeat_channel": heartbeat_channel,
        "heartbeat_to": heartbeat_to,
        "heartbeat_prompt": heartbeat_prompt,
        "heartbeat_sentinel": heartbeat_sentinel,
        "cron_jobs_path": cron_jobs_path,
    }
    if feishu_is_lark:
        overrides["feishu_is_lark"] = True
    if heartbeat_enabled:
        overrides["heartbeat_enabled"] = True
    if no_stream:
        overrides["stream"] = False
    if no_memory:
        overrides["enable_memory"] = False
    if no_todos:
        overrides["enable_todos"] = False
    return ConfigLoader(project_dir=Path.cwd()).load(overrides)


def _build_typer_app() -> Any:
    app = typer.Typer(add_completion=False, pretty_exceptions_show_locals=False)

    @app.command()
    def cli(
        task: list[str] | None = typer.Argument(None),
        model: str | None = typer.Option(None),
        max_turns: int | None = typer.Option(None, "--max-turns"),
        session_id: str | None = typer.Option(None, "--session-id"),
        data_dir: str | None = typer.Option(None, "--data-dir"),
        context_max_tokens: int | None = typer.Option(None, "--context-max-tokens"),
        compact_model: str | None = typer.Option(None, "--compact-model"),
        channel: str | None = typer.Option(None, "--channel"),
        account_id: str | None = typer.Option(None, "--account-id"),
        agent_id: str | None = typer.Option(None, "--agent-id"),
        default_agent_id: str | None = typer.Option(None, "--default-agent-id"),
        force_agent_id: str | None = typer.Option(None, "--force-agent-id"),
        dm_scope: str | None = typer.Option(None, "--dm-scope"),
        telegram_token: str | None = typer.Option(None, "--telegram-token"),
        telegram_offset: int | None = typer.Option(None, "--telegram-offset"),
        telegram_timeout: int | None = typer.Option(None, "--telegram-timeout"),
        telegram_allowed_chats: str | None = typer.Option(None, "--telegram-allowed-chats"),
        telegram_text_coalesce: float | None = typer.Option(None, "--telegram-text-coalesce"),
        telegram_media_group_coalesce: float | None = typer.Option(
            None,
            "--telegram-media-group-coalesce",
        ),
        feishu_app_id: str | None = typer.Option(None, "--feishu-app-id"),
        feishu_app_secret: str | None = typer.Option(None, "--feishu-app-secret"),
        feishu_verification_token: str | None = typer.Option(
            None,
            "--feishu-verification-token",
        ),
        feishu_encrypt_key: str | None = typer.Option(None, "--feishu-encrypt-key"),
        feishu_bot_open_id: str | None = typer.Option(None, "--feishu-bot-open-id"),
        feishu_is_lark: bool = typer.Option(False, "--feishu-is-lark"),
        feishu_webhook_host: str | None = typer.Option(None, "--feishu-webhook-host"),
        feishu_webhook_port: int | None = typer.Option(None, "--feishu-webhook-port"),
        feishu_receive_timeout: float | None = typer.Option(None, "--feishu-receive-timeout"),
        delivery_max_attempts: int | None = typer.Option(None, "--delivery-max-attempts"),
        delivery_base_delay: float | None = typer.Option(None, "--delivery-base-delay"),
        delivery_max_delay: float | None = typer.Option(None, "--delivery-max-delay"),
        delivery_jitter: float | None = typer.Option(None, "--delivery-jitter"),
        heartbeat_enabled: bool = typer.Option(False, "--heartbeat"),
        heartbeat_interval: float | None = typer.Option(None, "--heartbeat-interval"),
        heartbeat_min_idle: float | None = typer.Option(None, "--heartbeat-min-idle"),
        heartbeat_channel: str | None = typer.Option(None, "--heartbeat-channel"),
        heartbeat_to: str | None = typer.Option(None, "--heartbeat-to"),
        heartbeat_prompt: str | None = typer.Option(None, "--heartbeat-prompt"),
        heartbeat_sentinel: str | None = typer.Option(None, "--heartbeat-sentinel"),
        cron_jobs_path: str | None = typer.Option(None, "--cron-jobs"),
        watch: bool = typer.Option(False, "--watch"),
        no_stream: bool = typer.Option(False, "--no-stream"),
        no_memory: bool = typer.Option(False, "--no-memory"),
        no_todos: bool = typer.Option(False, "--no-todos"),
        print_system_prompt: bool = typer.Option(False, "--print-system-prompt"),
    ) -> None:
        settings = _settings_from_cli(
            model=model,
            max_turns=max_turns,
            session_id=session_id,
            data_dir=data_dir,
            context_max_tokens=context_max_tokens,
            compact_model=compact_model,
            channel=channel,
            account_id=account_id,
            agent_id=agent_id,
            default_agent_id=default_agent_id,
            force_agent_id=force_agent_id,
            dm_scope=dm_scope,
            telegram_token=telegram_token,
            telegram_offset=telegram_offset,
            telegram_timeout=telegram_timeout,
            telegram_allowed_chats=telegram_allowed_chats,
            telegram_text_coalesce=telegram_text_coalesce,
            telegram_media_group_coalesce=telegram_media_group_coalesce,
            feishu_app_id=feishu_app_id,
            feishu_app_secret=feishu_app_secret,
            feishu_verification_token=feishu_verification_token,
            feishu_encrypt_key=feishu_encrypt_key,
            feishu_bot_open_id=feishu_bot_open_id,
            feishu_is_lark=feishu_is_lark,
            feishu_webhook_host=feishu_webhook_host,
            feishu_webhook_port=feishu_webhook_port,
            feishu_receive_timeout=feishu_receive_timeout,
            delivery_max_attempts=delivery_max_attempts,
            delivery_base_delay=delivery_base_delay,
            delivery_max_delay=delivery_max_delay,
            delivery_jitter=delivery_jitter,
            heartbeat_enabled=heartbeat_enabled,
            heartbeat_interval=heartbeat_interval,
            heartbeat_min_idle=heartbeat_min_idle,
            heartbeat_channel=heartbeat_channel,
            heartbeat_to=heartbeat_to,
            heartbeat_prompt=heartbeat_prompt,
            heartbeat_sentinel=heartbeat_sentinel,
            cron_jobs_path=cron_jobs_path,
            no_stream=no_stream,
            no_memory=no_memory,
            no_todos=no_todos,
        )
        exit_code = _run_cli(
            task=" ".join(task).strip() if task else None,
            settings=settings,
            print_system_prompt=print_system_prompt,
            watch=watch,
        )
        raise typer.Exit(exit_code)

    return app


def main() -> int | None:
    app = _build_typer_app()
    app()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
