"""文件形式的 skill 索引和加载器。

prompt 只接收紧凑的 name/description 索引。完整 Markdown skill 文件会在路径校验后，
通过 ``load_skill`` 工具按需加载。
"""

from __future__ import annotations


from pathlib import Path
from typing import Any

import frontmatter
from pydantic import BaseModel, ConfigDict, Field


class SkillMetadata(BaseModel):
    """技能索引中展示的轻量元数据。"""

    model_config = ConfigDict(extra="forbid")

    name: str
    path: Path
    title: str = ""
    description: str = ""


class SkillDocument(BaseModel):
    """完整技能文档，包含 frontmatter 和 Markdown 正文。"""

    model_config = ConfigDict(extra="forbid")

    metadata: SkillMetadata
    frontmatter: dict[str, Any] = Field(default_factory=dict)
    content: str


class SkillStore:
    """基于文件的 skill 加载器。

    prompt 只接收紧凑索引。完整 Markdown 通过 load_skill 工具加载，避免低频 skill
    长期占用 token。
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser()

    def list(self) -> list[SkillMetadata]:
        """扫描技能目录，返回按 name 排序的技能索引。"""
        if not self.root.exists():
            return []
        skills = [self._metadata_for(path) for path in sorted(self.root.rglob("*.md"))]
        return sorted(skills, key=lambda item: item.name)

    def load(self, name: str) -> SkillDocument:
        """按技能名加载完整 Markdown 文档。"""
        path = self._resolve(name)
        post = frontmatter.loads(path.read_text(encoding="utf-8"))
        metadata = self._metadata_for(path, post=post)
        return SkillDocument(
            metadata=metadata,
            frontmatter=dict(post.metadata),
            content=post.content.strip(),
        )

    def render_index(self, *, max_chars: int = 4000) -> str:
        """渲染注入 system prompt 的技能索引。"""
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
        """把技能名解析到 root 内的 .md 文件，阻止路径穿越。"""
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
        """从 frontmatter 和首个 heading 中提取技能元数据。"""
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
    """返回 Markdown 正文中的第一个 heading。"""
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
    return ""
