from __future__ import annotations

import os
import time
from collections.abc import Callable, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agent.models import Message, ModelTurn
from agent.providers.base import ModelClient, StreamHandler
from agent.providers.litellm_client import LiteLLMModelClient
from agent.recovery import AgentError, classify_error


class AllProfilesExhaustedError(AgentError):
    pass


class ModelProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    model: str
    api_key_env: str = ""
    extra_kwargs: dict[str, Any] = Field(default_factory=dict)

    def litellm_kwargs(self) -> dict[str, Any]:
        kwargs = dict(self.extra_kwargs)
        if self.api_key_env:
            api_key = os.getenv(self.api_key_env)
            if api_key:
                kwargs["api_key"] = api_key
        return kwargs


class ProfileManager:
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
    def __init__(
        self,
        profiles: Sequence[ModelProfile],
        *,
        client_factory: ClientFactory | None = None,
        cooldown_by_kind: dict[str, float] | None = None,
    ) -> None:
        self.profile_manager = ProfileManager(profiles)
        self.client_factory = client_factory or _default_client_factory
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
            try:
                return await client.run_turn(
                    system_prompt=system_prompt,
                    messages=messages,
                    tools=tools,
                    stream_handler=stream_handler,
                )
            except Exception as exc:
                kind = classify_error(exc)
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
    return LiteLLMModelClient(
        model=profile.model,
        extra_kwargs=profile.litellm_kwargs(),
    )
