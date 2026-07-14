from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.config import AgentSettings
from agent.operations import FileCheckpointStore
from agent.runtime.session import AgentSession, SessionClosedError


class _Closer:
    def __init__(self) -> None:
        self.calls = 0

    async def close(self) -> None:
        self.calls += 1


class _Team:
    def __init__(self) -> None:
        self.calls = 0

    async def shutdown(self) -> None:
        self.calls += 1


@pytest.mark.asyncio
async def test_agent_session_builds_once_runs_many_and_closes_once(monkeypatch) -> None:
    builds: list[object] = []
    run_registries: list[object] = []
    run_traces: list[object] = []
    router = _Closer()
    team = _Team()
    flushes: list[bool] = []
    lifecycle: list[str] = []
    session_store = SimpleNamespace(
        append=lambda session_id, record: lifecycle.append(record.type)
    )
    runtime = SimpleNamespace(
        settings=AgentSettings(enable_memory=False, enable_todos=False, stream=False),
        policy=None,
        config=SimpleNamespace(session_id="session-1"),
        tool_registry=object(),
        session_store=session_store,
        project_root=".",
        services=SimpleNamespace(
            model_client=object(),
            summary_model_client=object(),
            context_guard=object(),
            prompt_builder=object(),
            hook_runner=object(),
            debug_log=object(),
            trace_logger=object(),
            operation_journal=SimpleNamespace(records=lambda: ()),
            checkpoint_store=object(),
            stream_output=SimpleNamespace(handler=None, renderer=None, flush=lambda: flushes.append(True)),
        ),
        extensions=SimpleNamespace(team=team, mcp_router=None),
    )

    def fake_build_runtime(*args, **kwargs):
        builds.append((args, kwargs))
        return runtime

    async def fake_attach(runtime_arg, settings_arg):
        runtime_arg.extensions.mcp_router = router
        return router

    async def fake_run_task(task, **kwargs):
        run_registries.append(kwargs["tool_registry"])
        run_traces.append(kwargs["trace_logger"])
        return SimpleNamespace(final_text=f"answer:{task}", tool_results=[])

    monkeypatch.setattr("agent.runtime.session._build_runtime", fake_build_runtime)
    monkeypatch.setattr("agent.runtime.session._attach_mcp_tools", fake_attach)
    monkeypatch.setattr("agent.runtime.session.run_task", fake_run_task)
    monkeypatch.setattr("agent.runtime.session._refresh_system_prompt", lambda runtime: None)
    monkeypatch.setattr("agent.runtime.session._build_trace_logger", lambda *a, **k: object())

    session = await AgentSession.open(
        AgentSettings(enable_memory=False, enable_todos=False, stream=False),
        session_id="session-1",
    )
    first = await session.run("one")
    second = await session.run("two")
    await session.close()
    await session.close()

    assert len(builds) == 1
    assert first.final_text == "answer:one"
    assert second.final_text == "answer:two"
    assert run_registries == [runtime.tool_registry, runtime.tool_registry]
    assert len(run_traces) == 2 and run_traces[0] is not run_traces[1]
    assert router.calls == 1
    assert team.calls == 1
    assert flushes == [True, True, True]
    assert lifecycle == [
        "session_opened",
        "run_completed",
        "run_completed",
        "session_closed",
    ]


