"""CodeContext 文件切块逻辑。

Python 文件优先按顶层 class/function/async function 切块，保留 symbol 名称；其他
语言按行数和字符预算切块，并保留少量行重叠，提升跨边界搜索命中率。
"""
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
    """读取单个文件并切成 CodeChunk。"""
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
    """按 Python AST 顶层定义切块；语法错误时返回空让调用方走行切块。"""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    lines = text.splitlines()
    chunks: list[CodeChunk] = []
    body_nodes = [
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
    ]
    if body_nodes:
        preamble_end = min(getattr(node, "lineno", 1) for node in body_nodes) - 1
        preamble_lines = lines[:preamble_end]
        while preamble_lines and not preamble_lines[-1].strip():
            preamble_lines.pop()
        if any(line.strip() for line in preamble_lines):
            chunks.append(
                CodeChunk(
                    relative_path=relative_path,
                    language=language,
                    start_line=1,
                    end_line=len(preamble_lines),
                    content="\n".join(preamble_lines),
                    symbol=Path(relative_path).stem,
                )
            )
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
    """通用行切块，按 max_chars 控制片段大小并保留 overlap。"""
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
