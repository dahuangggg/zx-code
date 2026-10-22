"""Scripted model, real runtime/tools: no API key or model request required."""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from agent.core.loop import run_task
from agent.models import Message, ModelTurn, RuntimeConfig, ToolCall
from agent.permissions import PermissionManager
from agent.state.sessions import SessionStore
from agent.tools import build_default_registry
from agent.trace import TraceLogger


class ScriptedModel:
    """Fixed responses exercise the runtime; they do not evaluate an LLM."""

    def __init__(self, turns: list[ModelTurn]) -> None:
        self.turns = iter(turns)
        self.seen_history: list[Message] = []

    async def run_turn(self, *, system_prompt, messages, tools, stream_handler=None):
        self.seen_history = list(messages)
        return next(self.turns)


async def run_demo(output_dir: Path) -> dict:
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=False)
    target = output_dir / "greeting.py"
    target.write_text('def greet():\n    return "helo"\n', encoding="utf-8")
    registry = build_default_registry(permission_manager=PermissionManager(
        default_decision="deny",
        tool_policies={"read_file": "allow", "edit_file": "allow", "bash": "deny"},
        working_dir=output_dir,
    ))
    store = SessionStore(output_dir / "sessions")
    config = RuntimeConfig(session_id="offline-demo", model="scripted-offline", stream=False)
    turns = [
        ModelTurn(tool_calls=[ToolCall(id="read", name="read_file", arguments={"path": str(target)})]),
        ModelTurn(tool_calls=[ToolCall(id="edit", name="edit_file", arguments={
            "path": str(target), "old_text": "helo", "new_text": "hello",
        })]),
        ModelTurn(tool_calls=[ToolCall(id="blocked", name="bash", arguments={"command": "printf blocked"})]),
        ModelTurn(text="Typo fixed; shell execution was denied by policy."),
    ]
    trace = TraceLogger(session_id=config.session_id, trace_dir=output_dir / "traces", html_enabled=True)
    result = await run_task("Fix the greeting typo and exercise the shell deny policy.",
        model_client=ScriptedModel(turns), tool_registry=registry, config=config,
        session_store=store, trace_logger=trace)
    assert [item.status for item in result.tool_results] == ["success", "success", "error"]
    denied = result.tool_results[-1].envelope.error
    assert denied is not None and denied.code == "ACCESS_DENIED"
    assert 'return "hello"' in target.read_text(encoding="utf-8")
    diff = result.tool_results[1].envelope.data["diff"]
    (output_dir / "change.diff").write_text(diff, encoding="utf-8")

    # Recreate both objects to exercise recovery from persisted messages.
    resumed_store = SessionStore(output_dir / "sessions")
    resumed = ScriptedModel([ModelTurn(text="Resumed the persisted greeting task.")])
    await run_task("Continue the previous task.", model_client=resumed,
        tool_registry=registry, config=config, session_store=resumed_store)
    assert any(msg.content == result.final_text for msg in resumed.seen_history)
    events = [json.loads(line) for line in trace.jsonl_path.read_text().splitlines()]
    assert len([event for event in events if event["event"] == "tool_result"]) == 3
    assert trace.html_path.is_file()
    summary = {
        "model": "scripted (no model API calls)",
        "read_file": "passed", "edit_file": "passed", "bash_policy": "denied",
        "session_resume": "passed", "trace_events": len(events),
    }
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print("OFFLINE DEMO — fixed model responses, real runtime execution")
    print(diff)
    print(json.dumps(summary, indent=2))
    print(f"Artifacts: {output_dir}")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="New directory for demo artifacts")
    asyncio.run(run_demo(parser.parse_args().output))
