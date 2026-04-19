from __future__ import annotations

import sys
from types import SimpleNamespace

from agent.config import AgentSettings
from agent import main
from agent.profiles import FallbackModelClient, ModelProfile


def test_configure_readline_applies_bindings(monkeypatch) -> None:
    bindings: list[str] = []

    fake_readline = SimpleNamespace(
        parse_and_bind=bindings.append,
    )
    monkeypatch.setitem(sys.modules, "readline", fake_readline)

    main._configure_readline()

    assert bindings == [
        "set bind-tty-special-chars off",
        "set input-meta on",
        "set output-meta on",
        "set convert-meta off",
    ]


def test_configure_readline_ignores_backend_errors(monkeypatch) -> None:
    calls: list[str] = []

    def parse_and_bind(binding: str) -> None:
        calls.append(binding)
        raise RuntimeError("unsupported")

    fake_readline = SimpleNamespace(parse_and_bind=parse_and_bind)
    monkeypatch.setitem(sys.modules, "readline", fake_readline)

    main._configure_readline()

    assert len(calls) == 4


def test_build_runtime_registers_subagent_tool_by_default(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)

    runtime = main._build_runtime(
        AgentSettings(enable_memory=False, enable_todos=False),
    )

    assert runtime["tool_registry"].get("subagent_run") is not None


def test_build_runtime_omits_subagent_tool_at_max_depth(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)

    runtime = main._build_runtime(
        AgentSettings(enable_memory=False, enable_todos=False, subagent_max_depth=1),
        subagent_depth=1,
    )

    assert runtime["tool_registry"].get("subagent_run") is None


def test_build_runtime_uses_fallback_client_for_multiple_profiles(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)

    runtime = main._build_runtime(
        AgentSettings(
            enable_memory=False,
            enable_todos=False,
            model_profiles=[
                ModelProfile(name="primary", model="openai/primary"),
                ModelProfile(name="backup", model="openai/backup"),
            ],
        ),
    )

    assert isinstance(runtime["model_client"], FallbackModelClient)
