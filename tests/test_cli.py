"""Tests for CLI parser module."""

import pytest

from gamemode.cli import cli_parse, main


class TestCliParser:
    @pytest.mark.parametrize(
        "argv,expected_mode,expected_cmd",
        [
            (["on"], "on", []),
            (["off"], "off", []),
            (["--", "steam"], "wrapper", ["steam"]),
            (["--", "~/Games/foo/run.sh"], "wrapper", ["~/Games/foo/run.sh"]),
            (["mygame"], "wrapper", ["mygame"]),
            (["mygame", "--flag"], "wrapper", ["mygame", "--flag"]),
            ([], None, []),
            (["--help"], None, []),
            (["-h"], None, []),
        ],
    )
    def test_cli_parse(self, argv, expected_mode, expected_cmd, capsys):
        mode, cmd = cli_parse(argv)
        assert mode == expected_mode
        assert cmd == expected_cmd

    def test_wrapper_empty_returns_none(self, capsys):
        mode, cmd = cli_parse(["--"])
        assert mode is None
        assert cmd == []


class TestMain:
    def test_main_version(self, disabled_features_env):
        """main with -V/--version should print version and return 0."""
        ret = main(["-V"])
        assert ret == 0

    def test_main_version_long(self, disabled_features_env):
        """main with --version should print version and return 0."""
        ret = main(["--version"])
        assert ret == 0

    def test_main_unknown_subcommand(self, disabled_features_env, capsys):
        """main with unknown subcommand should return 1."""
        ret = main(["unknown"])
        assert ret == 1

    def test_main_empty_argv_returns_usage(self, disabled_features_env, capsys):
        """main with empty argv should print usage and return 0."""
        ret = main([])
        assert ret == 0
        output = capsys.readouterr().out
        assert "Usage:" in output
