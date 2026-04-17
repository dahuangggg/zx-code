from __future__ import annotations

import argparse
import asyncio
import contextlib
import io
from pathlib import Path
from typing import Any

from rich.console import Console

from agent.config import AgentSettings, ConfigLoader
from agent.context import ContextGuard
from agent.loop import run_task
from agent.memory import MemoryStore
from agent.permissions import PermissionCheck, PermissionManager
from agent.prompt import SystemPromptBuilder
from agent.providers.litellm_client import LiteLLMModelClient
from agent.recovery import AgentError
from agent.sessions import SessionStore, safe_session_id
from agent.todo import TodoManager
from agent.tools import build_default_registry

try:
    import typer
except ImportError:
    typer = None


console = Console()


def _configure_readline() -> None:
    try:
        import readline
    except ImportError:
        return

    # Improve UTF-8 input editing in REPLs. Different readline backends
    # support slightly different settings, so apply defensively.
    bindings = [
        "set bind-tty-special-chars off",
        "set input-meta on",
        "set output-meta on",
        "set convert-meta off",
    ]
    if "libedit" in (readline.__doc__ or "").lower():
        bindings.append("set enable-meta-keybindings on")
    for binding in bindings:
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                readline.parse_and_bind(binding)
        except Exception:
            continue


def _stream_printer(chunk: str) -> None:
    console.print(chunk, end="")


def _resolve_project_path(project_root: Path, value: str) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    return project_root / path


def _approval_prompt(check: PermissionCheck) -> bool:
    console.print(f"[yellow]Permission required:[/yellow] {check.reason}")
    console.print(f"Tool: {check.tool_name}")
    answer = input("Allow this tool call? [y/N] ").strip().lower()
    return answer in {"y", "yes"}


def _build_runtime(settings: AgentSettings) -> dict[str, Any]:
    project_root = Path.cwd()
    data_dir = _resolve_project_path(project_root, settings.data_dir)
    memory_store = (
        MemoryStore(_resolve_project_path(project_root, settings.memory_path))
        if settings.enable_memory
        else None
    )
    if memory_store is not None:
        memory_store.ensure()

    todo_manager = (
        TodoManager(data_dir / "todos" / f"{safe_session_id(settings.session_id)}.json")
        if settings.enable_todos
        else None
    )
    prompt_builder = SystemPromptBuilder(
        project_root=project_root,
        memory_store=memory_store,
        todo_manager=todo_manager,
    )
    system_prompt = prompt_builder.build(settings.to_agent_config())
    config = settings.to_agent_config(system_prompt=system_prompt)
    permission_manager = PermissionManager(
        tool_policies=settings.permission_tools,
        default_decision=settings.permission_default,
    )
    registry = build_default_registry(
        permission_manager=permission_manager,
        approval_callback=_approval_prompt,
        todo_manager=todo_manager,
        memory_store=memory_store,
    )
    return {
        "config": config,
        "context_guard": ContextGuard(
            max_chars=settings.context_max_chars,
            keep_recent=settings.context_keep_recent,
            tool_result_max_chars=settings.context_tool_result_max_chars,
        ),
        "model_client": LiteLLMModelClient(model=settings.model),
        "prompt_builder": prompt_builder,
        "session_store": SessionStore(data_dir / "sessions"),
        "tool_registry": registry,
    }


async def _run_once(
    task: str,
    *,
    settings: AgentSettings,
    print_system_prompt: bool,
) -> int:
    runtime = _build_runtime(settings)

    if print_system_prompt:
        console.print(runtime["prompt_builder"].debug(runtime["config"]))
        return 0

    try:
        result = await run_task(
            task,
            model_client=runtime["model_client"],
            tool_registry=runtime["tool_registry"],
            config=runtime["config"],
            stream_handler=_stream_printer if settings.stream else None,
            session_store=runtime["session_store"],
            context_guard=runtime["context_guard"],
            prompt_builder=runtime["prompt_builder"],
        )
    except AgentError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        return 1

    if settings.stream and result.final_text:
        console.print()
    if not settings.stream:
        console.print(result.final_text)
    return 0


