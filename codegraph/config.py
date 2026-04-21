"""Configuration via Pydantic settings — loads from env vars and .codegraph.toml."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _load_toml_config(repo_path: Path | None) -> dict[str, Any]:
    """Read .codegraph.toml from the repo root, if it exists."""
    if repo_path is None:
        return {}
    toml_path = Path(repo_path) / ".codegraph.toml"
    if not toml_path.is_file():
        return {}
    with open(toml_path, "rb") as f:
        return tomllib.load(f)


class Settings(BaseSettings):
    """Central configuration for codegraph.

    Resolution order (highest priority first):
        1. Explicit constructor kwargs
        2. Environment variables (prefixed CODEGRAPH_)
        3. Values from .codegraph.toml in repo_path
        4. Field defaults
    """

    model_config = SettingsConfigDict(
        env_prefix="CODEGRAPH_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    # ── Repository ──────────────────────────────────────────────
    repo_path: Path = Field(
        default=Path("."),
        description="Root of the repository to analyse.",
    )

    # ── Neo4j ───────────────────────────────────────────────────
    neo4j_uri: str = Field(
        default="bolt://localhost:7687",
        description="Neo4j Bolt URI.",
    )
    neo4j_user: str = Field(default="neo4j")
    neo4j_password: str = Field(default="password")
    neo4j_database: str = Field(default="neo4j")

    # ── Parsing ─────────────────────────────────────────────────
    languages: list[str] = Field(
        default_factory=lambda: ["python", "javascript", "typescript", "rust"],
        description="Languages to parse.",
    )
    ignore_patterns: list[str] = Field(
        default_factory=lambda: [
            "__pycache__",
            "node_modules",
            ".git",
            "target",
            "dist",
            "build",
            ".venv",
            "venv",
        ],
        description="Glob patterns for directories/files to skip.",
    )
    max_file_size_kb: int = Field(
        default=512,
        description="Skip files larger than this (kilobytes).",
    )

    # ── TOML overlay ────────────────────────────────────────────
    @model_validator(mode="before")
    @classmethod
    def _merge_toml(cls, values: dict[str, Any]) -> dict[str, Any]:
        repo_path = values.get("repo_path") or values.get("CODEGRAPH_REPO_PATH")
        toml_data = _load_toml_config(Path(repo_path) if repo_path else None)
        # TOML values serve as defaults — explicit/env values take precedence
        merged = {**toml_data, **{k: v for k, v in values.items() if v is not None}}
        return merged


def get_settings(**overrides: Any) -> Settings:
    """Convenience constructor."""
    return Settings(**overrides)
