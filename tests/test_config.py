"""Tests for configuration module."""

from typing import Any, cast

import pytest

import gamemode


class TestConfig:
    def test_defaults_from_env(self, monkeypatch):
        monkeypatch.setenv("ENABLE_VRR", "false")
        monkeypatch.setenv("SCX_SCHEDULER", "custom")
        monkeypatch.setenv("SCX_SCHEDULER_MODE", "power-save")
        monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/999")
        cfg = gamemode.Config()
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
        assert gamemode._env_bool("TEST_BOOL", False) == expected

    def test_env_bool_missing_default(self):
        assert gamemode._env_bool("NONEXISTENT_XYZ", True) is True
        assert gamemode._env_bool("NONEXISTENT_XYZ", False) is False


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
    return gamemode.Config(**cast(dict[str, Any], defaults))
