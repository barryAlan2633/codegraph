"""Neo4j driver wrapper for the codebase knowledge graph."""

from __future__ import annotations

from typing import Any

from neo4j import GraphDatabase
from neo4j.exceptions import ServiceUnavailable, AuthError, ClientError

from codegraph.graph.models import GraphNode, GraphEdge
from codegraph.graph.schema import ALL_SCHEMA_STATEMENTS


class Neo4jClient:
    """Thin wrapper around the neo4j Python driver.

    Provides batch upsert, schema management, and convenience queries.
    """

    def __init__(
        self,
        uri: str = "bolt://localhost:7687",
        user: str = "neo4j",
        password: str = "neo4j",
        database: str = "neo4j",
    ) -> None:
        self._uri = uri
        self._user = user
        self._password = password
        self._database = database
        self._driver = None

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    def connect(self) -> None:
        """Open a driver connection to Neo4j."""
        try:
            self._driver = GraphDatabase.driver(
                self._uri, auth=(self._user, self._password)
            )
            # Verify connectivity immediately
            self._driver.verify_connectivity()
        except (ServiceUnavailable, AuthError) as exc:
            self._driver = None
            raise ConnectionError(
                f"Failed to connect to Neo4j at {self._uri}: {exc}"
            ) from exc

    def close(self) -> None:
        """Close the driver connection."""
        if self._driver is not None:
            self._driver.close()
            self._driver = None

    @property
    def driver(self):
        if self._driver is None:
            raise ConnectionError("Not connected. Call connect() first.")
        return self._driver

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def ensure_schema(self) -> None:
        """Create constraints and indexes if they do not already exist."""
        with self.driver.session(database=self._database) as session:
            for stmt in ALL_SCHEMA_STATEMENTS:
                try:
                    session.run(stmt)
                except ClientError:
                    # Constraint/index may already exist in an incompatible
                    # form on older Neo4j versions — skip gracefully.
                    pass

    # ------------------------------------------------------------------
    # Batch upserts
    # ------------------------------------------------------------------

    def upsert_nodes(self, nodes: list[GraphNode], batch_size: int = 500) -> int:
        """Upsert symbol nodes using UNWIND batching. Returns count upserted."""
        cypher = """
        UNWIND $rows AS row
        MERGE (s:Symbol {id: row.id})
        SET s.kind           = row.kind,
            s.name           = row.name,
            s.qualified_name = row.qualified_name,
            s.signature      = row.signature,
            s.docstring      = row.docstring,
            s.file_path      = row.file_path,
            s.line_start     = row.line_start,
            s.line_end       = row.line_end,
            s.language        = row.language
        """
        total = 0
        with self.driver.session(database=self._database) as session:
            for i in range(0, len(nodes), batch_size):
                batch = [n.to_dict() for n in nodes[i : i + batch_size]]
                session.run(cypher, rows=batch)
                total += len(batch)
        return total

    def upsert_edges(self, edges: list[GraphEdge], batch_size: int = 500) -> int:
        """Upsert relationship edges using UNWIND batching. Returns count upserted."""
        cypher = """
        UNWIND $rows AS row
        MATCH (src:Symbol {id: row.source_id})
        MATCH (tgt:Symbol {id: row.target_id})
        MERGE (src)-[r:RELATES_TO {kind: row.kind}]->(tgt)
        SET r.file_path   = row.file_path,
            r.line_number = row.line_number
        """
        total = 0
        with self.driver.session(database=self._database) as session:
            for i in range(0, len(edges), batch_size):
                batch = [e.to_dict() for e in edges[i : i + batch_size]]
                session.run(cypher, rows=batch)
                total += len(batch)
        return total

    # ------------------------------------------------------------------
    # File-level operations
    # ------------------------------------------------------------------

    def delete_file_nodes(self, file_path: str) -> int:
        """Remove all nodes and their edges for a given file. Returns deleted count."""
        cypher = """
        MATCH (s:Symbol {file_path: $file_path})
        DETACH DELETE s
        RETURN count(s) AS deleted
        """
        with self.driver.session(database=self._database) as session:
            result = session.run(cypher, file_path=file_path)
            record = result.single()
            return record["deleted"] if record else 0

    # ------------------------------------------------------------------
    # Raw query
    # ------------------------------------------------------------------

    def query(self, cypher: str, params: dict[str, Any] | None = None) -> list[dict]:
        """Execute an arbitrary Cypher query and return results as dicts."""
        params = params or {}
        with self.driver.session(database=self._database) as session:
            result = session.run(cypher, **params)
            return [record.data() for record in result]

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    def clear_all(self) -> None:
        """Delete every node and relationship in the database."""
        # Use CALL in batches to avoid OOM on large graphs
        cypher = """
        MATCH (n)
        CALL (n) { DETACH DELETE n }
        IN TRANSACTIONS OF 5000 ROWS
        """
        try:
            with self.driver.session(database=self._database) as session:
                session.run(cypher)
        except ClientError:
            # Fallback for Neo4j < 5.x that doesn't support CALL IN TRANSACTIONS
            with self.driver.session(database=self._database) as session:
                session.run("MATCH (n) DETACH DELETE n")

    def get_stats(self) -> dict[str, int]:
        """Return node and edge counts."""
        node_cypher = "MATCH (n:Symbol) RETURN count(n) AS node_count"
        edge_cypher = "MATCH ()-[r]->() RETURN count(r) AS edge_count"
        with self.driver.session(database=self._database) as session:
            node_result = session.run(node_cypher).single()
            edge_result = session.run(edge_cypher).single()
            return {
                "nodes": node_result["node_count"] if node_result else 0,
                "edges": edge_result["edge_count"] if edge_result else 0,
            }
