"""MCP server entry point for codegraph."""

from __future__ import annotations

import asyncio
import logging
import sys

from mcp.server import Server
from mcp.server.stdio import stdio_server

from codegraph.config import get_settings
from codegraph.graph.client import Neo4jClient
from codegraph.mcp_server.tools import register_tools

log = logging.getLogger(__name__)


def _make_client(settings) -> Neo4jClient:
    """Build and connect a Neo4jClient from settings."""
    client = Neo4jClient(
        uri=settings.neo4j_uri,
        user=settings.neo4j_user,
        password=settings.neo4j_password,
        database=settings.neo4j_database,
    )
    client.connect()
    return client


async def run_server() -> None:
    """Start the codegraph MCP server on stdio transport."""
    settings = get_settings()

    server = Server("codegraph")

    try:
        client = _make_client(settings)
    except Exception as exc:
        log.error("Failed to connect to Neo4j at %s: %s", settings.neo4j_uri, exc)
        print(
            f"Error: Cannot connect to Neo4j at {settings.neo4j_uri}. "
            f"Ensure Neo4j is running and credentials are correct.\n"
            f"Details: {exc}",
            file=sys.stderr,
        )
        sys.exit(1)

    register_tools(server, client)

    log.info("codegraph MCP server starting on stdio")
    try:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options(),
            )
    finally:
        client.close()


def main() -> None:
    """Entry point callable from CLI."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stderr,
    )
    asyncio.run(run_server())


if __name__ == "__main__":
    main()
