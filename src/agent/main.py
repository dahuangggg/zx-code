from __future__ import annotations

import argparse
import asyncio
from typing import Any

from rich.console import Console

from agent.loop import run_task
from agent.models import AgentConfig
from agent.providers.litellm_client import LiteLLMModelClient
from agent.recovery import AgentError
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
    # support slightly different settings, so apply them defensively.
    bindings = [
        "set bind-tty-special-chars off",
        "set input-meta on",
        "set output-meta on",
        "set convert-meta off",
        "set enable-meta-keybindings on",
    ]
    for binding in bindings:
        try:
            readline.parse_and_bind(binding)
        except Exception:
            continue


def _stream_printer(chunk: str) -> None:
    console.print(chunk, end="")


async def _run_once(
    task: str,
    *,
    model: str,
    max_turns: int,
    stream: bool,
) -> int:
    config = AgentConfig(
        model=model,
        max_iterations=max_turns,
        stream=stream,
    )
    model_client = LiteLLMModelClient(model=model)
    registry = build_default_registry()

    try:
        result = await run_task(
            task,
            model_client=model_client,
            tool_registry=registry,
            config=config,
            stream_handler=_stream_printer if stream else None,
        )
    except AgentError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        return 1

    if stream and result.final_text:
        console.print()
    if not stream:
        console.print(result.final_text)
    return 0


def _run_repl(*, model: str, max_turns: int, stream: bool) -> int:
    _configure_readline()
    console.print("Entering REPL. Type 'exit' or 'quit' to stop.")
    while True:
        try:
            task = input("agent> ").strip()
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
                model=model,
                max_turns=max_turns,
                stream=stream,
            )
        )
        if exit_code != 0:
            return exit_code


def _run_cli(*, task: str | None, model: str, max_turns: int, stream: bool) -> int:
    if not task:
        return _run_repl(model=model, max_turns=max_turns, stream=stream)
    return asyncio.run(
        _run_once(
            task,
            model=model,
            max_turns=max_turns,
            stream=stream,
        )
    )


def _build_argparse() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Phase 1 local coding agent")
    parser.add_argument("task", nargs="*")
    parser.add_argument("--model", default="openai/gpt-4o-mini")
    parser.add_argument("--max-turns", type=int, default=8)
    parser.add_argument("--no-stream", action="store_true")
    return parser


def _build_typer_app() -> Any:
    app = typer.Typer(add_completion=False, pretty_exceptions_show_locals=False)

    @app.command()
    def cli(
        task: list[str] | None = typer.Argument(None),
        model: str = typer.Option("openai/gpt-4o-mini"),
        max_turns: int = typer.Option(8, "--max-turns"),
        no_stream: bool = typer.Option(False, "--no-stream"),
    ) -> None:
        exit_code = _run_cli(
            task=" ".join(task).strip() if task else None,
            model=model,
            max_turns=max_turns,
            stream=not no_stream,
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
    return _run_cli(
        task=task,
        model=args.model,
        max_turns=args.max_turns,
        stream=not args.no_stream,
    )


if __name__ == "__main__":
    raise SystemExit(main())
