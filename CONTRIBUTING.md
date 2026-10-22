# Contributing to zx-code

zx-code is an early-stage, local-first Python coding agent runtime. Small bug reports,
reproducible examples, documentation improvements and focused pull requests are welcome.

## Development

Python 3.11+ on macOS or Linux and uv are required. The session store uses POSIX `fcntl`;
native Windows is not currently supported.

```bash
git clone https://github.com/dahuangggg/zx-code.git
cd zx-code
uv sync --locked --extra dev
uv run pytest -q
uv run python examples/offline_demo.py --output .zx-code/artifacts/contributor-demo
uv build
```

Normal tests use fake model clients; the live API streaming test is opt-in. Do not add
API keys or require paid model calls in the default test suite. For runtime changes,
include a regression test for the behavior being changed.

## Reporting problems and use cases

Use the [issue templates](https://github.com/dahuangggg/zx-code/issues/new/choose).
Include OS, Python and zx-code versions, a minimal reproduction, expected behavior,
actual behavior and sanitized error output. For trial feedback, explain the task,
what worked, what failed and whether you permit a public quote. Never include secrets,
private repositories, raw session history or unsanitized model traces.

## Pull requests

Explain the user-visible change and how you verified it. Keep each PR focused. Existing
`agent` commands and `agent` Python imports remain supported; `zx-code` is the preferred
CLI name. Contributions are distributed under the repository's MIT license.

## Release checks

Follow [the release checklist](docs/RELEASING.md). Do not claim published packages,
verified platform support, users, downloads or benchmarks without corresponding evidence.
