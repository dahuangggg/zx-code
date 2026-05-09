"""agent.channels — 外部消息通道与路由网关。

模块说明：
  base.py     — 抽象基类 ``ChannelManager`` 和 ``InboundMessage`` 数据模型
  cli.py      — CLI 通道（stdin/stdout，用于本地交互）
  telegram.py — Telegram Bot 通道（长轮询 + webhook）
  feishu.py   — 飞书机器人通道（webhook 接收）
  delivery.py — ``DeliveryQueue`` / ``DeliveryRunner``：带重试的异步消息投递队列
  gateway.py  — ``Gateway``：统一入口，路由消息到正确的 agent，协调 ActivityTracker
"""
from agent.channels.base import Channel, ChannelManager, InboundMessage, OutboundMessage
from agent.channels.cli import CLIChannel
from agent.channels.feishu import FeishuChannel
from agent.channels.telegram import TelegramChannel

__all__ = [
    "Channel",
    "ChannelManager",
    "CLIChannel",
    "FeishuChannel",
    "InboundMessage",
    "OutboundMessage",
    "TelegramChannel",
]

