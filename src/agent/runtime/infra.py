"""runtime.infra — 基础设施构建函数（Gateway / Heartbeat / Cron）。

此模块负责将 AgentSettings 中与外部通道和调度相关的配置
"装配"成可运行的对象，供 runner.py 使用。

三个构建函数：
  _build_gateway()         — 注册所有通道（CLI/Telegram/Feishu）、创建 DeliveryQueue/Runner、
                             ActivityTracker、BindingTable，最终返回 Gateway
  _build_heartbeat_runner()— 若 heartbeat_enabled=True 且配置了 channel/to，
                             构建 HeartbeatRunner 并绑定到 "heartbeat" 泳道
  _build_cron_scheduler()  — 若存在 cron.json 配置文件，从文件加载 CronJob 列表，
                             构建 CronScheduler 并绑定到 "cron" 泳道

所有 agent turn 调用均通过 LaneScheduler 排队，防止与主对话抢占 LLM。
""" 

from __future__ import annotations


from pathlib import Path
from typing import Any

from agent.scheduling.activity import ActivityTracker
from agent.channels import ChannelManager, CLIChannel, FeishuChannel, InboundMessage
from agent.channels.telegram import TelegramChannel
from agent.config import AgentSettings
from agent.scheduling.cron import CronScheduler
from agent.channels.delivery import DeliveryQueue, DeliveryRunner
from agent.channels.gateway import AgentRouteConfig, BindingTable, Gateway
from agent.scheduling.heartbeat import HeartbeatConfig, HeartbeatRunner
from agent.scheduling.lanes import LaneScheduler
from agent.runtime.builder import _run_agent_text
from agent.runtime.utils import _resolve_project_path, console


def _build_gateway(
    settings: AgentSettings,
    *,
    emit_cli: bool,
    lane_scheduler: LaneScheduler | None = None,
) -> Gateway:
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
        async def execute() -> str:
            return await _run_agent_text(
                inbound.text,
                settings=settings.model_copy(update={"agent_id": agent_id}),
                session_id=session_id,
                lane_scheduler=lane_scheduler,
            )

        if lane_scheduler is None:
            return await execute()
        return await lane_scheduler.run("main", execute, job_id=session_id)

    return Gateway(
        channel_manager=channel_manager,
        binding_table=binding_table,
        agent_configs=agent_configs,
        run_agent_turn=run_agent_turn,
        delivery_queue=delivery_queue,
        delivery_runner=delivery_runner,
        activity_tracker=activity_tracker,
    )


def _build_heartbeat_runner(
    settings: AgentSettings,
    gateway: Gateway,
    *,
    lane_scheduler: LaneScheduler | None = None,
) -> HeartbeatRunner | None:
    if not settings.heartbeat_enabled or gateway.delivery_queue is None:
        return None
    channel = settings.heartbeat_channel or settings.channel
    to = settings.heartbeat_to
    if not channel or not to:
        return None

    async def run_agent_turn(prompt: str, session_id: str) -> str:
        async def execute() -> str:
            return await _run_agent_text(
                prompt,
                settings=settings.model_copy(update={"stream": False}),
                session_id=session_id,
                lane_scheduler=lane_scheduler,
            )

        if lane_scheduler is None:
            return await execute()
        return await lane_scheduler.run("heartbeat", execute, job_id=session_id)

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


def _build_cron_scheduler(
    settings: AgentSettings,
    gateway: Gateway,
    *,
    lane_scheduler: LaneScheduler | None = None,
) -> CronScheduler | None:
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
        async def execute() -> str:
            return await _run_agent_text(
                prompt,
                settings=settings.model_copy(update={"stream": False}),
                session_id=session_id,
                lane_scheduler=lane_scheduler,
            )

        if lane_scheduler is None:
            return await execute()
        return await lane_scheduler.run("cron", execute, job_id=session_id)

    data_dir = _resolve_project_path(project_root, settings.data_dir)
    return CronScheduler.from_file(
        cron_path,
        delivery_queue=gateway.delivery_queue,
        run_agent_turn=run_agent_turn,
        state_path=data_dir / "cron-state.json",
    )
