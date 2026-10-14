"""Indexer orchestrator — full and incremental indexing pipelines."""

from __future__ import annotations

import sys

import os
from pathlib import Path

from codegraph.parser.base import ParseResult
from codegraph.parser.registry import parse_file as registry_parse_file
from codegraph.graph.models import GraphNode, GraphEdge, EdgeKind, _make_id
from codegraph.graph.client import Neo4jClient
from codegraph.indexer.walker import walk
from codegraph.indexer.hasher import compute_hashes, load_hashes, save_hashes, diff


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

_EXT_TO_LANG: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".rs": "rust",
}



def _log(*args, **kwargs):
    """Progress/diagnostics go to stderr so stdout stays machine-readable."""
    print(*args, file=sys.stderr, **kwargs)

def _language_for_file(file_path: str) -> str:
    """Derive a language string from the file extension."""
    ext = os.path.splitext(file_path)[1].lower()
    return _EXT_TO_LANG.get(ext, "unknown")


def _do_parse(file_path: Path) -> ParseResult:
    """Parse a single file via the parser registry.

    Falls back to an empty ``ParseResult`` when no parser is available
    so the indexer pipeline never breaks.
    """
    result = registry_parse_file(file_path)
    if result is None:
        return ParseResult()
    return result


# ------------------------------------------------------------------
# Conversion: ParseResult -> GraphNode + GraphEdge
# ------------------------------------------------------------------

def _parse_result_to_graph(
    result: ParseResult,
    file_path: str,
) -> tuple[list[GraphNode], list[GraphEdge]]:
    """Convert a ``ParseResult`` into lists of ``GraphNode`` and ``GraphEdge``.

    CALLS edges are created with the raw callee name in ``target_id`` -- the
    ``_resolve_calls`` post-processing step replaces these with real node IDs.
    """
    language = _language_for_file(file_path)
    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []

    # --- Nodes from symbols ---
    for sym in result.symbols:
        fp = sym.file_path or file_path
        node = GraphNode(
            kind=sym.kind.value if hasattr(sym.kind, "value") else str(sym.kind),
            name=sym.name,
            qualified_name=sym.qualified_name,
            file_path=fp,
            line_start=sym.line_start,
            line_end=sym.line_end,
            signature=sym.signature or "",
            docstring=sym.docstring or "",
            language=language,
        )
        nodes.append(node)

    # --- CONTAINS edges (parent -> child) ---
    for sym in result.symbols:
        if sym.parent:
            fp = sym.file_path or file_path
            parent_id = _make_id(fp, sym.parent)
            child_id = _make_id(fp, sym.qualified_name)
            edges.append(GraphEdge(
                source_id=parent_id,
                target_id=child_id,
                kind=EdgeKind.CONTAINS,
                file_path=fp,
                line_number=sym.line_start,
            ))

    # --- IMPORTS edges ---
    for imp in result.imports:
        fp = imp.file_path or file_path
        importer_id = _make_id(file_path, file_path)
        for name in imp.names:
            target_qname = f"{imp.source}.{name}" if imp.source else name
            target_id = _make_id("", target_qname)
            edges.append(GraphEdge(
                source_id=importer_id,
                target_id=target_id,
                kind=EdgeKind.IMPORTS,
                file_path=fp,
                line_number=imp.line_number,
            ))

    # --- CALLS edges (unresolved -- callee is a raw name, not an id) ---
    for call in result.calls:
        fp = call.file_path or file_path
        caller_id = _make_id(file_path, call.caller)
        edges.append(GraphEdge(
            source_id=caller_id,
            target_id=call.callee,
            kind=EdgeKind.CALLS,
            file_path=fp,
            line_number=call.line_number,
        ))

    # --- EXPORTS edges ---
    if result.exports:
        module_id = _make_id(file_path, file_path)
        for export_name in result.exports:
            for sym in result.symbols:
                if sym.name == export_name:
                    fp = sym.file_path or file_path
                    target_id = _make_id(fp, sym.qualified_name)
                    edges.append(GraphEdge(
                        source_id=module_id,
                        target_id=target_id,
                        kind=EdgeKind.EXPORTS,
                        file_path=file_path,
                    ))
                    break

    return nodes, edges


# ------------------------------------------------------------------
# Call resolution
# ------------------------------------------------------------------

