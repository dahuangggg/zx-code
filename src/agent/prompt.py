"""prompt — 管线式 System Prompt 构建器（s10）。

``SystemPromptBuilder`` 将 system prompt 分解为多个独立 section，
每个 section 是一个标题 + 内容的 Markdown 片段：

  Identity          — 读取 workspace/IDENTITY.md + SOUL.md（稳定，缓存友好）
  Operating Rules   — 内置默认行为准则（稳定）
  Tools             — 工具列表索引（稳定，工具不变则不变）
  Project           — 项目根路径（稳定）
  Project Instructions — CLAUDE.md 内容（稳定）
  Skills            — 技能名称索引（稳定）
  Memory            — MEMORY.md 索引（半稳定）
  Runtime           — 日期/session_id/模型等（每次会话变化）
  Tasks             — 任务状态（随任务变化）
  Todos             — Todo 列表（每轮都变，放最后）

排列顺序遵循"稳定内容在前"原则，最大化 prompt cache 命中率。
空 section 自动跳过，不出现在最终 prompt 中。
"""

from __future__ import annotations


from datetime import date
from pathlib import Path
import platform
from typing import Any

from pydantic import BaseModel, ConfigDict

from agent.state.memory import MemoryStore
from agent.models import RuntimeConfig
from agent.state.skills import SkillStore
from agent.state.tasks import TaskStore
from agent.state.todo import TodoManager


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
        skill_store: SkillStore | None = None,
        task_store: TaskStore | None = None,
        todo_manager: TodoManager | None = None,
    ) -> None:
        self.project_root = Path(project_root)
        self.memory_store = memory_store
        self.skill_store = skill_store
        self.task_store = task_store
        self.todo_manager = todo_manager

    def build(
        self,
        config: RuntimeConfig | None = None,
        *,
        tool_schemas: list[dict[str, Any]] | None = None,
    ) -> str:
        return "\n\n".join(
            section.render()
            for section in self.sections(config, tool_schemas=tool_schemas)
        )

    def debug(
        self,
        config: RuntimeConfig | None = None,
        *,
        tool_schemas: list[dict[str, Any]] | None = None,
    ) -> str:
        return self.build(config, tool_schemas=tool_schemas)

    def sections(
        self,
        config: RuntimeConfig | None = None,
        *,
        tool_schemas: list[dict[str, Any]] | None = None,
    ) -> list[PromptSection]:
        runtime_config = config or RuntimeConfig()
        # Order is cache-aware: stable → semi-stable → dynamic.
        # Stable content at the top maximises prompt cache hit rate.
        # Todos go last because they change most frequently (every iteration).
        sections = [
            PromptSection(title="Identity", content=self._identity_block()),       # stable
            PromptSection(title="Operating Rules", content=DEFAULT_SYSTEM_PROMPT), # stable
            PromptSection(title="Tools", content=self._tools_block(tool_schemas or [])),  # stable
            PromptSection(title="Project", content=self._project_block()),         # stable per session
            PromptSection(title="Project Instructions", content=self._project_instructions_block()),  # stable
            PromptSection(title="Skills", content=self._skills_block()),           # stable
            PromptSection(title="Memory", content=self._memory_block()),           # semi-stable
            PromptSection(title="Runtime", content=self._runtime_block(runtime_config)),  # per-session
            PromptSection(title="Tasks", content=self._task_block()),              # changes by task state
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

    def _project_instructions_block(self) -> str:
        return self._read_project_file("CLAUDE.md")

    def _tools_block(self, tool_schemas: list[dict[str, Any]]) -> str:
        guidance = self._read_workspace_file("TOOLS.md").strip()
        if not guidance:
            guidance = (
                "Use tools for repository inspection and file edits. "
                "Prefer narrow reads, validate before writing, and report tool failures clearly."
            )
        guidance += (
            "\nWhen a task requires understanding an unfamiliar codebase, architecture "
            "boundaries, ownership, or natural-language code locations, prefer code_search "
            "before broad grep/read_file exploration. Use read_file after code_search when "
            "you need exact surrounding lines or when preparing edits."
        )
        index = self._tool_index(tool_schemas)
        return "\n\n".join(part for part in (guidance, index) if part.strip())

    def _tool_index(self, tool_schemas: list[dict[str, Any]]) -> str:
        if not tool_schemas:
            return ""
        lines = ["Available tools:"]
        for schema in tool_schemas:
            function = schema.get("function", {})
            name = str(function.get("name", "")).strip()
            if not name:
                continue
            description = str(function.get("description", "")).strip()
            suffix = f" {description}" if description else ""
            lines.append(f"- {name}:{suffix}".rstrip())
        return "\n".join(lines)

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

    def _skills_block(self) -> str:
        if self.skill_store is None:
            return ""
        return self.skill_store.render_index()

    def _task_block(self) -> str:
        if self.task_store is None:
            return ""
        return self.task_store.render_for_prompt()

    def _todo_block(self) -> str:
        if self.todo_manager is None:
            return ""
        return self.todo_manager.render_for_prompt()

    def _runtime_block(self, config: RuntimeConfig) -> str:
        return "\n".join(
            [
                f"Current date: {date.today().isoformat()}",
                f"Session id: {config.session_id}",
                f"Model: {config.model}",
                f"Max iterations: {config.max_iterations}",
                f"Context budget tokens: {config.context_max_tokens}",
                f"Platform: {platform.platform()}",
                f"Python: {platform.python_version()}",
            ]
        )

    def _read_workspace_file(self, name: str) -> str:
        path = self.project_root / "workspace" / name
        if not path.exists() or path.is_dir():
            return ""
        return path.read_text(encoding="utf-8", errors="replace")

    def _read_project_file(self, name: str) -> str:
        path = self.project_root / name
        if not path.exists() or path.is_dir():
            return ""
        return path.read_text(encoding="utf-8", errors="replace")
