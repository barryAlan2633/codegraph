"""Named Cypher query templates for common codebase exploration tasks."""

# ---------------------------------------------------------------------------
# CODEBASE_OVERVIEW
# List every file with the count of symbols it contains, ordered by count.
# ---------------------------------------------------------------------------
CODEBASE_OVERVIEW = """
MATCH (s:Symbol)
RETURN s.file_path AS file_path,
       count(s)    AS symbol_count,
       collect(DISTINCT s.kind) AS kinds
ORDER BY symbol_count DESC
"""

# ---------------------------------------------------------------------------
# GET_SYMBOL
# Find a symbol by name or qualified_name and return its properties plus
# immediate relationships (both incoming and outgoing).
# ---------------------------------------------------------------------------
GET_SYMBOL = """
MATCH (s:Symbol)
WHERE s.name = $name OR s.qualified_name = $name
WITH s
OPTIONAL MATCH (s)-[r_out]->(target:Symbol)
WITH s, collect(DISTINCT {kind: type(r_out), target: target.qualified_name}) AS outgoing
OPTIONAL MATCH (source:Symbol)-[r_in]->(s)
WITH s, outgoing, collect(DISTINCT {kind: type(r_in), source: source.qualified_name}) AS incoming
RETURN s.id AS id, s.kind AS kind, s.name AS name, s.qualified_name AS qualified_name,
       s.signature AS signature, s.docstring AS docstring,
       s.file_path AS file_path, s.line_start AS line_start, s.line_end AS line_end,
       outgoing, incoming
"""

# ---------------------------------------------------------------------------
# FIND_REFERENCES
# Find all callers and importers of a symbol (by name or qualified_name).
# ---------------------------------------------------------------------------
FIND_REFERENCES = """
MATCH (ref:Symbol)-[r]->(s:Symbol)
WHERE (s.name = $name OR s.qualified_name = $name)
  AND r.kind IN ['CALLS', 'IMPORTS']
RETURN ref.qualified_name AS referrer,
       ref.file_path      AS file_path,
       r.kind             AS relationship,
       r.line_number      AS line_number
ORDER BY ref.file_path, r.line_number
"""

# ---------------------------------------------------------------------------
# GET_DEPENDENCIES
# Return all imports for a given module (file_path).
# ---------------------------------------------------------------------------
GET_DEPENDENCIES = """
MATCH (s:Symbol {file_path: $file_path})-[r {kind: 'IMPORTS'}]->(dep:Symbol)
RETURN DISTINCT dep.qualified_name AS dependency,
       dep.file_path               AS dep_file,
       r.line_number               AS line_number
ORDER BY r.line_number
"""

# ---------------------------------------------------------------------------
# SEARCH_SYMBOLS
# Fulltext search across name, qualified_name, and docstring.
# Returns results ranked by relevance score.
# ---------------------------------------------------------------------------
SEARCH_SYMBOLS = """
CALL db.index.fulltext.queryNodes('symbol_fulltext', $query)
YIELD node, score
RETURN node.id             AS id,
       node.kind           AS kind,
       node.name           AS name,
       node.qualified_name AS qualified_name,
       node.file_path      AS file_path,
       node.line_start     AS line_start,
       node.signature      AS signature,
       score
ORDER BY score DESC
LIMIT $limit
"""

# ---------------------------------------------------------------------------
# GET_FILE_STRUCTURE
# All symbols in a file ordered by line number — gives a structural overview.
# ---------------------------------------------------------------------------
GET_FILE_STRUCTURE = """
MATCH (s:Symbol {file_path: $file_path})
RETURN s.kind           AS kind,
       s.name           AS name,
       s.qualified_name AS qualified_name,
       s.signature      AS signature,
       s.line_start     AS line_start,
       s.line_end       AS line_end
ORDER BY s.line_start
"""
