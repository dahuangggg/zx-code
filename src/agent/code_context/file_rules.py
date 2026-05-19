"""CodeContext 文件发现规则。

iter_code_files() 决定哪些源码会进入索引：先解析代码库根目录，再合并默认忽略规则、
.gitignore/.*ignore 和用户传入规则，最后按扩展名筛选普通文件。
"""
from __future__ import annotations

import fnmatch
from pathlib import Path


DEFAULT_SUPPORTED_EXTENSIONS = {
    ".c",
    ".cpp",
    ".cs",
    ".css",
    ".dart",
    ".go",
    ".h",
    ".hpp",
    ".html",
    ".java",
    ".js",
    ".jsx",
    ".kt",
    ".m",
    ".markdown",
    ".md",
    ".mm",
    ".py",
    ".php",
    ".rb",
    ".rs",
    ".scala",
    ".scss",
    ".sh",
    ".sol",
    ".sql",
    ".swift",
    ".txt",
    ".toml",
    ".ts",
    ".tsx",
    ".yaml",
    ".yml",
    ".json",
}

DEFAULT_IGNORE_PATTERNS = {
    ".git/**",
    ".venv/**",
    "node_modules/**",
    "dist/**",
    "build/**",
    "out/**",
    "target/**",
    "coverage/**",
    ".nyc_output/**",
    ".cache/**",
    "__pycache__/**",
    ".pytest_cache/**",
    ".vscode/**",
    ".idea/**",
    ".agent/**",
    ".memory/**",
    ".tasks/**",
    "*.lock",
    "*.local",
    "*.log",
    "*.min.js",
    "*.min.css",
    "*.min.map",
    "*.bundle.js",
    "*.bundle.css",
    "*.chunk.js",
    "*.vendor.js",
    "*.polyfills.js",
    "*.runtime.js",
    "*.map",
    "*.swp",
    "*.swo",
    ".env",
    ".env.*",
    "logs/**",
    "tmp/**",
    "temp/**",
}


def resolve_codebase_path(path: str | Path | None) -> Path:
    """把空 path 解析为 cwd，其他 path 展开为绝对路径。"""
    if path is None or str(path).strip() == "":
        return Path.cwd().resolve()
    return Path(path).expanduser().resolve()


def load_ignore_patterns(
    codebase_path: str | Path,
    *,
    custom_ignore_patterns: list[str] | set[str] | None = None,
) -> list[str]:
    """加载默认忽略规则、仓库 ignore 文件和用户自定义规则。"""
    root = Path(codebase_path)
    patterns = set(DEFAULT_IGNORE_PATTERNS)
    if custom_ignore_patterns:
        patterns.update(custom_ignore_patterns)
    ignore_files = {root / ".gitignore", *root.glob(".*ignore"), Path.home() / ".contextignore", Path.home() / ".context" / ".contextignore"}
    for path in ignore_files:
        if not path.exists() or path.is_dir():
            continue
        for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            patterns.add(line)
    return sorted(patterns)


def iter_code_files(
    codebase_path: str | Path,
    *,
    supported_extensions: set[str] | None = None,
    ignore_patterns: list[str] | None = None,
    custom_extensions: set[str] | list[str] | None = None,
    custom_ignore_patterns: list[str] | set[str] | None = None,
) -> list[Path]:
    """返回应进入 CodeContext 索引的源码文件列表。"""
    root = Path(codebase_path).resolve()
    extensions = set(supported_extensions or DEFAULT_SUPPORTED_EXTENSIONS)
    if custom_extensions:
        extensions.update(_normalize_extensions(custom_extensions))
    patterns = (
        sorted(set(ignore_patterns) | set(custom_ignore_patterns or []))
        if ignore_patterns is not None
        else load_ignore_patterns(root, custom_ignore_patterns=custom_ignore_patterns)
    )
    files: list[Path] = []

    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        if not path.is_file():
            continue
        if path.is_symlink():
            continue
        relative = path.relative_to(root).as_posix()
        if _is_ignored(relative, patterns):
            continue
        if path.suffix in extensions:
            files.append(path)
    return files


def _normalize_extensions(extensions: set[str] | list[str]) -> set[str]:
    """把 py/js 这类扩展名规范化为 .py/.js。"""
    normalized: set[str] = set()
    for extension in extensions:
        value = extension.strip()
        if not value:
            continue
        normalized.add(value if value.startswith(".") else f".{value}")
    return normalized


def _is_ignored(relative_path: str, patterns: list[str]) -> bool:
    """用 gitignore 风格的简单 glob 判断相对路径是否应跳过。"""
    parts = relative_path.split("/")
    candidates = [relative_path, Path(relative_path).name, *parts]
    for pattern in patterns:
        normalized = pattern.replace("\\", "/").strip("/")
        if not normalized:
            continue
        if pattern.endswith("/"):
            normalized = f"{normalized}/**"
        if any(fnmatch.fnmatch(candidate, normalized) for candidate in candidates):
            return True
        if "/" in normalized and fnmatch.fnmatch(relative_path, normalized):
            return True
        if normalized.endswith("/**"):
            prefix = normalized[:-3]
            if relative_path == prefix or relative_path.startswith(f"{prefix}/"):
                return True
    return False
