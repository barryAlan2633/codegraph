# codegraph

Codebase knowledge graph tool. Parses source code (Python, JavaScript, TypeScript, Rust) into a Neo4j graph and exposes it via an MCP server for AI assistants or a CLI for humans.

## Prerequisites

- Python 3.12+
- Neo4j 5.x (local or remote)

## Installation

```bash
pip install -e .
```

## Quick start

```bash
# Start Neo4j (e.g. via Docker)
docker run -d --name neo4j -p 7687:7687 -p 7474:7474 \
  -e NEO4J_AUTH=neo4j/password neo4j:5

# Index your codebase
codegraph init .

# Check what got indexed
codegraph status

# Look up a symbol
codegraph query MyClassName

# Start the MCP server
codegraph serve
```

## Configuration

codegraph reads configuration from (highest priority first):

1. CLI flags (`--neo4j-uri`, `--neo4j-user`, `--neo4j-password`)
2. Environment variables (prefixed `CODEGRAPH_`)
3. `.codegraph.toml` in the repo root
4. Built-in defaults

Environment variables:

```bash
export CODEGRAPH_NEO4J_URI=bolt://localhost:7687
export CODEGRAPH_NEO4J_USER=neo4j
export CODEGRAPH_NEO4J_PASSWORD=password
export CODEGRAPH_NEO4J_DATABASE=neo4j
export CODEGRAPH_REPO_PATH=/path/to/repo
```

## Claude Code integration

Add to your Claude Code MCP settings (`.claude/settings.json` or project settings):

```json
{
  "mcpServers": {
    "codegraph": {
      "command": "codegraph",
      "args": ["serve"],
      "env": {
        "CODEGRAPH_NEO4J_URI": "bolt://localhost:7687",
        "CODEGRAPH_NEO4J_USER": "neo4j",
        "CODEGRAPH_NEO4J_PASSWORD": "password"
      }
    }
  }
}
```

This gives Claude access to six tools:

| Tool | Description |
|------|-------------|
| `codebase_overview` | List all files with symbol counts, grouped by directory |
| `get_symbol` | Symbol definition, signature, docstring, callers, callees |
| `find_references` | All call sites and imports referencing a symbol |
| `get_dependencies` | Imports for a file or module |
| `search_symbols` | Fuzzy search across all symbols |
| `get_file_structure` | All symbols in a file (no source code) |

## CLI reference

```
codegraph init [REPO_PATH]      Full index of a codebase
codegraph update [REPO_PATH]    Incremental update of changed files
codegraph serve                  Start MCP server (stdio)
codegraph status                 Show graph stats
codegraph stats-csv              Print graph stats as CSV
codegraph clear                  Wipe the database
codegraph query <NAME>           Quick symbol lookup

Global options (all commands):
  --neo4j-uri TEXT        Neo4j Bolt URI
  --neo4j-user TEXT       Neo4j username
  --neo4j-password TEXT   Neo4j password
```

## Supported languages

- Python (`.py`)
- JavaScript (`.js`, `.jsx`)
- TypeScript (`.ts`, `.tsx`)
- Rust (`.rs`)
