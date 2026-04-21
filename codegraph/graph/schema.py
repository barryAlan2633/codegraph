"""Cypher schema statements — constraints, indexes, and fulltext search."""

# Unique constraint on Symbol.id
CONSTRAINT_SYMBOL_ID = (
    "CREATE CONSTRAINT symbol_id_unique IF NOT EXISTS "
    "FOR (s:Symbol) REQUIRE s.id IS UNIQUE"
)

# B-tree indexes for common lookup patterns
INDEX_SYMBOL_NAME = (
    "CREATE INDEX symbol_name IF NOT EXISTS "
    "FOR (s:Symbol) ON (s.name)"
)

INDEX_SYMBOL_KIND = (
    "CREATE INDEX symbol_kind IF NOT EXISTS "
    "FOR (s:Symbol) ON (s.kind)"
)

INDEX_SYMBOL_FILE_PATH = (
    "CREATE INDEX symbol_file_path IF NOT EXISTS "
    "FOR (s:Symbol) ON (s.file_path)"
)

INDEX_SYMBOL_QUALIFIED_NAME = (
    "CREATE INDEX symbol_qualified_name IF NOT EXISTS "
    "FOR (s:Symbol) ON (s.qualified_name)"
)

# Fulltext index for fuzzy search across name, qualified_name, and docstring
FULLTEXT_INDEX_SYMBOL_SEARCH = (
    "CREATE FULLTEXT INDEX symbol_fulltext IF NOT EXISTS "
    "FOR (s:Symbol) ON EACH [s.name, s.qualified_name, s.docstring]"
)

# All schema statements in order
ALL_SCHEMA_STATEMENTS: list[str] = [
    CONSTRAINT_SYMBOL_ID,
    INDEX_SYMBOL_NAME,
    INDEX_SYMBOL_KIND,
    INDEX_SYMBOL_FILE_PATH,
    INDEX_SYMBOL_QUALIFIED_NAME,
    FULLTEXT_INDEX_SYMBOL_SEARCH,
]