def _run_repl(*, settings: AgentSettings, print_system_prompt: bool) -> int:
    _configure_readline()
    if print_system_prompt:
        runtime = _build_runtime(settings)
        console.print(runtime["prompt_builder"].debug(runtime["config"]))
        return 0

    console.print("Entering REPL. Type 'exit' or 'quit' to stop.")
    while True:
        try:
            task = input("zx-code> ").strip()
        except EOFError:
            console.print()
            return 0
        if not task:
            continue
        if task in {"exit", "quit"}:
            return 0
        exit_code = asyncio.run(
            _run_once(
                task,
                settings=settings,
                print_system_prompt=False,
            )
        )
        if exit_code != 0:
            return exit_code


def _run_cli(
    *,
    task: str | None,
    settings: AgentSettings,
    print_system_prompt: bool,
) -> int:
    if not task:
        return _run_repl(settings=settings, print_system_prompt=print_system_prompt)
    return asyncio.run(
        _run_once(
            task,
            settings=settings,
            print_system_prompt=print_system_prompt,
        )
    )


def _build_argparse() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="ZX-code local coding agent")
    parser.add_argument("task", nargs="*")
    parser.add_argument("--model", default=None)
    parser.add_argument("--max-turns", type=int, default=None)
    parser.add_argument("--session-id", default=None)
    parser.add_argument("--data-dir", default=None)
    parser.add_argument("--context-max-chars", type=int, default=None)
    parser.add_argument("--no-stream", action="store_true")
    parser.add_argument("--no-memory", action="store_true")
    parser.add_argument("--no-todos", action="store_true")
    parser.add_argument("--print-system-prompt", action="store_true")
    return parser


def _settings_from_cli(
    *,
    model: str | None,
    max_turns: int | None,
    session_id: str | None,
    data_dir: str | None,
    context_max_chars: int | None,
    no_stream: bool,
    no_memory: bool,
    no_todos: bool,
) -> AgentSettings:
    overrides: dict[str, Any] = {
        "model": model,
        "max_iterations": max_turns,
        "session_id": session_id,
        "data_dir": data_dir,
        "context_max_chars": context_max_chars,
    }
    if no_stream:
        overrides["stream"] = False
    if no_memory:
        overrides["enable_memory"] = False
    if no_todos:
        overrides["enable_todos"] = False
    return ConfigLoader(project_dir=Path.cwd()).load(overrides)


def _build_typer_app() -> Any:
    app = typer.Typer(add_completion=False, pretty_exceptions_show_locals=False)

    @app.command()
    def cli(
        task: list[str] | None = typer.Argument(None),
        model: str | None = typer.Option(None),
        max_turns: int | None = typer.Option(None, "--max-turns"),
        session_id: str | None = typer.Option(None, "--session-id"),
        data_dir: str | None = typer.Option(None, "--data-dir"),
        context_max_chars: int | None = typer.Option(None, "--context-max-chars"),
        no_stream: bool = typer.Option(False, "--no-stream"),
        no_memory: bool = typer.Option(False, "--no-memory"),
        no_todos: bool = typer.Option(False, "--no-todos"),
        print_system_prompt: bool = typer.Option(False, "--print-system-prompt"),
    ) -> None:
        settings = _settings_from_cli(
            model=model,
            max_turns=max_turns,
            session_id=session_id,
            data_dir=data_dir,
            context_max_chars=context_max_chars,
            no_stream=no_stream,
            no_memory=no_memory,
            no_todos=no_todos,
        )
        exit_code = _run_cli(
            task=" ".join(task).strip() if task else None,
            settings=settings,
            print_system_prompt=print_system_prompt,
        )
        raise typer.Exit(exit_code)

    return app


def main() -> int | None:
    if typer is not None:
        app = _build_typer_app()
        app()
        return 0

    parser = _build_argparse()
    args = parser.parse_args()
    task = " ".join(args.task).strip() if args.task else None
    settings = _settings_from_cli(
        model=args.model,
        max_turns=args.max_turns,
        session_id=args.session_id,
        data_dir=args.data_dir,
        context_max_chars=args.context_max_chars,
        no_stream=args.no_stream,
        no_memory=args.no_memory,
        no_todos=args.no_todos,
    )
    return _run_cli(
        task=task,
        settings=settings,
        print_system_prompt=args.print_system_prompt,
    )


if __name__ == "__main__":
    raise SystemExit(main())
