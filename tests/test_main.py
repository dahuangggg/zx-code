from __future__ import annotations

from typer.testing import CliRunner

from agent.main import _build_typer_app


def test_cli_resume_option_passes_session_to_runtime(monkeypatch) -> None:
    seen: dict[str, object] = {}

    def fake_run_cli(**kwargs: object) -> int:
        seen.update(kwargs)
        return 0

    monkeypatch.setattr("agent.main._run_cli", fake_run_cli)

    result = CliRunner().invoke(_build_typer_app(), ["--resume", "demo", "continue"])

    assert result.exit_code == 0
    assert seen["task"] == "continue"
    assert seen["resume_session_id"] == "demo"


def test_cli_session_id_remains_resume_alias(monkeypatch) -> None:
    seen: dict[str, object] = {}

    def fake_run_cli(**kwargs: object) -> int:
        seen.update(kwargs)
        return 0

    monkeypatch.setattr("agent.main._run_cli", fake_run_cli)

    result = CliRunner().invoke(_build_typer_app(), ["--session-id", "legacy", "continue"])

    assert result.exit_code == 0
    assert seen["task"] == "continue"
    assert seen["resume_session_id"] == "legacy"
