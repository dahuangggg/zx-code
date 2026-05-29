"""CLI-only 代理运行时的 Typer 命令行入口。

本模块解析命令行参数，将其折叠进 ``AgentSettings``，然后把执行委托给
``runtime.runner``。它不直接构建模型客户端，也不直接运行 ReAct loop。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import typer

from agent.config import AgentSettings, ConfigLoader
from agent.runtime import _run_cli


def _settings_from_cli(
    *,
    model: str | None,
    fallback_models: str | None,
    reasoning_effort: str | None,
    max_turns: int | None,
    session_id: str | None,
    data_dir: str | None,
    context_max_tokens: int | None,
    compact_model: str | None,
    skills_dir: str | None,
    tasks_dir: str | None,
    subagent_max_depth: int | None,
    worktree_dir: str | None,
    no_stream: bool,
    no_memory: bool,
    no_skills: bool,
    no_todos: bool,
    no_tasks: bool,
    no_subagents: bool,
    worktree_isolation: bool,
    debug_log: bool,
    debug_log_path: str | None,
    trace: bool,
    trace_dir: str | None,
    no_trace_sanitize: bool,
    trace_html: bool,
    trace_html_include_raw_response: bool,
) -> AgentSettings:
    overrides: dict[str, Any] = {
        "model": model,
        "fallback_models": fallback_models,
        "reasoning_effort": reasoning_effort,
        "max_iterations": max_turns,
        "session_id": session_id,
        "data_dir": data_dir,
        "context_max_tokens": context_max_tokens,
        "compact_model": compact_model,
        "skills_dir": skills_dir,
        "tasks_dir": tasks_dir,
        "subagent_max_depth": subagent_max_depth,
        "worktree_dir": worktree_dir,
        "debug_log_path": debug_log_path,
        "trace_dir": trace_dir,
    }
    if no_stream:
        overrides["stream"] = False
    if no_memory:
        overrides["enable_memory"] = False
    if no_skills:
        overrides["enable_skills"] = False
    if no_todos:
        overrides["enable_todos"] = False
    if no_tasks:
        overrides["enable_tasks"] = False
    if no_subagents:
        overrides["enable_subagents"] = False
    if worktree_isolation:
        overrides["enable_worktree_isolation"] = True
    if debug_log:
        overrides["debug_log_enabled"] = True
    if trace:
        overrides["trace_enabled"] = True
    if no_trace_sanitize:
        overrides["trace_sanitize"] = False
    if trace_html:
        overrides["trace_html"] = True
    if trace_html_include_raw_response:
        overrides["trace_html_include_raw_response"] = True
    return ConfigLoader(project_dir=Path.cwd()).load(overrides)


def _build_typer_app() -> Any:
    app = typer.Typer(add_completion=False, pretty_exceptions_show_locals=False)

    @app.command()
    def cli(
        task: list[str] | None = typer.Argument(None),
        model: str | None = typer.Option(None),
        fallback_models: str | None = typer.Option(None, "--fallback-models"),
        reasoning_effort: str | None = typer.Option(None, "--reasoning-effort"),
        max_turns: int | None = typer.Option(None, "--max-turns"),
        resume: str | None = typer.Option(None, "--resume"),
        session_id: str | None = typer.Option(None, "--session-id"),
        data_dir: str | None = typer.Option(None, "--data-dir"),
        context_max_tokens: int | None = typer.Option(None, "--context-max-tokens"),
        compact_model: str | None = typer.Option(None, "--compact-model"),
        skills_dir: str | None = typer.Option(None, "--skills-dir"),
        tasks_dir: str | None = typer.Option(None, "--tasks-dir"),
        subagent_max_depth: int | None = typer.Option(None, "--subagent-max-depth"),
        worktree_dir: str | None = typer.Option(None, "--worktree-dir"),
        no_stream: bool = typer.Option(False, "--no-stream"),
        no_memory: bool = typer.Option(False, "--no-memory"),
        no_skills: bool = typer.Option(False, "--no-skills"),
        no_todos: bool = typer.Option(False, "--no-todos"),
        no_tasks: bool = typer.Option(False, "--no-tasks"),
        no_subagents: bool = typer.Option(False, "--no-subagents"),
        worktree_isolation: bool = typer.Option(False, "--worktree-isolation"),
        debug_log: bool = typer.Option(False, "--debug-log"),
        debug_log_path: str | None = typer.Option(None, "--debug-log-path"),
        trace: bool = typer.Option(False, "--trace"),
        trace_dir: str | None = typer.Option(None, "--trace-dir"),
        no_trace_sanitize: bool = typer.Option(False, "--no-trace-sanitize"),
        trace_html: bool = typer.Option(False, "--trace-html"),
        trace_html_include_raw_response: bool = typer.Option(
            False,
            "--trace-html-include-raw-response",
        ),
        print_system_prompt: bool = typer.Option(False, "--print-system-prompt"),
    ) -> None:
        settings = _settings_from_cli(
            model=model,
            fallback_models=fallback_models,
            reasoning_effort=reasoning_effort,
            max_turns=max_turns,
            session_id=session_id,
            data_dir=data_dir,
            context_max_tokens=context_max_tokens,
            compact_model=compact_model,
            skills_dir=skills_dir,
            tasks_dir=tasks_dir,
            subagent_max_depth=subagent_max_depth,
            worktree_dir=worktree_dir,
            no_stream=no_stream,
            no_memory=no_memory,
            no_skills=no_skills,
            no_todos=no_todos,
            no_tasks=no_tasks,
            no_subagents=no_subagents,
            worktree_isolation=worktree_isolation,
            debug_log=debug_log,
            debug_log_path=debug_log_path,
            trace=trace,
            trace_dir=trace_dir,
            no_trace_sanitize=no_trace_sanitize,
            trace_html=trace_html,
            trace_html_include_raw_response=trace_html_include_raw_response,
        )
        exit_code = _run_cli(
            task=" ".join(task).strip() if task else None,
            settings=settings,
            print_system_prompt=print_system_prompt,
            resume_session_id=resume or session_id,
        )
        raise typer.Exit(exit_code)

    return app


def main() -> int | None:
    """构建并调用 Typer 应用。"""
    app = _build_typer_app()
    app()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
