"""Tests for the codegraph CLI."""

import json

from click.testing import CliRunner

from codegraph import __version__
from codegraph import cli as cli_module
from codegraph.cli import main


class FakeClient:
    def __init__(self, stats=None, rows=None):
        self._stats = stats or {}
        self._rows = rows if rows is not None else []
        self.cleared = False

    def get_stats(self):
        return self._stats

    def query(self, *_args, **_kwargs):
        return self._rows

    def clear_all(self):
        self.cleared = True

    def close(self):
        pass


def test_version_flag_prints_version():
    result = CliRunner().invoke(main, ["--version"])
    assert result.exit_code == 0
    assert result.output.strip() == f"codegraph, version {__version__}"


def test_version_flag_exits_before_subcommand_resolution():
    # --version is eager: it must short-circuit even with a subcommand present
    result = CliRunner().invoke(main, ["--version", "status"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_status_json_flag(monkeypatch):
    fake = FakeClient(stats={"nodes": 42, "edges": 7})
    monkeypatch.setattr(cli_module, "_make_client", lambda *a: fake)

    result = CliRunner().invoke(main, ["status", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.output) == {"nodes": 42, "edges": 7}


def test_status_human_output_unchanged(monkeypatch):
    fake = FakeClient(stats={"nodes": 42, "edges": 7})
    monkeypatch.setattr(cli_module, "_make_client", lambda *a: fake)

    result = CliRunner().invoke(main, ["status"])
    assert result.exit_code == 0
    assert "Graph statistics:" in result.output
    assert "Nodes:   42" in result.output


def test_query_json_found(monkeypatch):
    row = {
        "s": {
            "name": "foo",
            "kind": "function",
            "file_path": "src/foo.py",
            "line_start": 10,
        }
    }
    fake = FakeClient(rows=[row])
    monkeypatch.setattr(cli_module, "_make_client", lambda *a: fake)

    result = CliRunner().invoke(main, ["query", "foo", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["found"] is True
    assert data["name"] == "foo"
    assert data["file_path"] == "src/foo.py"


def test_query_json_not_found(monkeypatch):
    fake = FakeClient(rows=[])
    monkeypatch.setattr(cli_module, "_make_client", lambda *a: fake)

    result = CliRunner().invoke(main, ["query", "missing", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.output) == {"found": False, "name": "missing"}


def test_clear_json_flag(monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(cli_module, "_make_client", lambda *a: fake)

    result = CliRunner().invoke(main, ["clear", "--json", "--yes"])
    assert result.exit_code == 0
    assert json.loads(result.output) == {"cleared": True}
    assert fake.cleared


def test_json_flag_in_help():
    for cmd in ("init", "update", "status", "query", "clear"):
        result = CliRunner().invoke(main, [cmd, "--help"])
        assert result.exit_code == 0
        assert "--json" in result.output, cmd
