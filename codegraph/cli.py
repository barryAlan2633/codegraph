"""Click-based CLI for codegraph."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from codegraph import __version__
from codegraph.config import get_settings


def _make_client(neo4j_uri: str | None, neo4j_user: str | None, neo4j_password: str | None):
    """Build and connect a Neo4jClient from CLI options or environment."""
    from codegraph.graph.client import Neo4jClient

    settings = get_settings()
    client = Neo4jClient(
        uri=neo4j_uri or settings.neo4j_uri,
        user=neo4j_user or settings.neo4j_user,
        password=neo4j_password or settings.neo4j_password,
        database=settings.neo4j_database,
    )
    try:
        client.connect()
    except Exception as exc:
        click.echo(f"Error connecting to Neo4j: {exc}", err=True)
        sys.exit(1)
    return client


_neo4j_options = [
    click.option("--neo4j-uri", default=None, help="Neo4j Bolt URI."),
    click.option("--neo4j-user", default=None, help="Neo4j username."),
    click.option("--neo4j-password", default=None, help="Neo4j password."),
]


def neo4j_options(func):
    for opt in reversed(_neo4j_options):
        func = opt(func)
    return func


json_option = click.option(
    "--json", "as_json", is_flag=True, default=False, help="Print machine-readable JSON output."
)


@click.group()
@click.version_option(version=__version__, prog_name="codegraph")
def main():
    """codegraph -- codebase knowledge graph tool."""


@main.command()
@click.argument("repo_path", default=".", type=click.Path(exists=True))
@neo4j_options
@json_option
def init(
    repo_path: str,
    neo4j_uri: str | None,
    neo4j_user: str | None,
    neo4j_password: str | None,
    as_json: bool,
):
    """Full index of a codebase into the knowledge graph."""
    from codegraph.indexer import Indexer

    settings = get_settings(repo_path=Path(repo_path).resolve())
    client = _make_client(neo4j_uri, neo4j_user, neo4j_password)

    try:
        indexer = Indexer(
            repo_path=settings.repo_path,
            client=client,
            ignore_patterns=set(settings.ignore_patterns),
            max_file_size_kb=settings.max_file_size_kb,
        )
        summary = indexer.full_index()
        if as_json:
            click.echo(json.dumps(summary))
        else:
            click.echo(
                f"Done. Files: {summary['files']}, "
                f"Nodes: {summary['nodes']}, "
                f"Edges: {summary['edges']}"
            )
    finally:
        client.close()


@main.command()
@click.argument("repo_path", default=".", type=click.Path(exists=True))
@neo4j_options
@json_option
def update(
    repo_path: str,
    neo4j_uri: str | None,
    neo4j_user: str | None,
    neo4j_password: str | None,
    as_json: bool,
):
    """Incremental update of changed files."""
    from codegraph.indexer import Indexer

    settings = get_settings(repo_path=Path(repo_path).resolve())
    client = _make_client(neo4j_uri, neo4j_user, neo4j_password)

    try:
        indexer = Indexer(
            repo_path=settings.repo_path,
            client=client,
            ignore_patterns=set(settings.ignore_patterns),
            max_file_size_kb=settings.max_file_size_kb,
        )
        summary = indexer.incremental_update()
        if as_json:
            click.echo(json.dumps(summary))
        else:
            click.echo(
                f"Done. Files: {summary['files']}, "
                f"Nodes: {summary['nodes']}, "
                f"Edges: {summary['edges']}"
            )
    finally:
        client.close()


@main.command()
@neo4j_options
def serve(neo4j_uri: str | None, neo4j_user: str | None, neo4j_password: str | None):
    """Start the MCP server (stdio transport)."""
    import os

    # Pass CLI options as environment variables so the server picks them up
    if neo4j_uri:
        os.environ["CODEGRAPH_NEO4J_URI"] = neo4j_uri
    if neo4j_user:
        os.environ["CODEGRAPH_NEO4J_USER"] = neo4j_user
    if neo4j_password:
        os.environ["CODEGRAPH_NEO4J_PASSWORD"] = neo4j_password

    from codegraph.mcp_server.server import main as server_main
    server_main()


@main.command()
@neo4j_options
@json_option
def status(
    neo4j_uri: str | None, neo4j_user: str | None, neo4j_password: str | None, as_json: bool
):
    """Show graph statistics."""
    client = _make_client(neo4j_uri, neo4j_user, neo4j_password)

    try:
        stats = client.get_stats()
        if as_json:
            click.echo(json.dumps(stats))
        else:
            click.echo("Graph statistics:")
            click.echo(f"  Nodes:   {stats['nodes']}")
            click.echo(f"  Edges:   {stats['edges']}")
    finally:
        client.close()


@main.command()
@neo4j_options
@json_option
@click.option("--yes", is_flag=True, default=False, help="Skip the confirmation prompt.")
def clear(
    neo4j_uri: str | None,
    neo4j_user: str | None,
    neo4j_password: str | None,
    as_json: bool,
    yes: bool,
):
    """Wipe the entire graph database."""
    # Prompt on stderr so stdout stays machine-readable in --json mode.
    if not yes and not click.confirm(
        "This will delete all data in the graph. Continue?", err=True
    ):
        raise click.Abort()

    client = _make_client(neo4j_uri, neo4j_user, neo4j_password)

    try:
        client.clear_all()
        if as_json:
            click.echo(json.dumps({"cleared": True}))
        else:
            click.echo("Database cleared.")
    finally:
        client.close()


@main.command()
@click.argument("name")
@neo4j_options
@json_option
def query(
    name: str,
    neo4j_uri: str | None,
    neo4j_user: str | None,
    neo4j_password: str | None,
    as_json: bool,
):
    """Quick symbol lookup from the CLI."""
    from codegraph.graph import queries as q

    client = _make_client(neo4j_uri, neo4j_user, neo4j_password)

    try:
        rows = client.query(q.GET_SYMBOL, {"name": name})
        if not rows:
            if as_json:
                click.echo(json.dumps({"found": False, "name": name}))
            else:
                click.echo(f"Symbol '{name}' not found.")
            return

        row = rows[0]
        # GET_SYMBOL returns s{.*} which the driver nests under key "s"
        s = row.get("s", row)
        if isinstance(s, dict) and "name" in s:
            r = s
        else:
            r = row

        if as_json:
            click.echo(json.dumps({"found": True, **r}, default=str))
            return

        kind = str(r.get("kind", "symbol")).capitalize()
        click.echo(f"{kind}: {r.get('name', name)}")

        if r.get("file_path"):
            loc = r["file_path"]
            if r.get("line_start"):
                loc += f":{r['line_start']}"
                if r.get("line_end") and r["line_end"] != r["line_start"]:
                    loc += f"-{r['line_end']}"
            click.echo(f"File: {loc}")

        if r.get("qualified_name"):
            click.echo(f"Qualified: {r['qualified_name']}")

        if r.get("signature"):
            click.echo(f"Signature: {r['signature']}")

        if r.get("docstring"):
            click.echo(f"Docstring: {r['docstring']}")

        incoming = [i for i in (r.get("incoming") or []) if i.get("source")]
        if incoming:
            strs = [i["source"] for i in incoming]
            click.echo(f"Called by: {', '.join(strs)}")

        outgoing = [o for o in (r.get("outgoing") or []) if o.get("target")]
        if outgoing:
            click.echo(f"Calls: {', '.join(o['target'] for o in outgoing)}")
    finally:
        client.close()
