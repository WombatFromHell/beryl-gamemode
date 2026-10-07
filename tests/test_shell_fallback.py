"""Tests for shell_fallback: per-shell negotiation of shell-function resolution."""

from __future__ import annotations

import shlex

import pytest

from gamemode.shell_fallback import shell_fallback


class TestShellFallback:
    def test_fish_plain(self, shell_stub):
        shell_stub("fish")
        env = {"SHELL": "/bin/fish"}
        result = shell_fallback(["yy", "--x"], env)
        assert result is not None
        argv, extra = result
        assert argv == ["/bin/fish", "-ic", "yy --x"]
        assert extra == {}

    def test_bash_with_bashrc(self, tmp_path, monkeypatch):
        rc = tmp_path / ".bashrc"
        rc.write_text("# defs\n")
        monkeypatch.setenv("HOME", str(tmp_path))
        result = shell_fallback(["yy"], {"SHELL": "/bin/bash"})
        assert result is not None
        argv, extra = result
        assert argv == ["/bin/bash", "-c", "yy"]
        assert extra == {"BASH_ENV": str(rc)}

    def test_bash_without_bashrc(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        assert shell_fallback(["yy"], {"SHELL": "/bin/bash"}) is None

    def test_zsh(self, shell_stub):
        shell_stub("zsh")
        result = shell_fallback(["yy"], {"SHELL": "/usr/bin/zsh"})
        assert result is not None
        argv, extra = result
        assert argv == ["/usr/bin/zsh", "-ic", "yy"]
        assert extra == {}

    @pytest.mark.parametrize("shell", ["/bin/sh", "/usr/bin/tcsh"])
    def test_unsupported_shell(self, shell):
        assert shell_fallback(["yy"], {"SHELL": shell}) is None

    def test_unset_shell(self):
        assert shell_fallback(["yy"], {}) is None

    def test_binary_not_on_path(self, tmp_path, monkeypatch):
        empty = tmp_path / "empty"
        empty.mkdir()
        monkeypatch.setenv("PATH", str(empty))
        assert shell_fallback(["yy"], {"SHELL": "/bin/fish"}) is None

    def test_quoting_round_trip(self, shell_stub):
        shell_stub("fish")
        result = shell_fallback(["yy", "a b", "c"], {"SHELL": "/bin/fish"})
        assert result is not None
        argv, _ = result
        assert shlex.split(argv[2]) == ["yy", "a b", "c"]
