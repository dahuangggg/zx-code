from __future__ import annotations

import fnmatch
from pathlib import Path


DEFAULT_SUPPORTED_EXTENSIONS = {
    ".py",
    ".md",
    ".txt",
    ".toml",
    ".yaml",
    ".yml",
    ".json",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".java",
    ".go",
    ".rs",
}

DEFAULT_IGNORE_PATTERNS = {
    ".git/**",
    ".venv/**",
    "node_modules/**",
    "dist/**",
    "build/**",
    "__pycache__/**",
    ".pytest_cache/**",
    ".agent/**",
    ".memory/**",
    ".tasks/**",
    "*.lock",
    ".env",
    ".env.*",
}


def resolve_codebase_path(path: str | Path | None) -> Path:
    if path is None or str(path).strip() == "":
        return Path.cwd().resolve()
    return Path(path).expanduser().resolve()


def load_ignore_patterns(codebase_path: str | Path) -> list[str]:
    root = Path(codebase_path)
    patterns = set(DEFAULT_IGNORE_PATTERNS)
    for ignore_file in (".gitignore", ".contextignore"):
        path = root / ignore_file
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
) -> list[Path]:
    root = Path(codebase_path).resolve()
    extensions = supported_extensions or DEFAULT_SUPPORTED_EXTENSIONS
    patterns = ignore_patterns or load_ignore_patterns(root)
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


def _is_ignored(relative_path: str, patterns: list[str]) -> bool:
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
