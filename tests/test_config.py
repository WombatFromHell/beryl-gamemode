"""Tests for configuration module."""

import os
from typing import Any, cast

import pytest

from gamemode.config import (
    Config,
    _env_bool,
    _parse_line,
    _should_skip_line,
    load_config_file,
)


class TestConfig:
    def test_defaults_from_env(self, monkeypatch):
        monkeypatch.setenv("ENABLE_VRR", "false")
        monkeypatch.setenv("SCX_SCHEDULER", "custom")
        monkeypatch.setenv("SCX_SCHEDULER_MODE", "power-save")
        monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/999")
        cfg = Config()
        assert cfg.enable_vrr is False
        assert cfg.scx_scheduler == "custom"
        assert cfg.scx_mode == "power-save"
        assert cfg.runtime_dir == "/run/user/999"

    def test_state_dir_derived(self, tmp_path):
        cfg = _cfg(runtime_dir=str(tmp_path))
        assert cfg.state_dir == tmp_path / "gamemode"
        assert cfg.lock_file == tmp_path / "gamemode" / "lock"

    @pytest.mark.parametrize(
        "val,expected",
        [
            ("true", True),
            ("True", True),
            ("1", True),
            ("yes", True),
            ("false", False),
            ("0", False),
            ("no", False),
            ("", False),
        ],
    )
    def test_env_bool_parsing(self, monkeypatch, val, expected):
        monkeypatch.setenv("TEST_BOOL", val)
        assert _env_bool("TEST_BOOL", False) == expected

    def test_env_bool_missing_default(self):
        assert _env_bool("NONEXISTENT_XYZ", True) is True
        assert _env_bool("NONEXISTENT_XYZ", False) is False


class TestShouldSkipLine:
    @pytest.mark.parametrize(
        "line,expected",
        [
            ("", True),
            ("   ", True),
            ("# comment", True),
            ("  # indented comment", True),
            ("NO_EQUALS_SIGN", True),
            ("KEY=value", False),
            ("KEY=val=ue", False),
            ("KEY=", False),
        ],
    )
    def test_skip_lines(self, line, expected):
        assert _should_skip_line(line) == expected


class TestParseLine:
    @pytest.mark.parametrize(
        "line,expected",
        [
            ("KEY=value", ("KEY", "value")),
            ("  KEY  =  value  ", ("KEY", "value")),
            ('KEY="quoted"', ("KEY", "quoted")),
            ("KEY='single'", ("KEY", "single")),
            ('KEY="unterminated', ("KEY", '"unterminated')),
            ("KEY=x", ("KEY", "x")),
            ("K=v", ("K", "v")),
        ],
    )
    def test_parse_valid(self, line, expected):
        assert _parse_line(line) == expected

    def test_parse_no_equals(self):
        """Lines without = are never passed to _parse_line by load_config_file."""
        result = _parse_line("NO_EQUALS")
        assert result == ("NO_EQUALS", "")


class TestLoadConfigFile:
    def test_nonexistent_file(self, tmp_path):
        config_path = tmp_path / "nonexistent.conf"
        load_config_file(config_path)

    def test_empty_file(self, tmp_path):
        config_path = tmp_path / "empty.conf"
        config_path.write_text("")
        load_config_file(config_path)

    def test_comments_and_blanks(self, tmp_path):
        config_path = tmp_path / "comments.conf"
        config_path.write_text("# comment\n\n  \n# another\n")
        load_config_file(config_path)

    def test_basic_key_value(self, tmp_path, monkeypatch):
        monkeypatch.delenv("TEST_KEY_A", raising=False)
        monkeypatch.delenv("TEST_KEY_B", raising=False)
        config_path = tmp_path / "basic.conf"
        config_path.write_text("TEST_KEY_A=value_a\nTEST_KEY_B=value_b\n")
        load_config_file(config_path)
        assert os.environ["TEST_KEY_A"] == "value_a"
        assert os.environ["TEST_KEY_B"] == "value_b"

    def test_quoted_values(self, tmp_path, monkeypatch):
        monkeypatch.delenv("TEST_QUOTED", raising=False)
        config_path = tmp_path / "quoted.conf"
        config_path.write_text("TEST_QUOTED=\"double quoted\"\nTEST_SINGLE='single'\n")
        load_config_file(config_path)
        assert os.environ["TEST_QUOTED"] == "double quoted"
        assert os.environ["TEST_SINGLE"] == "single"

    def test_env_precedence(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TEST_PRECEDENCE", "env_value")
        config_path = tmp_path / "precedence.conf"
        config_path.write_text("TEST_PRECEDENCE=file_value\n")
        load_config_file(config_path)
        assert os.environ["TEST_PRECEDENCE"] == "env_value"

    def test_value_with_equals(self, tmp_path, monkeypatch):
        monkeypatch.delenv("TEST_EQUALS", raising=False)
        config_path = tmp_path / "equals.conf"
        config_path.write_text("TEST_EQUALS=a=b=c\n")
        load_config_file(config_path)
        assert os.environ["TEST_EQUALS"] == "a=b=c"

    def test_spaces_around_equals(self, tmp_path, monkeypatch):
        monkeypatch.delenv("TEST_SPACES", raising=False)
        config_path = tmp_path / "spaces.conf"
        config_path.write_text("  TEST_SPACES  =  spaced_value  \n")
        load_config_file(config_path)
        assert os.environ["TEST_SPACES"] == "spaced_value"


def _cfg(**overrides):
    defaults = dict(
        enable_scx=False,
        enable_vrr=False,
        enable_tuned=False,
        enable_inhibit=False,
        enable_audio=False,
        enable_steam=False,
        scx_scheduler="lavd",
        scx_mode="gaming",
        profile_game="throughput-performance-bazzite",
        profile_desktop="balanced-bazzite",
        audio_latency="60",
        steam_script="",
        vrr_output_default="DP-1",
        runtime_dir="/tmp",
    )
    defaults.update(overrides)
    return Config(**cast(dict[str, Any], defaults))
