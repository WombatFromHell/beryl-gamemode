"""Tests for CLI parser module."""

import pytest

from gamemode.cli import cli_parse


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
