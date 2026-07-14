from __future__ import annotations

from typer.testing import CliRunner

from agent.config import ConfigLoader
from agent.main import _build_typer_app
from agent.operations import FileCheckpointStore


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

    result = CliRunner().invoke(_build_typer_app(), ["--session-id", "resume-me", "continue"])

    assert result.exit_code == 0
    assert seen["task"] == "continue"
    assert seen["resume_session_id"] == "resume-me"


def test_cli_trace_options_override_settings(monkeypatch) -> None:
    seen: dict[str, object] = {}

    def fake_run_cli(**kwargs: object) -> int:
        seen.update(kwargs)
        return 0

    monkeypatch.setattr("agent.main._run_cli", fake_run_cli)

    result = CliRunner().invoke(
        _build_typer_app(),
        [
            "--trace",
            "--trace-dir",
            ".zx-code/logs/custom-traces",
            "--no-trace-sanitize",
            "--trace-html",
            "--trace-html-include-raw-response",
            "hello",
        ],
    )

    assert result.exit_code == 0
    settings = seen["settings"]
    assert settings.trace.enabled is True
    assert settings.trace.dir == ".zx-code/logs/custom-traces"
    assert settings.trace.sanitize is False
    assert settings.trace.html is True
    assert settings.trace.html_include_raw_response is True


def test_cli_doctor_json_does_not_start_runtime(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    loaded = ConfigLoader(
        project_dir=tmp_path,
        user_config_path=tmp_path / "missing-user.toml",
    ).load_with_sources()
    monkeypatch.setattr("agent.main._loaded_config_from_cli", lambda **kwargs: loaded)

    def fail_run_cli(**kwargs: object) -> int:
        raise AssertionError("doctor must not start the agent runtime")

    monkeypatch.setattr("agent.main._run_cli", fail_run_cli)

    result = CliRunner().invoke(_build_typer_app(), ["doctor", "--json"])

    assert result.exit_code == 0
    payload = __import__("json").loads(result.stdout)
    assert payload["workspace"]["trust"] == "untrusted"


def test_cli_undo_restores_finalized_operation_without_starting_runtime(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "notes.txt"
    target.write_text("before\n", encoding="utf-8")
    store = FileCheckpointStore(
        tmp_path / ".zx-code" / "state" / "checkpoints",
        project_root=tmp_path,
    )
    store.begin("op-undo", tool_name="write_file", arguments={"path": str(target)})
    target.write_text("after\n", encoding="utf-8")
    store.finalize("op-undo")
    loaded = ConfigLoader(
        project_dir=tmp_path,
        user_config_path=tmp_path / "missing-user.toml",
    ).load_with_sources()
    monkeypatch.setattr("agent.main._loaded_config_from_cli", lambda **kwargs: loaded)

    def fail_run_cli(**kwargs: object) -> int:
        raise AssertionError("undo must not start the agent runtime")

    monkeypatch.setattr("agent.main._run_cli", fail_run_cli)

    result = CliRunner().invoke(_build_typer_app(), ["undo", "op-undo"])

    assert result.exit_code == 0
    assert "Restored" in result.stdout
    assert target.read_text(encoding="utf-8") == "before\n"
