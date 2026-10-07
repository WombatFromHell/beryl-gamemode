"""Tests for configuration module."""

import pytest
from conftest import _cfg

import gamemode.config as _cfgmod
from gamemode.config import (
    Config,
    _parse_line,
    _should_skip_line,
    load_config_file,
)


class TestConfig:
    def test_defaults_from_env_mapping(self):
        cfg = Config.from_env(
            {
                "ENABLE_VRR": "false",
                "SCX_SCHEDULER": "custom",
                "SCX_SCHEDULER_MODE": "power-save",
                "XDG_RUNTIME_DIR": "/run/user/999",
            }
        )
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
    def test_bool_parsing(self, val, expected):
        assert Config.from_env({"ENABLE_VRR": val}).enable_vrr is expected

    def test_bool_missing_uses_default(self):
        assert Config.from_env({}).enable_vrr is True
        assert Config.from_env({}).enable_tuned is False


class TestConfigFromEnv:
    def test_parses_mapping_without_os_environ(self):
        env = {
            "ENABLE_VRR": "false",
            "ENABLE_IDLE_MONITOR": "1",
            "IDLE_CMD": "check-idle",
            "ACTIVE_CMD": "check-active",
            "IDLE_TIMEOUT": "60",
            "IDLE_POLL_INTERVAL": "2",
            "SCX_SCHEDULER": "custom",
            "TOGGLE_FEATURES": " VRR , scx ",
            "WRAPPER_FEATURES": "steam,inhibit",
            "VRR_OUTPUTS": "DP-1,DP-2",
            "SYSTEMD_RUN_ARGS": "--user --scope",
            "XDG_RUNTIME_DIR": "/run/user/999",
            "GAMEMODE_DEBUG": "1",
            "NIRI_OUTPUT_NAME": "DP-1",
        }
        cfg = Config.from_env(env)
        assert cfg.enable_vrr is False
        assert cfg.idle_monitor_explicit is True
        assert cfg.enable_idle_monitor is True
        assert cfg.idle_timeout == 60
        assert cfg.idle_poll_interval == 2
        assert cfg.scx_scheduler == "custom"
        assert cfg.toggle_features == {"vrr", "scx"}
        assert cfg.wrapper_features == {"steam", "inhibit"}
        assert cfg.vrr_output_default == "DP-1,DP-2"
        assert cfg.systemd_run_args == ["--user", "--scope"]
        assert cfg.runtime_dir == "/run/user/999"
        assert cfg.debug is True
        assert cfg.niri_output_name == "DP-1"

    def test_built_in_defaults_on_empty_mapping(self):
        cfg = Config.from_env({})
        assert cfg.enable_vrr is True
        assert cfg.enable_idle_monitor is False
        assert cfg.idle_monitor_explicit is False
        assert cfg.idle_timeout == 300
        assert cfg.toggle_features == {
            "vrr",
            "scx",
            "tuned",
            "audio",
            "inhibit",
            "steam",
        }
        assert cfg.debug is False

    def test_idle_monitor_auto_default_requires_pair(self):
        assert (
            Config.from_env({"IDLE_CMD": "a", "ACTIVE_CMD": "b"}).enable_idle_monitor
            is True
        )
        assert Config.from_env({"IDLE_CMD": "a"}).enable_idle_monitor is False

    def test_load_config_file_returns_pairs(self, tmp_path):
        p = tmp_path / "g.conf"
        p.write_text("ENABLE_VRR=false\n# comment\nKEY=a=b\n\n")
        assert load_config_file(p) == {"ENABLE_VRR": "false", "KEY": "a=b"}

    def test_env_wins_over_file_in_from_env(self, monkeypatch):
        monkeypatch.setenv("ENABLE_VRR", "false")
        assert Config.from_env(None).enable_vrr is False

    def test_systemd_run_args_default(self):
        assert "--user" in Config.from_env({"SYSTEMD_RUN_ARGS": ""}).systemd_run_args
        assert "--scope" in Config.from_env({"SYSTEMD_RUN_ARGS": ""}).systemd_run_args

    def test_systemd_run_args_custom(self):
        cfg = Config.from_env({"SYSTEMD_RUN_ARGS": "--custom --args"})
        assert cfg.systemd_run_args == ["--custom", "--args"]

    def test_toggle_features_parsing(self):
        cfg = Config.from_env({"TOGGLE_FEATURES": "VRR,scx"})
        assert "vrr" in cfg.toggle_features
        assert "scx" in cfg.toggle_features

    def test_wrapper_features_parsing(self):
        cfg = Config.from_env({"WRAPPER_FEATURES": "steam,inhibit"})
        assert "steam" in cfg.wrapper_features
        assert "inhibit" in cfg.wrapper_features

    def test_enable_systemd_run_env(self):
        assert Config.from_env({"ENABLE_SYSTEMD_RUN": "false"}).enable_systemd_run is (
            False
        )

    def test_enable_systemd_run_default(self):
        assert Config.from_env({}).enable_systemd_run is True


class TestSteamScript:
    def test_defaults_to_path_lookup(self, monkeypatch):
        monkeypatch.setattr(
            _cfgmod.shutil, "which", lambda name: "/usr/bin/steam-env-base.sh"
        )
        cfg = Config.from_env({})
        assert cfg.steam_script == "/usr/bin/steam-env-base.sh"

    def test_path_lookup_miss_is_empty(self, monkeypatch):
        monkeypatch.setattr(_cfgmod.shutil, "which", lambda name: None)
        assert Config.from_env({}).steam_script == ""

    def test_explicit_value_wins(self, monkeypatch):
        monkeypatch.setattr(_cfgmod.shutil, "which", lambda name: "")
        cfg = Config.from_env({"STEAM_ENV_SCRIPT": "/opt/custom.sh"})
        assert cfg.steam_script == "/opt/custom.sh"

    def test_empty_value_disables(self, monkeypatch):
        monkeypatch.setattr(
            _cfgmod.shutil, "which", lambda name: "/usr/bin/steam-env-base.sh"
        )
        cfg = Config.from_env({"STEAM_ENV_SCRIPT": ""})
        assert cfg.steam_script == ""

    def test_direct_construction_default_is_empty(self):
        assert Config().steam_script == ""


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
