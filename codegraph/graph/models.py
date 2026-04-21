"""Graph data models — nodes and edges for the codebase knowledge graph."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum


class EdgeKind(str, Enum):
    """Types of relationships between symbols."""

    CALLS = "CALLS"
    IMPORTS = "IMPORTS"
    CONTAINS = "CONTAINS"
    DEPENDS_ON = "DEPENDS_ON"
    IMPLEMENTS = "IMPLEMENTS"
    EXPORTS = "EXPORTS"


def _make_id(file_path: str, qualified_name: str) -> str:
    """Deterministic SHA-256 based ID for a symbol node."""
    raw = f"{file_path}:{qualified_name}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass
class GraphNode:
    """A symbol node stored in the graph database."""

    kind: str
    name: str
    qualified_name: str
    file_path: str
    line_start: int
    line_end: int
    signature: str = ""
    docstring: str = ""
    language: str = ""
    id: str = field(init=False, default="")

    def __post_init__(self) -> None:
        self.id = _make_id(self.file_path, self.qualified_name)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "name": self.name,
            "qualified_name": self.qualified_name,
            "signature": self.signature,
            "docstring": self.docstring,
            "file_path": self.file_path,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "language": self.language,
        }


@dataclass
class GraphEdge:
    """A relationship edge stored in the graph database."""

    source_id: str
    target_id: str
    kind: str  # one of EdgeKind values
    file_path: str = ""
    line_number: int = 0

    def to_dict(self) -> dict:
        return {
            "source_id": self.source_id,
            "target_id": self.target_id,
            "kind": self.kind,
            "file_path": self.file_path,
            "line_number": self.line_number,
        }
