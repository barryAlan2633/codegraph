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


def _calls_row():
    return {
        "s": {
            "name": "foo",
            "kind": "function",
            "incoming": [{"source": f"caller{i}"} for i in range(5)],
            "outgoing": [{"target": f"callee{i}"} for i in range(3)],
        }
    }


def test_query_limit_caps_text_lists(monkeypatch):
    fake = FakeClient(rows=[_calls_row()])
    monkeypatch.setattr(cli_module, "_make_client", lambda *a: fake)

    result = CliRunner().invoke(main, ["query", "foo", "--limit", "2"])
    assert result.exit_code == 0
    assert "Called by: caller0, caller1 (+3 more)" in result.output
    assert "Calls: callee0, callee1 (+1 more)" in result.output


def test_query_limit_larger_than_lists_shows_all(monkeypatch):
    fake = FakeClient(rows=[_calls_row()])
    monkeypatch.setattr(cli_module, "_make_client", lambda *a: fake)

    result = CliRunner().invoke(main, ["query", "foo", "--limit", "10"])
    assert result.exit_code == 0
    assert "Called by: caller0, caller1, caller2, caller3, caller4" in result.output
    assert "more)" not in result.output


def test_query_limit_zero_hides_call_lists(monkeypatch):
    fake = FakeClient(rows=[_calls_row()])
    monkeypatch.setattr(cli_module, "_make_client", lambda *a: fake)

    result = CliRunner().invoke(main, ["query", "foo", "--limit", "0"])
    assert result.exit_code == 0
    assert "Called by" not in result.output
    assert "Calls" not in result.output


def test_query_limit_caps_json_lists(monkeypatch):
    fake = FakeClient(rows=[_calls_row()])
    monkeypatch.setattr(cli_module, "_make_client", lambda *a: fake)

    result = CliRunner().invoke(main, ["query", "foo", "--json", "--limit", "2"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["incoming"] == [{"source": "caller0"}, {"source": "caller1"}]
    assert data["outgoing"] == [{"target": "callee0"}, {"target": "callee1"}]


def test_query_json_without_limit_keeps_full_lists(monkeypatch):
    fake = FakeClient(rows=[_calls_row()])
    monkeypatch.setattr(cli_module, "_make_client", lambda *a: fake)

    result = CliRunner().invoke(main, ["query", "foo", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert len(data["incoming"]) == 5
    assert len(data["outgoing"]) == 3


def test_query_limit_rejects_negative():
    result = CliRunner().invoke(main, ["query", "foo", "--limit", "-1"])
    assert result.exit_code != 0


def test_query_limit_in_help():
    result = CliRunner().invoke(main, ["query", "--help"])
    assert result.exit_code == 0
    assert "--limit" in result.output


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


def test_quiet_flag_in_help():
    for cmd in ("init", "update"):
        result = CliRunner().invoke(main, [cmd, "--help"])
        assert result.exit_code == 0
        assert "--quiet" in result.output, cmd


class FakeIndexer:
    captured_quiet = None

    def __init__(self, *, quiet=False, **_kwargs):
        FakeIndexer.captured_quiet = quiet

    def full_index(self):
        return {"files": 1, "nodes": 2, "edges": 3}

    def incremental_update(self):
        return {"files": 1, "nodes": 2, "edges": 3}


def _patch_indexing(monkeypatch):
    import codegraph.indexer as indexer_pkg

    monkeypatch.setattr(cli_module, "_make_client", lambda *a: FakeClient())
    monkeypatch.setattr(indexer_pkg, "Indexer", FakeIndexer)
    FakeIndexer.captured_quiet = None


def test_init_quiet_passed_to_indexer(monkeypatch):
    _patch_indexing(monkeypatch)
    result = CliRunner().invoke(main, ["init", ".", "--quiet"])
    assert result.exit_code == 0
    assert FakeIndexer.captured_quiet is True
    assert "Done. Files: 1, Nodes: 2, Edges: 3" in result.output


def test_init_default_not_quiet(monkeypatch):
    _patch_indexing(monkeypatch)
    result = CliRunner().invoke(main, ["init", "."])
    assert result.exit_code == 0
    assert FakeIndexer.captured_quiet is False


def test_update_quiet_short_flag(monkeypatch):
    _patch_indexing(monkeypatch)
    result = CliRunner().invoke(main, ["update", ".", "-q"])
    assert result.exit_code == 0
    assert FakeIndexer.captured_quiet is True


def test_init_quiet_with_json_outputs_only_json(monkeypatch):
    _patch_indexing(monkeypatch)
    result = CliRunner().invoke(main, ["init", ".", "--quiet", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.output) == {"files": 1, "nodes": 2, "edges": 3}


def test_indexer_quiet_suppresses_progress(capsys):
    from codegraph.indexer.indexer import Indexer

    quiet_indexer = Indexer(repo_path=".", client=FakeClient(), quiet=True)
    quiet_indexer._log("progress line")
    assert capsys.readouterr().err == ""

    loud_indexer = Indexer(repo_path=".", client=FakeClient())
    loud_indexer._log("progress line")
    assert "progress line" in capsys.readouterr().err


class FailingStatsClient(FakeClient):
    def get_stats(self):
        raise RuntimeError("boom")


def test_export_writes_stats_file(monkeypatch, tmp_path):
    fake = FakeClient(stats={"nodes": 42, "edges": 7})
    monkeypatch.setattr(cli_module, "_make_client", lambda *a: fake)
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(main, ["export"])
    assert result.exit_code == 0
    assert "Exported graph stats to codegraph_export.json" in result.output
    data = json.loads((tmp_path / "codegraph_export.json").read_text())
    assert data == {"nodes": 42, "edges": 7}


def test_export_failure_exits_nonzero_and_writes_no_file(monkeypatch, tmp_path):
    fake = FailingStatsClient()
    monkeypatch.setattr(cli_module, "_make_client", lambda *a: fake)
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(main, ["export"])
    assert result.exit_code != 0
    assert not (tmp_path / "codegraph_export.json").exists()
