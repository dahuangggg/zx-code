"""profiles — 多模型 Fallback 客户端（供应商无关架构）。

``FallbackModelClient`` 管理一组 ``ModelProfile``，按序尝试：
  - 若当前 profile 成功，直接返回结果
  - 若失败，根据错误类型（rate_limit / auth / billing / timeout）
    将该 profile 放入冷却队列一段时间，然后尝试下一个 profile
  - 所有 profile 都在冷却中时，抛出 ``AllProfilesExhaustedError``

``ProfileManager`` 维护冷却时间表（monotonic clock），
``available_profiles()`` 过滤出当前可用的 profiles。

典型配置：primary=claude-opus-4-6，fallback=gpt-5.4，
限流时自动切换，对上层调用方透明。
"""

from __future__ import annotations


import os
import time
from collections.abc import Callable, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agent.debuglog import DebugLog
from agent.models import Message, ModelTurn
from agent.providers.base import ModelClient, StreamHandler
from agent.providers.openai_client import OpenAIModelClient
from agent.errors import AgentError
from agent.core.recovery import classify_error


class AllProfilesExhaustedError(AgentError):
    """所有模型 profile 都不可用或仍在冷却中。"""

    pass


class ModelProfile(BaseModel):
    """单个模型 profile 配置。"""

    model_config = ConfigDict(extra="forbid")

    name: str
    model: str
    api_key_env: str = ""
    reasoning_effort: str = ""
    extra_kwargs: dict[str, Any] = Field(default_factory=dict)

    def client_kwargs(self) -> dict[str, Any]:
        """返回传给 OpenAIModelClient 的额外参数。"""
        kwargs = dict(self.extra_kwargs)
        if self.reasoning_effort:
            kwargs["reasoning_effort"] = self.reasoning_effort
        return kwargs

    def resolve_api_key(self) -> str | None:
        """从 api_key_env 指定的环境变量读取 API key。"""
        if self.api_key_env:
            return os.getenv(self.api_key_env)
        return None


class ProfileManager:
    """维护 profile 冷却时间表。"""

    def __init__(
        self,
        profiles: Sequence[ModelProfile],
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not profiles:
            raise ValueError("at least one model profile is required")
        self.profiles = list(profiles)
        self.clock = clock
        self._cooldowns: dict[str, float] = {}

    def available_profiles(self) -> list[ModelProfile]:
        """返回当前未处于冷却期的 profile。"""
        now = self.clock()
        return [
            profile
            for profile in self.profiles
            if self._cooldowns.get(profile.name, 0.0) <= now
        ]

    def is_available(self, name: str) -> bool:
        return self._cooldowns.get(name, 0.0) <= self.clock()

    def cooldown(self, name: str, *, seconds: float) -> None:
        self._cooldowns[name] = self.clock() + seconds


ClientFactory = Callable[[ModelProfile], ModelClient]


class FallbackModelClient:
    """按 profile 顺序尝试模型调用，失败时按错误类型冷却并切换。"""

    def __init__(
        self,
        profiles: Sequence[ModelProfile],
        *,
        client_factory: ClientFactory | None = None,
        cooldown_by_kind: dict[str, float] | None = None,
        debug_log: DebugLog | None = None,
    ) -> None:
        self.profile_manager = ProfileManager(profiles)
        self.client_factory = client_factory or _default_client_factory
        self._log = debug_log or DebugLog.null()
        self.cooldown_by_kind = {
            "rate_limit": 120.0,
            "auth": 300.0,
            "billing": 600.0,
            "timeout": 30.0,
            **(cooldown_by_kind or {}),
        }

    async def run_turn(
        self,
        *,
        system_prompt: str,
        messages: Sequence[Message],
        tools: list[dict[str, Any]],
        stream_handler: StreamHandler | None = None,
    ) -> ModelTurn:
        failures: list[str] = []
        profiles = self.profile_manager.available_profiles()
        if not profiles:
            raise AllProfilesExhaustedError("all model profiles are cooling down")

        for profile in profiles:
            client = self.client_factory(profile)
            if isinstance(client, OpenAIModelClient):
                client._log = self._log
            try:
                self._log.event(
                    "model.profile.attempt",
                    {"profile": profile.name, "model": profile.model},
                )
                return await client.run_turn(
                    system_prompt=system_prompt,
                    messages=messages,
                    tools=tools,
                    stream_handler=stream_handler,
                )
            except Exception as exc:
                kind = classify_error(exc)
                self._log.event(
                    "model.profile.failure",
                    {
                        "profile": profile.name,
                        "model": profile.model,
                        "kind": kind,
                        "exception_type": type(exc).__name__,
                        "exception": str(exc),
                    },
                    )
                failures.append(f"{profile.name}:{kind}:{exc}")
                cooldown = self.cooldown_by_kind.get(kind)
                if cooldown is None:
                    raise
                self.profile_manager.cooldown(profile.name, seconds=cooldown)
                continue

        raise AllProfilesExhaustedError(
            "all model profiles exhausted: " + "; ".join(failures)
        )


def _default_client_factory(profile: ModelProfile) -> ModelClient:
    """默认把 ModelProfile 转成 OpenAIModelClient。"""
    return OpenAIModelClient(
        model=profile.model,
        api_key=profile.resolve_api_key(),
        extra_kwargs=profile.client_kwargs(),
    )
