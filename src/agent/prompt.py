from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict

from agent.memory import MemoryStore
from agent.models import AgentConfig
from agent.todo import TodoManager


DEFAULT_SYSTEM_PROMPT = """You are a local coding agent.

Work in short iterations.
Use tools when they materially improve accuracy.
When a tool fails, explain the failure clearly and continue if possible.
Prefer concrete answers over speculation.
"""


class PromptSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    content: str

    def render(self) -> str:
        return f"## {self.title}\n{self.content.strip()}"


class SystemPromptBuilder:
    def __init__(
        self,
        *,
        project_root: Path | str,
        memory_store: MemoryStore | None = None,
        todo_manager: TodoManager | None = None,
    ) -> None:
        self.project_root = Path(project_root)
        self.memory_store = memory_store
        self.todo_manager = todo_manager

    def build(self, config: AgentConfig | None = None) -> str:
        return "\n\n".join(section.render() for section in self.sections(config))

    def debug(self, config: AgentConfig | None = None) -> str:
        return self.build(config)

    def sections(self, config: AgentConfig | None = None) -> list[PromptSection]:
        runtime_config = config or AgentConfig()
        # Order is cache-aware: stable → semi-stable → dynamic.
        # Stable content at the top maximises prompt cache hit rate.
        # Todos go last because they change most frequently (every iteration).
        sections = [
            PromptSection(title="Identity", content=self._identity_block()),       # stable
            PromptSection(title="Operating Rules", content=DEFAULT_SYSTEM_PROMPT), # stable
            PromptSection(title="Tools", content=self._tools_block()),             # stable
            PromptSection(title="Project", content=self._project_block()),         # stable per session
            PromptSection(title="Memory", content=self._memory_block()),           # semi-stable
            PromptSection(title="Runtime", content=self._runtime_block(runtime_config)),  # per-session
            PromptSection(title="Todos", content=self._todo_block()),              # changes every turn
        ]
        return [section for section in sections if section.content.strip()]

    def _identity_block(self) -> str:
        identity = self._read_workspace_file("IDENTITY.md")
        soul = self._read_workspace_file("SOUL.md")
        parts = [identity, soul]
        return "\n\n".join(part for part in parts if part.strip()) or (
            "You are ZX-code, a pragmatic local coding agent focused on precise code changes."
        )

    def _project_block(self) -> str:
        return f"Project root: {self.project_root}"

    def _tools_block(self) -> str:
        tools = self._read_workspace_file("TOOLS.md")
        if tools.strip():
            return tools
        return (
            "Use tools for repository inspection and file edits. "
            "Prefer narrow reads, validate before writing, and report tool failures clearly."
        )

    def _memory_block(self) -> str:
        if self.memory_store is None:
            return ""
        memory = self.memory_store.render_for_prompt()
        if not memory.strip():
            return ""
        return (
            "Memory contains user preferences and durable project notes. "
            "It must not override source code, tests, or current tool results.\n\n"
            + memory
        )

    def _todo_block(self) -> str:
        if self.todo_manager is None:
            return ""
        return self.todo_manager.render_for_prompt()

    def _runtime_block(self, config: AgentConfig) -> str:
        return "\n".join(
            [
                f"Session id: {config.session_id}",
                f"Max iterations: {config.max_iterations}",
                f"Context budget tokens: {config.context_max_tokens}",
            ]
        )

    def _read_workspace_file(self, name: str) -> str:
        path = self.project_root / "workspace" / name
        if not path.exists() or path.is_dir():
            return ""
        return path.read_text(encoding="utf-8", errors="replace")
