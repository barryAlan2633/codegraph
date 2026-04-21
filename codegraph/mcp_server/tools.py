"""MCP tool definitions and handlers for codegraph."""

from __future__ import annotations

import asyncio
from typing import Any

from mcp.server import Server
from mcp.types import Tool, TextContent

from codegraph.graph.client import Neo4jClient
from codegraph.graph import queries


def _query(client: Neo4jClient, cypher: str, params: dict[str, Any] | None = None) -> list[dict]:
    """Synchronous query helper."""
    return client.query(cypher, params)


def register_tools(server: Server, client: Neo4jClient) -> None:
    """Register all codegraph tools on the MCP server instance."""

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(
                name="codebase_overview",
                description="List all indexed files with symbol counts, grouped by directory.",
                inputSchema={
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            ),
            Tool(
                name="get_symbol",
                description="Get a symbol's definition, signature, docstring, location, callers, and callees.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Symbol name or qualified name to look up.",
                        },
                    },
                    "required": ["name"],
                },
            ),
            Tool(
                name="find_references",
                description="Find all call sites and imports that reference a symbol.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Symbol name to find references for.",
                        },
                    },
                    "required": ["name"],
                },
            ),
            Tool(
                name="get_dependencies",
                description="List all imports for a file or module.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "File path (relative to repo root).",
                        },
                    },
                    "required": ["path"],
                },
            ),
            Tool(
                name="search_symbols",
                description="Fuzzy search across all indexed symbols by name.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search query string.",
                        },
                    },
                    "required": ["query"],
                },
            ),
            Tool(
                name="get_file_structure",
                description="List all symbols defined in a file (without full source code).",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "File path (relative to repo root).",
                        },
                    },
                    "required": ["path"],
                },
            ),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any] | None) -> list[TextContent]:
        arguments = arguments or {}
        try:
            if name == "codebase_overview":
                text = await asyncio.to_thread(_handle_codebase_overview, client)
            elif name == "get_symbol":
                text = await asyncio.to_thread(_handle_get_symbol, client, arguments["name"])
            elif name == "find_references":
                text = await asyncio.to_thread(_handle_find_references, client, arguments["name"])
            elif name == "get_dependencies":
                text = await asyncio.to_thread(_handle_get_dependencies, client, arguments["path"])
            elif name == "search_symbols":
                text = await asyncio.to_thread(_handle_search_symbols, client, arguments["query"])
            elif name == "get_file_structure":
                text = await asyncio.to_thread(_handle_get_file_structure, client, arguments["path"])
            else:
                text = f"Unknown tool: {name}"
        except KeyError as exc:
            text = f"Missing required parameter: {exc}"
        except Exception as exc:
            text = f"Error: {exc}"

        return [TextContent(type="text", text=text)]


# ── Handler implementations ────────────────────────────────────────


def _handle_codebase_overview(client: Neo4jClient) -> str:
    rows = _query(client, queries.CODEBASE_OVERVIEW)
    if not rows:
        return "No files indexed. Run `codegraph init` first."

    # Group by directory
    dirs: dict[str, list[dict]] = {}
    total_files = 0
    total_symbols = 0
    for r in rows:
        file_path = r["file_path"] or "<unknown>"
        parts = file_path.rsplit("/", 1)
        directory = parts[0] if len(parts) > 1 else "."
        dirs.setdefault(directory, []).append(r)
        total_files += 1
        total_symbols += r["symbol_count"]

    lines: list[str] = []
    for directory in sorted(dirs.keys()):
        files = dirs[directory]
        lines.append(f"\n{directory}/")
        for f in sorted(files, key=lambda x: x["file_path"]):
            fname = f["file_path"].rsplit("/", 1)[-1]
            count = f["symbol_count"]
            kinds = f.get("kinds") or []
            kinds_str = f" [{', '.join(str(k) for k in kinds)}]" if kinds else ""
            lines.append(f"  {fname}{kinds_str} {count} symbols")

    header = f"Codebase: {total_files} files, {total_symbols} symbols"
    return header + "\n" + "\n".join(lines)


def _handle_get_symbol(client: Neo4jClient, name: str) -> str:
    rows = _query(client, queries.GET_SYMBOL, {"name": name})
    if not rows:
        return f"Symbol '{name}' not found."

    row = rows[0]
    # GET_SYMBOL returns s{.*} which the driver may nest under key "s"
    s = row.get("s", row)
    if isinstance(s, dict) and "name" in s:
        r = s
    else:
        r = row

    parts: list[str] = []

    kind = str(r.get("kind", "symbol")).capitalize()
    sym_name = r.get("name", name)
    parts.append(f"{kind}: {sym_name}")

    if r.get("file_path"):
        loc = r["file_path"]
        if r.get("line_start"):
            loc += f":{r['line_start']}"
            if r.get("line_end") and r["line_end"] != r["line_start"]:
                loc += f"-{r['line_end']}"
        parts.append(f"File: {loc}")

    if r.get("qualified_name"):
        parts.append(f"Qualified: {r['qualified_name']}")

    if r.get("signature"):
        parts.append(f"Signature: {r['signature']}")

    if r.get("docstring"):
        parts.append(f"Docstring: {r['docstring']}")

    # Incoming relationships (callers)
    incoming = [i for i in (r.get("incoming") or []) if i.get("source")]
    if incoming:
        caller_strs = [i["source"] for i in incoming]
        parts.append(f"Called by: {', '.join(caller_strs)}")

    # Outgoing relationships (callees)
    outgoing = [o for o in (r.get("outgoing") or []) if o.get("target")]
    if outgoing:
        callee_strs = [o["target"] for o in outgoing]
        parts.append(f"Calls: {', '.join(callee_strs)}")

    return "\n".join(parts)


