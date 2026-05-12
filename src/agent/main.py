"""main — CLI 入口（typer 命令行应用）。

定义所有命令行参数，通过 ConfigLoader 三层合并构建 AgentSettings，
最终调用 runtime._run_cli() 选择运行模式。

主要参数分组：
  模型配置    — --model, --fallback-models, --max-turns, --compact-model
  上下文管理  — --context-max-tokens
  通道选择    — --channel, --watch
  功能开关    — --no-memory, --no-todos, --no-tasks, --no-skills, --no-subagents
  外部通道    — --telegram-token, --feishu-*, --heartbeat-*, --cron-*
  会话        — --resume / --session-id
  调试        — --print-system-prompt / --debug-log
  任务输入    — 最后的可选位置参数

运行入口：``agent`` CLI 命令（pyproject.toml scripts 定义）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import typer

from agent.config import AgentSettings, ConfigLoader
from agent.runtime import _run_cli, console


def _settings_from_cli(
    *,
    model: str | None,
    fallback_models: str | None,
    max_turns: int | None,
    session_id: str | None,
    data_dir: str | None,
    context_max_tokens: int | None,
    compact_model: str | None,
    skills_dir: str | None,
    tasks_dir: str | None,
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
    delivery_daemon_interval: float | None,
    heartbeat_enabled: bool,
    heartbeat_interval: float | None,
    heartbeat_min_idle: float | None,
    heartbeat_channel: str | None,
    heartbeat_to: str | None,
    heartbeat_prompt: str | None,
    heartbeat_sentinel: str | None,
    cron_jobs_path: str | None,
    subagent_max_depth: int | None,
    worktree_dir: str | None,
    no_stream: bool,
    no_memory: bool,
    no_skills: bool,
    no_todos: bool,
    no_tasks: bool,
    no_subagents: bool,
    worktree_isolation: bool,
    debug_log: bool,
    debug_log_path: str | None,
) -> AgentSettings:
    overrides: dict[str, Any] = {
        "model": model,
        "fallback_models": fallback_models,
        "max_iterations": max_turns,
        "session_id": session_id,
        "data_dir": data_dir,
        "context_max_tokens": context_max_tokens,
        "compact_model": compact_model,
        "skills_dir": skills_dir,
        "tasks_dir": tasks_dir,
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
        "delivery_daemon_interval_s": delivery_daemon_interval,
        "heartbeat_interval_s": heartbeat_interval,
        "heartbeat_min_idle_s": heartbeat_min_idle,
        "heartbeat_channel": heartbeat_channel,
        "heartbeat_to": heartbeat_to,
        "heartbeat_prompt": heartbeat_prompt,
        "heartbeat_sentinel": heartbeat_sentinel,
        "cron_jobs_path": cron_jobs_path,
        "subagent_max_depth": subagent_max_depth,
        "worktree_dir": worktree_dir,
        "debug_log_path": debug_log_path,
    }
    if feishu_is_lark:
        overrides["feishu_is_lark"] = True
    if heartbeat_enabled:
        overrides["heartbeat_enabled"] = True
    if no_stream:
        overrides["stream"] = False
    if no_memory:
        overrides["enable_memory"] = False
    if no_skills:
        overrides["enable_skills"] = False
    if no_todos:
        overrides["enable_todos"] = False
    if no_tasks:
        overrides["enable_tasks"] = False
    if no_subagents:
        overrides["enable_subagents"] = False
    if worktree_isolation:
        overrides["enable_worktree_isolation"] = True
    if debug_log:
        overrides["debug_log_enabled"] = True
    return ConfigLoader(project_dir=Path.cwd()).load(overrides)


def _build_typer_app() -> Any:
    app = typer.Typer(add_completion=False, pretty_exceptions_show_locals=False)

    @app.command()
    def cli(
        task: list[str] | None = typer.Argument(None),
        model: str | None = typer.Option(None),
        fallback_models: str | None = typer.Option(None, "--fallback-models"),
        max_turns: int | None = typer.Option(None, "--max-turns"),
        resume: str | None = typer.Option(None, "--resume"),
        session_id: str | None = typer.Option(None, "--session-id"),
        data_dir: str | None = typer.Option(None, "--data-dir"),
        context_max_tokens: int | None = typer.Option(None, "--context-max-tokens"),
        compact_model: str | None = typer.Option(None, "--compact-model"),
        skills_dir: str | None = typer.Option(None, "--skills-dir"),
        tasks_dir: str | None = typer.Option(None, "--tasks-dir"),
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
        delivery_daemon_interval: float | None = typer.Option(
            None,
            "--delivery-daemon-interval",
        ),
        heartbeat_enabled: bool = typer.Option(False, "--heartbeat"),
        heartbeat_interval: float | None = typer.Option(None, "--heartbeat-interval"),
        heartbeat_min_idle: float | None = typer.Option(None, "--heartbeat-min-idle"),
        heartbeat_channel: str | None = typer.Option(None, "--heartbeat-channel"),
        heartbeat_to: str | None = typer.Option(None, "--heartbeat-to"),
        heartbeat_prompt: str | None = typer.Option(None, "--heartbeat-prompt"),
        heartbeat_sentinel: str | None = typer.Option(None, "--heartbeat-sentinel"),
        cron_jobs_path: str | None = typer.Option(None, "--cron-jobs"),
        subagent_max_depth: int | None = typer.Option(None, "--subagent-max-depth"),
        worktree_dir: str | None = typer.Option(None, "--worktree-dir"),
        watch: bool = typer.Option(False, "--watch"),
        no_stream: bool = typer.Option(False, "--no-stream"),
        no_memory: bool = typer.Option(False, "--no-memory"),
        no_skills: bool = typer.Option(False, "--no-skills"),
        no_todos: bool = typer.Option(False, "--no-todos"),
        no_tasks: bool = typer.Option(False, "--no-tasks"),
        no_subagents: bool = typer.Option(False, "--no-subagents"),
        worktree_isolation: bool = typer.Option(False, "--worktree-isolation"),
        debug_log: bool = typer.Option(False, "--debug-log"),
        debug_log_path: str | None = typer.Option(None, "--debug-log-path"),
        print_system_prompt: bool = typer.Option(False, "--print-system-prompt"),
    ) -> None:
        settings = _settings_from_cli(
            model=model,
            fallback_models=fallback_models,
            max_turns=max_turns,
            session_id=session_id,
            data_dir=data_dir,
            context_max_tokens=context_max_tokens,
            compact_model=compact_model,
            skills_dir=skills_dir,
            tasks_dir=tasks_dir,
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
            delivery_daemon_interval=delivery_daemon_interval,
            heartbeat_enabled=heartbeat_enabled,
            heartbeat_interval=heartbeat_interval,
            heartbeat_min_idle=heartbeat_min_idle,
            heartbeat_channel=heartbeat_channel,
            heartbeat_to=heartbeat_to,
            heartbeat_prompt=heartbeat_prompt,
            heartbeat_sentinel=heartbeat_sentinel,
            cron_jobs_path=cron_jobs_path,
            subagent_max_depth=subagent_max_depth,
            worktree_dir=worktree_dir,
            no_stream=no_stream,
            no_memory=no_memory,
            no_skills=no_skills,
            no_todos=no_todos,
            no_tasks=no_tasks,
            no_subagents=no_subagents,
            worktree_isolation=worktree_isolation,
            debug_log=debug_log,
            debug_log_path=debug_log_path,
        )
        exit_code = _run_cli(
            task=" ".join(task).strip() if task else None,
            settings=settings,
            print_system_prompt=print_system_prompt,
            watch=watch,
            resume_session_id=resume or session_id,
        )
        raise typer.Exit(exit_code)

    return app


def main() -> int | None:
    app = _build_typer_app()
    app()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
