"""File hash manager — tracks content changes for incremental indexing."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import NamedTuple


HASH_FILE_NAME = "hashes.json"


class HashDiff(NamedTuple):
    """Result of comparing two hash snapshots."""

    added: set[str]
    modified: set[str]
    deleted: set[str]


def sha256_file(path: Path) -> str:
    """Return the hex SHA-256 digest of a file's contents."""
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                h.update(chunk)
    except OSError:
        return ""
    return h.hexdigest()


def compute_hashes(file_paths: list[Path]) -> dict[str, str]:
    """Compute SHA-256 hashes for a list of files.

    Returns a dict mapping ``str(path)`` -> hex digest.
    """
    return {str(p): sha256_file(p) for p in file_paths}


def _hash_store_path(repo_root: Path) -> Path:
    """Return the path to the hash store JSON file."""
    return repo_root / ".codegraph" / HASH_FILE_NAME


def load_hashes(repo_root: Path) -> dict[str, str]:
    """Load previously saved hashes from ``.codegraph/hashes.json``."""
    path = _hash_store_path(repo_root)
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, OSError):
        pass
    return {}


def save_hashes(repo_root: Path, hashes: dict[str, str]) -> None:
    """Persist hashes to ``.codegraph/hashes.json``."""
    path = _hash_store_path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(hashes, f, indent=2, sort_keys=True)


def diff(old: dict[str, str], new: dict[str, str]) -> HashDiff:
    """Compare two hash snapshots and categorise changes.

    Returns
    -------
    HashDiff(added, modified, deleted) where each element is a set of
    file path strings.
    """
    old_keys = set(old.keys())
    new_keys = set(new.keys())

    added = new_keys - old_keys
    deleted = old_keys - new_keys
    common = old_keys & new_keys
    modified = {k for k in common if old[k] != new[k]}

    return HashDiff(added=added, modified=modified, deleted=deleted)