@pytest.mark.asyncio
async def test_close_cancels_active_run_and_session_rejects_new_runs(monkeypatch) -> None:
    started = asyncio.Event()
    runtime = SimpleNamespace(
        settings=AgentSettings(enable_memory=False, enable_todos=False, stream=False),
        policy=None,
        config=SimpleNamespace(session_id="session-1"),
        tool_registry=object(),
        session_store=SimpleNamespace(append=lambda session_id, record: None),
        project_root=".",
        services=SimpleNamespace(
            model_client=object(),
            summary_model_client=object(),
            context_guard=object(),
            prompt_builder=object(),
            hook_runner=object(),
            debug_log=object(),
            trace_logger=object(),
            operation_journal=SimpleNamespace(records=lambda: ()),
            checkpoint_store=object(),
            stream_output=SimpleNamespace(handler=None, renderer=None, flush=lambda: None),
        ),
        extensions=SimpleNamespace(team=None, mcp_router=None),
    )

    monkeypatch.setattr("agent.runtime.session._build_runtime", lambda *a, **k: runtime)
    monkeypatch.setattr("agent.runtime.session._attach_mcp_tools", lambda *a, **k: _async_none())

    async def block(*args, **kwargs):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr("agent.runtime.session.run_task", block)
    monkeypatch.setattr("agent.runtime.session._refresh_system_prompt", lambda runtime: None)
    session = await AgentSession.open(
        AgentSettings(enable_memory=False, enable_todos=False, stream=False),
        session_id="session-1",
    )
    active = asyncio.create_task(session.run("wait"))
    await started.wait()
    await session.close()

    with pytest.raises(asyncio.CancelledError):
        await active
    with pytest.raises(SessionClosedError):
        await session.run("again")


@pytest.mark.asyncio
async def test_agent_session_does_not_render_operation_evidence_after_run(
    monkeypatch,
    capsys,
) -> None:
    operation_id = "operation-1"
    runtime = SimpleNamespace(
        settings=AgentSettings(enable_memory=False, enable_todos=False, stream=False),
        policy=None,
        config=SimpleNamespace(session_id="session-1"),
        tool_registry=object(),
        session_store=SimpleNamespace(append=lambda session_id, record: None),
        project_root=Path.cwd(),
        services=SimpleNamespace(
            model_client=object(),
            summary_model_client=object(),
            context_guard=object(),
            prompt_builder=object(),
            hook_runner=object(),
            debug_log=object(),
            trace_logger=object(),
            operation_journal=SimpleNamespace(
                get=lambda requested_id: SimpleNamespace(
                    operation_id=requested_id,
                    request=SimpleNamespace(tool_name="edit_file", side_effect="write"),
                    status="committed",
                    outcome=SimpleNamespace(
                        evidence={"patch_path": "/tmp/operation-1.patch"}
                    ),
                )
            ),
            checkpoint_store=object(),
            stream_output=SimpleNamespace(handler=None, renderer=None, flush=lambda: None),
        ),
        extensions=SimpleNamespace(team=None, mcp_router=None),
    )

    async def fake_run_task(task, **kwargs):
        return SimpleNamespace(
            final_text="done",
            iterations=1,
            tool_results=[
                SimpleNamespace(
                    envelope=SimpleNamespace(context={"operation_id": operation_id})
                )
            ],
        )

    monkeypatch.setattr("agent.runtime.session.run_task", fake_run_task)
    monkeypatch.setattr("agent.runtime.session._refresh_system_prompt", lambda runtime: None)
    monkeypatch.setattr("agent.runtime.session._build_trace_logger", lambda *a, **k: object())

    await AgentSession(runtime).run("edit a file")

    assert "operation evidence" not in capsys.readouterr().out


def test_agent_session_undo_restores_latest_file_change(tmp_path: Path) -> None:
    target = tmp_path / "notes.txt"
    target.write_text("before\n", encoding="utf-8")
    store = FileCheckpointStore(tmp_path / "checkpoints", project_root=tmp_path)
    store.begin("op-latest", tool_name="edit_file", arguments={"path": str(target)})
    target.write_text("after\n", encoding="utf-8")
    store.finalize("op-latest")
    runtime = SimpleNamespace(
        policy=None,
        project_root=tmp_path,
        services=SimpleNamespace(checkpoint_store=store),
    )

    operation_id, restored = AgentSession(runtime).undo()

    assert operation_id == "op-latest"
    assert restored == target.resolve()
    assert target.read_text(encoding="utf-8") == "before\n"


async def _async_none():
    return None
