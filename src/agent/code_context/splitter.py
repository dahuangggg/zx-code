from __future__ import annotations

import ast
from pathlib import Path

from agent.code_context.models import CodeChunk


LANGUAGE_BY_EXTENSION = {
    ".py": "python",
    ".md": "markdown",
    ".txt": "text",
    ".toml": "toml",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".json": "json",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".java": "java",
    ".go": "go",
    ".rs": "rust",
}


def split_file(
    path: str | Path,
    *,
    codebase_path: str | Path,
    max_chars: int = 1800,
    overlap_lines: int = 2,
) -> list[CodeChunk]:
    file_path = Path(path)
    root = Path(codebase_path).resolve()
    text = file_path.read_text(encoding="utf-8", errors="replace")
    language = LANGUAGE_BY_EXTENSION.get(file_path.suffix, file_path.suffix.lstrip(".") or "text")
    relative_path = file_path.resolve().relative_to(root).as_posix()
    if file_path.suffix == ".py":
        chunks = _split_python(text, relative_path, language, max_chars=max_chars)
        if chunks:
            return chunks
    return _split_lines(
        text,
        relative_path,
        language,
        max_chars=max_chars,
        overlap_lines=overlap_lines,
    )


def _split_python(
    text: str,
    relative_path: str,
    language: str,
    *,
    max_chars: int,
) -> list[CodeChunk]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    lines = text.splitlines()
    chunks: list[CodeChunk] = []
    for node in tree.body:
        if not isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        start = getattr(node, "lineno", 1)
        end = getattr(node, "end_lineno", start)
        content = "\n".join(lines[start - 1 : end])
        if len(content) <= max_chars:
            chunks.append(
                CodeChunk(
                    relative_path=relative_path,
                    language=language,
                    start_line=start,
                    end_line=end,
                    content=content,
                    symbol=node.name,
                )
            )
            continue
        chunks.extend(
            chunk.model_copy(update={"symbol": node.name})
            for chunk in _split_lines(
                content,
                relative_path,
                language,
                max_chars=max_chars,
                overlap_lines=1,
                start_line_offset=start - 1,
            )
        )
    return chunks


def _split_lines(
    text: str,
    relative_path: str,
    language: str,
    *,
    max_chars: int,
    overlap_lines: int,
    start_line_offset: int = 0,
) -> list[CodeChunk]:
    lines = text.splitlines()
    if not lines:
        return []
    chunks: list[CodeChunk] = []
    index = 0
    while index < len(lines):
        start = index
        current: list[str] = []
        current_len = 0
        while index < len(lines):
            line = lines[index]
            line_len = len(line) + 1
            if current and current_len + line_len > max_chars:
                break
            current.append(line)
            current_len += line_len
            index += 1
        if not current:
            current.append(lines[index])
            index += 1
        chunks.append(
            CodeChunk(
                relative_path=relative_path,
                language=language,
                start_line=start_line_offset + start + 1,
                end_line=start_line_offset + index,
                content="\n".join(current),
            )
        )
        if index >= len(lines):
            break
        index = max(index - overlap_lines, start + 1)
    return chunks
