"""state.skills — 两层技能加载（s05）。

技能文件格式：YAML frontmatter（含 name/description）+ Markdown 正文。
存放于 ``skills/`` 目录（或 workspace/skills/），每个技能一个 .md 文件。

两层加载策略（节省 token）：
  第一层：``render_index()`` 生成技能名+描述的简短索引，注入 system prompt
  第二层：agent 调用 ``load_skill`` 工具传入技能名，获取完整 Markdown 正文

路径安全：``load()`` 验证解析后的路径在 ``skill_root`` 内，防止路径穿越攻击。
"""

from __future__ import annotations


from pathlib import Path
from typing import Any

import frontmatter
from pydantic import BaseModel, ConfigDict, Field


class SkillMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    path: Path
    title: str = ""
    description: str = ""


class SkillDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metadata: SkillMetadata
    frontmatter: dict[str, Any] = Field(default_factory=dict)
    content: str


class SkillStore:
    """File-backed skill loader.

    The prompt receives only the compact index. Full markdown is loaded through
    the load_skill tool so rarely used skills do not permanently consume tokens.
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser()

    def list(self) -> list[SkillMetadata]:
        if not self.root.exists():
            return []
        skills = [self._metadata_for(path) for path in sorted(self.root.rglob("*.md"))]
        return sorted(skills, key=lambda item: item.name)

    def load(self, name: str) -> SkillDocument:
        path = self._resolve(name)
        post = frontmatter.loads(path.read_text(encoding="utf-8"))
        metadata = self._metadata_for(path, post=post)
        return SkillDocument(
            metadata=metadata,
            frontmatter=dict(post.metadata),
            content=post.content.strip(),
        )

    def render_index(self, *, max_chars: int = 4000) -> str:
        skills = self.list()
        if not skills:
            return ""
        lines = ["Available skills. Load the full markdown with load_skill when needed:"]
        for skill in skills:
            suffix = f" - {skill.description}" if skill.description else ""
            lines.append(f"- {skill.name}{suffix}")
        rendered = "\n".join(lines)
        if len(rendered) <= max_chars:
            return rendered
        omitted = len(rendered) - max_chars
        return rendered[:max_chars] + f"\n\n[skill index truncated, omitted {omitted} chars]"

    def _resolve(self, name: str) -> Path:
        safe_name = name.strip().removesuffix(".md")
        if not safe_name:
            raise FileNotFoundError("empty skill name")
        candidate = (self.root / f"{safe_name}.md").resolve()
        root = self.root.resolve()
        if root not in candidate.parents and candidate != root:
            raise PermissionError(f"skill path escapes root: {name}")
        if not candidate.exists() or candidate.is_dir():
            raise FileNotFoundError(f"skill not found: {name}")
        return candidate

    def _metadata_for(
        self,
        path: Path,
        *,
        post: frontmatter.Post | None = None,
    ) -> SkillMetadata:
        loaded = post or frontmatter.loads(path.read_text(encoding="utf-8"))
        name = path.relative_to(self.root).with_suffix("").as_posix()
        title = str(loaded.metadata.get("title", "")).strip() or _first_heading(loaded.content)
        description = str(loaded.metadata.get("description", "")).strip()
        return SkillMetadata(
            name=name,
            path=path,
            title=title,
            description=description,
        )


def _first_heading(content: str) -> str:
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
    return ""
