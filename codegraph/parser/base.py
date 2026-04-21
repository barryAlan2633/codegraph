"""Core dataclasses and protocol for language parsers."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Protocol, runtime_checkable


class SymbolKind(str, Enum):
    FUNCTION = "function"
    CLASS = "class"
    METHOD = "method"
    MODULE = "module"
    VARIABLE = "variable"
    INTERFACE = "interface"
    STRUCT = "struct"
    ENUM = "enum"
    TRAIT = "trait"
    IMPL = "impl"
    TYPE_ALIAS = "type_alias"


@dataclass(frozen=True, slots=True)
class ParsedSymbol:
    """A symbol (function, class, method, ...) extracted from source."""

    kind: SymbolKind
    name: str
    qualified_name: str
    signature: str
    docstring: str | None
    file_path: str
    line_start: int
    line_end: int
    parent: str | None = None
    decorators: list[str] = field(default_factory=list)
    is_async: bool = False
    is_public: bool = True


@dataclass(frozen=True, slots=True)
class ParsedImport:
    """An import/use statement."""

    source: str
    names: list[str]
    alias: str | None
    file_path: str
    line_number: int


@dataclass(frozen=True, slots=True)
class ParsedCall:
    """A function/method call-site."""

    caller: str
    callee: str
    file_path: str
    line_number: int


@dataclass(slots=True)
class ParseResult:
    """Aggregate output of parsing a single file."""

    symbols: list[ParsedSymbol] = field(default_factory=list)
    imports: list[ParsedImport] = field(default_factory=list)
    calls: list[ParsedCall] = field(default_factory=list)
    exports: list[str] = field(default_factory=list)


@runtime_checkable
class LanguageParser(Protocol):
    """Contract every language parser must satisfy."""

    @property
    def extensions(self) -> list[str]:
        """File extensions this parser handles (e.g. ['.py'])."""
        ...

    def parse_file(self, file_path: Path, source: bytes | None = None) -> ParseResult:
        """Parse *file_path* and return structured results.

        If *source* is provided it is used directly; otherwise the file is
        read from disk.
        """
        ...