def _resolve_calls(
    edges: list[GraphEdge],
    nodes: list[GraphNode],
) -> list[GraphEdge]:
    """Replace placeholder callee names in CALLS edges with real node IDs.

    Matching strategy (in priority order):
    1. Exact qualified_name match.
    2. Short (last segment) name match -- prefer same-file candidates.

    Unresolvable CALLS edges are dropped to avoid dangling references.
    Non-CALLS edges pass through unchanged.
    """
    by_name: dict[str, list[GraphNode]] = {}
    by_qname: dict[str, GraphNode] = {}
    for n in nodes:
        by_name.setdefault(n.name, []).append(n)
        by_qname[n.qualified_name] = n

    resolved: list[GraphEdge] = []
    for edge in edges:
        if edge.kind != EdgeKind.CALLS:
            resolved.append(edge)
            continue

        callee = edge.target_id

        # 1. Exact qualified_name match
        if callee in by_qname:
            resolved.append(GraphEdge(
                source_id=edge.source_id,
                target_id=by_qname[callee].id,
                kind=edge.kind,
                file_path=edge.file_path,
                line_number=edge.line_number,
            ))
            continue

        # 2. Short name match
        short = callee.rsplit(".", 1)[-1] if "." in callee else callee
        candidates = by_name.get(short, [])
        if len(candidates) == 1:
            resolved.append(GraphEdge(
                source_id=edge.source_id,
                target_id=candidates[0].id,
                kind=edge.kind,
                file_path=edge.file_path,
                line_number=edge.line_number,
            ))
        elif len(candidates) > 1:
            same_file = [c for c in candidates if c.file_path == edge.file_path]
            best = same_file[0] if same_file else candidates[0]
            resolved.append(GraphEdge(
                source_id=edge.source_id,
                target_id=best.id,
                kind=edge.kind,
                file_path=edge.file_path,
                line_number=edge.line_number,
            ))
        # else: unresolvable -- silently dropped

    return resolved


# ------------------------------------------------------------------
# Indexer class
# ------------------------------------------------------------------

