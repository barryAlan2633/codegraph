"""Tests for the codegraph CLI."""

from click.testing import CliRunner

from codegraph import __version__
from codegraph.cli import main


def test_version_flag_prints_version():
    result = CliRunner().invoke(main, ["--version"])
    assert result.exit_code == 0
    assert result.output.strip() == f"codegraph, version {__version__}"


def test_version_flag_exits_before_subcommand_resolution():
    # --version is eager: it must short-circuit even with a subcommand present
    result = CliRunner().invoke(main, ["--version", "status"])
    assert result.exit_code == 0
    assert __version__ in result.output
