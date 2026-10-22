# Changelog

## 0.1.0 — 2026-10-22 (alpha)

Initial public release packaging for the existing CLI runtime:

- ReAct loop with OpenAI-compatible Chat Completions, tools and model fallback.
- Persistent sessions, memory, todos, task DAG and optional subagents/worktrees.
- CodeContext hybrid retrieval, plugin tools and MCP integration.
- Tool permission policies, hooks, execution traces and edit diffs.
- MIT license, `zx-code` package/command and backward-compatible `agent` command.
- Offline reproducible demo, contributor guide, feedback templates and CI.
- Fix cancelled-index status being recreated after clearing an index.
- Make context tests compatible with Python 3.14 event-loop behavior.

Alpha software. No OS-level sandbox, guaranteed trace redaction, general automatic undo,
or native Windows support. See README for installation and runtime limitations.
