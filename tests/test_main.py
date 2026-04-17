from __future__ import annotations

import sys
from types import SimpleNamespace

from agent import main


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
        "set enable-meta-keybindings on",
    ]


def test_configure_readline_ignores_backend_errors(monkeypatch) -> None:
    calls: list[str] = []

    def parse_and_bind(binding: str) -> None:
        calls.append(binding)
        raise RuntimeError("unsupported")

    fake_readline = SimpleNamespace(parse_and_bind=parse_and_bind)
    monkeypatch.setitem(sys.modules, "readline", fake_readline)

    main._configure_readline()

    assert len(calls) == 5