def _handle_find_references(client: Neo4jClient, name: str) -> str:
    rows = _query(client, queries.FIND_REFERENCES, {"name": name})
    if not rows:
        return f"No references found for '{name}'."

    parts: list[str] = [f"References to '{name}' ({len(rows)} found)"]

    call_refs = [r for r in rows if r.get("relationship") == "CALLS"]
    import_refs = [r for r in rows if r.get("relationship") == "IMPORTS"]

    if call_refs:
        parts.append("\nCall sites:")
        for r in call_refs:
            loc = r.get("file_path", "")
            if r.get("line_number"):
                loc += f":{r['line_number']}"
            referrer = r.get("referrer", "?")
            parts.append(f"  {referrer} ({loc})")

    if import_refs:
        parts.append("\nImported by:")
        for r in import_refs:
            loc = r.get("file_path", "")
            if r.get("line_number"):
                loc += f":{r['line_number']}"
            referrer = r.get("referrer", "?")
            parts.append(f"  {referrer} ({loc})")

    return "\n".join(parts)


def _handle_get_dependencies(client: Neo4jClient, path: str) -> str:
    rows = _query(client, queries.GET_DEPENDENCIES, {"file_path": path})
    if not rows:
        return f"No dependencies found for '{path}'."

    parts: list[str] = [f"Dependencies of {path} ({len(rows)} imports)"]
    for r in rows:
        dep = r.get("dependency", "?")
        dep_file = r.get("dep_file", "")
        line = r.get("line_number")
        loc = f" (L{line})" if line else ""
        file_info = f" [{dep_file}]" if dep_file else ""
        parts.append(f"  {dep}{file_info}{loc}")

    return "\n".join(parts)


def _handle_search_symbols(client: Neo4jClient, query: str) -> str:
    # Fulltext search requires Lucene query syntax.
    safe_query = query.replace("~", "\\~").replace("*", "\\*")
    if not any(c in safe_query for c in ("+", "-", '"', "(")):
        safe_query = f"{safe_query}~"

    try:
        rows = _query(client, queries.SEARCH_SYMBOLS, {"query": safe_query, "limit": 25})
    except Exception:
        # Fallback if fulltext index is not available
        fallback = """
        MATCH (s:Symbol)
        WHERE toLower(s.name) CONTAINS toLower($query)
           OR toLower(s.qualified_name) CONTAINS toLower($query)
        RETURN s.kind AS kind, s.name AS name,
               s.qualified_name AS qualified_name,
               s.file_path AS file_path,
               s.line_start AS line_start,
               s.signature AS signature
        ORDER BY
            CASE WHEN toLower(s.name) = toLower($query) THEN 0
                 WHEN toLower(s.name) STARTS WITH toLower($query) THEN 1
                 ELSE 2
            END,
            s.name
        LIMIT 25
        """
        rows = _query(client, fallback, {"query": query})

    if not rows:
        return f"No symbols matching '{query}'."

    parts: list[str] = [f"Search results for '{query}' ({len(rows)} matches)"]
    for r in rows:
        kind = str(r.get("kind", "?")).capitalize()
        loc = r.get("file_path") or ""
        if r.get("line_start"):
            loc += f":{r['line_start']}"
        sig = r.get("signature") or r.get("name", "?")
        parts.append(f"  {kind}: {sig}")
        if loc:
            parts.append(f"    {loc}")

    return "\n".join(parts)


def _handle_get_file_structure(client: Neo4jClient, path: str) -> str:
    rows = _query(client, queries.GET_FILE_STRUCTURE, {"file_path": path})
    if not rows:
        return f"No symbols found in '{path}'. File may not be indexed."

    parts: list[str] = [f"Structure of {path} ({len(rows)} symbols)"]
    for r in rows:
        kind = str(r.get("kind", "?")).capitalize()
        line_info = ""
        if r.get("line_start"):
            line_info = f" L{r['line_start']}"
            if r.get("line_end") and r["line_end"] != r["line_start"]:
                line_info += f"-{r['line_end']}"

        sig = r.get("signature") or r.get("name", "?")
        parts.append(f"  {kind}{line_info}: {sig}")

    return "\n".join(parts)
