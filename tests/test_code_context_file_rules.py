from __future__ import annotations

from pathlib import Path

from agent.code_context.file_rules import iter_code_files, resolve_codebase_path


def test_iter_code_files_uses_defaults_and_excludes_sensitive_paths(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('ok')\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# docs\n", encoding="utf-8")
    (tmp_path / ".env").write_text("SECRET=1\n", encoding="utf-8")
    (tmp_path / "uv.lock").write_text("lock\n", encoding="utf-8")
    (tmp_path / ".agent").mkdir()
    (tmp_path / ".agent" / "state.py").write_text("ignored\n", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "pkg.ts").write_text("ignored\n", encoding="utf-8")

    files = [path.relative_to(tmp_path).as_posix() for path in iter_code_files(tmp_path)]

    assert files == ["README.md", "src/app.py"]


def test_iter_code_files_loads_gitignore_and_contextignore(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text("ignored_by_git.py\n", encoding="utf-8")
    (tmp_path / ".contextignore").write_text("docs/private/**\n", encoding="utf-8")
    (tmp_path / "ignored_by_git.py").write_text("x\n", encoding="utf-8")
    (tmp_path / "kept.py").write_text("x\n", encoding="utf-8")
    (tmp_path / "docs" / "private").mkdir(parents=True)
    (tmp_path / "docs" / "private" / "secret.md").write_text("x\n", encoding="utf-8")

    files = [path.relative_to(tmp_path).as_posix() for path in iter_code_files(tmp_path)]

    assert files == ["kept.py"]


def test_resolve_codebase_path_defaults_to_cwd(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    assert resolve_codebase_path("").resolve() == tmp_path.resolve()
    assert resolve_codebase_path(None).resolve() == tmp_path.resolve()
