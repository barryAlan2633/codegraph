"""Parser registry -- maps file extensions to language parser instances."""

from __future__ import annotations

import logging
from pathlib import Path

from codegraph.parser.base import LanguageParser, ParseResult

logger = logging.getLogger(__name__)


class ParserRegistry:
    """Singleton-style registry that lazily instantiates parsers on first use."""

    def __init__(self) -> None:
        self._parsers: dict[str, LanguageParser] = {}
        self._loaded = False

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True

        # Python
        try:
            from codegraph.parser.python import PythonParser
            self._register(PythonParser())
        except Exception as exc:
            logger.debug("Python parser unavailable: %s", exc)

        # Rust
        try:
            from codegraph.parser.rust import RustParser
            self._register(RustParser())
        except Exception as exc:
            logger.debug("Rust parser unavailable: %s", exc)

        # TypeScript / JavaScript
        try:
            from codegraph.parser.typescript import TypeScriptParser
            self._register(TypeScriptParser())
        except Exception as exc:
            logger.debug("TypeScript parser unavailable: %s", exc)

    def _register(self, parser: LanguageParser) -> None:
        for ext in parser.extensions:
            self._parsers[ext] = parser

    def get_parser(self, file_path: Path | str) -> LanguageParser | None:
        """Return the parser for *file_path*, or ``None`` if unsupported."""
        self._ensure_loaded()
        ext = Path(file_path).suffix.lower()
        return self._parsers.get(ext)

    def parse_file(self, file_path: Path, source: bytes | None = None) -> ParseResult | None:
        """Convenience: look up parser and parse in one call."""
        parser = self.get_parser(file_path)
        if parser is None:
            return None
        return parser.parse_file(file_path, source)

    def supported_extensions(self) -> set[str]:
        """Return every registered extension."""
        self._ensure_loaded()
        return set(self._parsers.keys())


# Module-level singleton
_registry = ParserRegistry()

get_parser = _registry.get_parser
parse_file = _registry.parse_file
supported_extensions = _registry.supported_extensions
