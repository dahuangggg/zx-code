from __future__ import annotations

from typing import Any

import pytest

from agent.models import Message, ModelTurn
from agent.profiles import FallbackModelClient, ModelProfile


class _FakeClient:
    def __init__(
        self,
        profile_name: str,
        calls: list[str],
        result: ModelTurn | Exception,
    ) -> None:
        self.profile_name = profile_name
        self.calls = calls
        self.result = result

    async def run_turn(
        self,
        *,
        system_prompt: str,
        messages: list[Message],
        tools: list[dict[str, Any]],
        stream_handler=None,
    ) -> ModelTurn:
        self.calls.append(self.profile_name)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


async def test_fallback_model_client_uses_next_profile_after_rate_limit() -> None:
    calls: list[str] = []
    profiles = [
        ModelProfile(name="primary", model="openai/primary"),
        ModelProfile(name="backup", model="openai/backup"),
    ]

    def client_factory(profile: ModelProfile) -> _FakeClient:
        if profile.name == "primary":
            return _FakeClient(profile.name, calls, RuntimeError("rate limit 429"))
        return _FakeClient(profile.name, calls, ModelTurn(text="backup answer"))

    client = FallbackModelClient(
        profiles,
        client_factory=client_factory,
        cooldown_by_kind={"rate_limit": 10.0},
    )

    turn = await client.run_turn(
        system_prompt="system",
        messages=[Message.user("hello")],
        tools=[],
    )

    assert turn.text == "backup answer"
    assert calls == ["primary", "backup"]
    assert not client.profile_manager.is_available("primary")


async def test_fallback_model_client_does_not_fallback_on_unknown_error() -> None:
    calls: list[str] = []
    profiles = [
        ModelProfile(name="primary", model="openai/primary"),
        ModelProfile(name="backup", model="openai/backup"),
    ]

    def client_factory(profile: ModelProfile) -> _FakeClient:
        if profile.name == "primary":
            return _FakeClient(profile.name, calls, ValueError("broken parser"))
        return _FakeClient(profile.name, calls, ModelTurn(text="should not run"))

    client = FallbackModelClient(profiles, client_factory=client_factory)

    with pytest.raises(ValueError, match="broken parser"):
        await client.run_turn(
            system_prompt="system",
            messages=[Message.user("hello")],
            tools=[],
        )

    assert calls == ["primary"]


def test_model_profile_reads_api_key_from_env(monkeypatch) -> None:
    monkeypatch.setenv("ZX_TEST_MODEL_KEY", "secret-key")
    profile = ModelProfile(
        name="primary",
        model="openai/gpt-4o-mini",
        api_key_env="ZX_TEST_MODEL_KEY",
        reasoning_effort="medium",
        extra_kwargs={"base_url": "https://example.invalid"},
    )

    assert profile.litellm_kwargs() == {
        "api_key": "secret-key",
        "base_url": "https://example.invalid",
        "reasoning_effort": "medium",
    }