class Indexer:
    """Orchestrates parsing, graph conversion, and Neo4j updates."""

    def __init__(
        self,
        repo_path: str | Path,
        client: Neo4jClient | None = None,
        ignore_patterns: set[str] | None = None,
        max_file_size_kb: int = 512,
    ) -> None:
        self.repo_path = Path(repo_path).resolve()
        self.client = client or Neo4jClient()
        self.ignore_patterns = ignore_patterns or set()
        self.max_file_size_kb = max_file_size_kb

    # ------------------------------------------------------------------
    # Full index
    # ------------------------------------------------------------------

    def full_index(self) -> dict[str, int]:
        """Perform a complete (re-)index of the repository.

        Pipeline:
        1. Walk the filesystem to discover source files.
        2. Parse every file via the parser registry.
        3. Convert ParseResult objects to GraphNode / GraphEdge.
        4. Clear the database.
        5. Upsert all nodes and edges.
        6. Resolve CALLS edges to actual symbol IDs.
        7. Save file hashes for future incremental runs.

        Returns a summary dict with file, node, and edge counts.
        """
        _log(f"[codegraph] Starting full index of {self.repo_path}")

        # 1. Walk
        files = walk(self.repo_path, self.ignore_patterns, self.max_file_size_kb)
        _log(f"[codegraph] Found {len(files)} source files")

        # 2+3. Parse and convert
        all_nodes: list[GraphNode] = []
        all_edges: list[GraphEdge] = []

        for i, fpath in enumerate(files, 1):
            if i % 50 == 0 or i == len(files):
                _log(f"[codegraph] Parsing {i}/{len(files)}: {fpath.name}")
            result = _do_parse(fpath)
            nodes, edges = _parse_result_to_graph(result, str(fpath))
            all_nodes.extend(nodes)
            all_edges.extend(edges)

        # Create a synthetic module-level node per file (anchor for IMPORTS/EXPORTS)
        for fpath in files:
            lang = _language_for_file(str(fpath))
            module_node = GraphNode(
                kind="module",
                name=fpath.stem,
                qualified_name=str(fpath),
                file_path=str(fpath),
                line_start=0,
                line_end=0,
                language=lang,
            )
            all_nodes.append(module_node)

        _log(
            f"[codegraph] Parsed {len(all_nodes)} nodes, "
            f"{len(all_edges)} edges (pre-resolve)"
        )

        # 4. Resolve CALLS edges
        all_edges = _resolve_calls(all_edges, all_nodes)
        _log(f"[codegraph] After call resolution: {len(all_edges)} edges")

        # 5. Clear database and upsert
        _log("[codegraph] Clearing database...")
        self.client.clear_all()
        self.client.ensure_schema()

        _log("[codegraph] Upserting nodes...")
        node_count = self.client.upsert_nodes(all_nodes)

        _log("[codegraph] Upserting edges...")
        edge_count = self.client.upsert_edges(all_edges)

        # 6. Save hashes
        hashes = compute_hashes(files)
        save_hashes(self.repo_path, hashes)

        stats = {"files": len(files), "nodes": node_count, "edges": edge_count}
        _log(f"[codegraph] Full index complete: {stats}")
        return stats

    # ------------------------------------------------------------------
    # Incremental update
    # ------------------------------------------------------------------

    def incremental_update(self) -> dict[str, int]:
        """Incrementally update the index -- only re-process changed files.

        Pipeline:
        1. Walk the filesystem.
        2. Diff hashes against last run.
        3. Delete graph data for changed/deleted files.
        4. Re-parse added + modified files.
        5. Upsert new graph data.
        6. Resolve CALLS edges (uses existing DB nodes for cross-file links).
        7. Save updated hashes.

        Returns a summary dict.
        """
        _log(f"[codegraph] Starting incremental update of {self.repo_path}")

        # 1. Walk
        files = walk(self.repo_path, self.ignore_patterns, self.max_file_size_kb)
        _log(f"[codegraph] Found {len(files)} source files")

        # 2. Diff hashes
        old_hashes = load_hashes(self.repo_path)
        new_hashes = compute_hashes(files)
        changes = diff(old_hashes, new_hashes)

        changed_files = changes.added | changes.modified
        _log(
            f"[codegraph] Changes: {len(changes.added)} added, "
            f"{len(changes.modified)} modified, {len(changes.deleted)} deleted"
        )

        if not changed_files and not changes.deleted:
            _log("[codegraph] No changes detected -- skipping.")
            return {"files": 0, "nodes": 0, "edges": 0}

        # 3. Delete graph data for changed + deleted files
        for fp in changes.deleted | changes.modified:
            self.client.delete_file_nodes(fp)
        _log(
            f"[codegraph] Deleted graph data for "
            f"{len(changes.deleted | changes.modified)} files"
        )

        # 4+5. Re-parse changed files and collect graph data
        all_nodes: list[GraphNode] = []
        all_edges: list[GraphEdge] = []

        for i, fp_str in enumerate(sorted(changed_files), 1):
            fpath = Path(fp_str)
            if not fpath.exists():
                continue
            if i % 50 == 0 or i == len(changed_files):
                _log(f"[codegraph] Parsing {i}/{len(changed_files)}: {fpath.name}")

            result = _do_parse(fpath)
            nodes, edges = _parse_result_to_graph(result, str(fpath))
            all_nodes.extend(nodes)
            all_edges.extend(edges)

            # Synthetic module node
            lang = _language_for_file(str(fpath))
            module_node = GraphNode(
                kind="module",
                name=fpath.stem,
                qualified_name=str(fpath),
                file_path=str(fpath),
                line_start=0,
                line_end=0,
                language=lang,
            )
            all_nodes.append(module_node)

        # 6. Resolve calls -- fetch existing nodes from DB for cross-file resolution
        existing_raw = self.client.query(
            "MATCH (s:Symbol) RETURN s.id AS id, s.kind AS kind, "
            "s.name AS name, s.qualified_name AS qualified_name, "
            "s.file_path AS file_path, s.line_start AS line_start, "
            "s.line_end AS line_end, s.signature AS signature, "
            "s.docstring AS docstring, s.language AS language"
        )
        existing_nodes: list[GraphNode] = []
        for row in existing_raw:
            n = GraphNode(
                kind=row.get("kind") or "",
                name=row.get("name") or "",
                qualified_name=row.get("qualified_name") or "",
                file_path=row.get("file_path") or "",
                line_start=row.get("line_start") or 0,
                line_end=row.get("line_end") or 0,
                signature=row.get("signature") or "",
                docstring=row.get("docstring") or "",
                language=row.get("language") or "",
            )
            existing_nodes.append(n)

        combined_nodes = existing_nodes + all_nodes
        all_edges = _resolve_calls(all_edges, combined_nodes)
        _log(f"[codegraph] Resolved edges: {len(all_edges)}")

        # Upsert new data
        node_count = self.client.upsert_nodes(all_nodes)
        edge_count = self.client.upsert_edges(all_edges)

        # 7. Save hashes
        save_hashes(self.repo_path, new_hashes)

        stats = {
            "files": len(changed_files),
            "nodes": node_count,
            "edges": edge_count,
        }
        _log(f"[codegraph] Incremental update complete: {stats}")
        return stats
