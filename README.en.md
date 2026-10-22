# zx-code

[中文](README.md) · [Reproducible demo](docs/DEMO.md) · [Contributing](CONTRIBUTING.md) · [MIT](LICENSE)

A local-first Python coding agent runtime for developers who need inspectable tool
execution, persistent sessions and extensible tooling. The CLI combines a ReAct loop,
OpenAI-compatible Chat Completions, permission policies and structured traces.

**Status: early alpha.** Public adoption and performance benchmarks have not yet been
established. Permission rules are not an OS-level sandbox. Edits return diffs, but there
is no general automatic undo. macOS/Linux with Python 3.11+ are required; the session
store depends on POSIX file locking, so native Windows is not supported.

## Install from source

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then:

```bash
git clone https://github.com/dahuangggg/zx-code.git
cd zx-code
uv sync --locked --extra dev
uv run zx-code --help
```

`zx-code` is the package and preferred CLI name. `agent` remains a CLI alias and the
Python import namespace. Source installation does not require a PyPI release.

## Try without an API key

```bash
uv run python examples/offline_demo.py --output .zx-code/artifacts/offline-demo
```

This uses scripted model responses with the real runtime. It reads and edits a sample
file, denies a shell call, writes JSONL/HTML traces and resumes a persisted session.
The output directory must not already exist. This demonstrates runtime behavior, not
an LLM's autonomous coding performance.

## Use a model

Set `OPENAI_API_KEY` in your shell (never commit it). Copy the example config if needed:

```bash
cp .zx-code/config.toml.example .zx-code/config.toml
uv run zx-code --session-id demo --trace --trace-html "Summarize this repository"
uv run zx-code --resume demo "Explain the tool execution path"
```

Configure a model available to your account in the config's model profiles or with
`--model`. Model-backed tasks incur provider charges. Configuration loads from
`~/.zx-code/config.toml`, then `.zx-code/config.toml`, then CLI overrides. The provider
uses Chat Completions; a Responses-only endpoint needs a different adapter.

## Runtime capabilities

- Read, write, exact edit, grep, glob and shell tools with centralized execution.
- Permissions, hooks, circuit breakers, output truncation and JSONL/HTML traces.
- Resumable sessions, memory, todos, task DAG and optional isolated subagents.
- CodeContext hybrid vector/keyword retrieval with explicit embeddings.
- Plugin tools, skills and MCP servers through the shared tool registry.

The default tool policy is `allow`; configure policies for your workspace before
running model-driven changes. Trace sanitization is best effort; inspect artifacts
before sharing them. See the [Chinese reference](README.md) for architecture, settings
and module paths.

## Develop and report feedback

```bash
uv run pytest -q
uv build
```

The live API test is opt-in. Normal tests and the offline demo require no model key.
[Report a bug or share trial feedback](https://github.com/dahuangggg/zx-code/issues/new/choose).
Only real, permissioned feedback will be used as adoption evidence.
