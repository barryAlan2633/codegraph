"""Filesystem walker — discovers source files to parse."""

from __future__ import annotations

import subprocess
from pathlib import Path

from codegraph.parser.registry import supported_extensions

# Directories that are always skipped regardless of user config
_ALWAYS_IGNORE = {
    ".git",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    "dist",
    "build",
    ".eggs",
    "target",       # Rust
    ".codegraph",   # our own metadata
}

DEFAULT_MAX_FILE_SIZE_KB = 512  # skip files larger than this


def _is_git_repo(repo_path: Path) -> bool:
    """Check if *repo_path* is inside a git working tree."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.returncode == 0 and result.stdout.strip() == "true"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _git_ls_files(repo_path: Path) -> list[Path]:
    """Use `git ls-files` to get tracked files (fast, respects .gitignore)."""
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        return []
    paths: list[Path] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if line:
            paths.append(repo_path / line)
    return paths


def _walk_directory(repo_path: Path) -> list[Path]:
    """Plain recursive walk when git is not available."""
    paths: list[Path] = []
    for child in repo_path.rglob("*"):
        if child.is_file():
            paths.append(child)
    return paths


def _should_ignore(path: Path, ignore_patterns: set[str]) -> bool:
    """Return True if any path component matches an ignore pattern."""
    parts = set(path.parts)
    if parts & _ALWAYS_IGNORE:
        return True
    if parts & ignore_patterns:
        return True
    return False


def walk(
    repo_path: str | Path,
    ignore_patterns: set[str] | None = None,
    max_file_size_kb: int = DEFAULT_MAX_FILE_SIZE_KB,
) -> list[Path]:
    """Discover parseable source files under *repo_path*.

    Parameters
    ----------
    repo_path:
        Root directory to scan.
    ignore_patterns:
        Additional directory/file names to skip.
    max_file_size_kb:
        Skip files larger than this (in kilobytes).

    Returns
    -------
    Sorted list of absolute ``Path`` objects.
    """
    repo_path = Path(repo_path).resolve()
    ignore_patterns = ignore_patterns or set()
    extensions = supported_extensions()
    max_bytes = max_file_size_kb * 1024

    # Prefer git ls-files for speed and .gitignore awareness
    if _is_git_repo(repo_path):
        raw_paths = _git_ls_files(repo_path)
    else:
        raw_paths = _walk_directory(repo_path)

    accepted: list[Path] = []
    for p in raw_paths:
        # Extension filter
        if p.suffix not in extensions:
            continue
        # Ignore-pattern filter
        try:
            rel = p.relative_to(repo_path)
        except ValueError:
            rel = p
        if _should_ignore(rel, ignore_patterns):
            continue
        # Size filter
        try:
            if p.stat().st_size > max_bytes:
                continue
        except OSError:
            continue
        accepted.append(p)

    accepted.sort()
    return accepted
