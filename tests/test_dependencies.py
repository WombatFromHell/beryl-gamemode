"""Tests for dependency validation module."""

from typing import Any, cast

import pytest

import gamemode
from gamemode.runner import Runner


class TestValidateDeps:
    @pytest.mark.parametrize(
        "enable_scx,enable_vrr,enable_tuned,enable_inhibit",
        [
            (False, False, False, False),
            (True, True, True, True),
        ],
    )
    def test_dep_checks(
        self,
        tmp_path,
        logger,
        enable_scx,
        enable_vrr,
        enable_tuned,
        enable_inhibit,
    ):
        cfg = _cfg(
            runtime_dir=str(tmp_path),
            enable_scx=enable_scx,
            enable_vrr=enable_vrr,
            enable_tuned=enable_tuned,
            enable_inhibit=enable_inhibit,
        )
        r = _FakeRunner(logger)
        r.when_resolved("scxctl", "/usr/bin/scxctl" if enable_scx else None)
        r.when_resolved("jq", "/usr/bin/jq" if enable_vrr else None)
        r.when_resolved("tuned-adm", "/usr/bin/tuned-adm" if enable_tuned else None)
        r.when_resolved(
            "systemd-inhibit", "/usr/bin/systemd-inhibit" if enable_inhibit else None
        )
        r.when_resolved("dbus-send", "/usr/bin/dbus-send" if enable_inhibit else None)
        ok = gamemode.validate_deps(cfg, r, logger)
        if enable_scx or enable_vrr or enable_tuned or enable_inhibit:
            all_present = all(
                [
                    not enable_scx or r.resolve("scxctl") is not None,
                    not enable_vrr or r.resolve("jq") is not None,
                    not enable_tuned or r.resolve("tuned-adm") is not None,
                    not enable_inhibit or r.resolve("systemd-inhibit") is not None,
                    not enable_inhibit or r.resolve("dbus-send") is not None,
                ]
            )
            assert ok is all_present
        else:
            assert ok is True


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


class _FakeRunner(Runner):
    def __init__(self, log):
        super().__init__(log)
        self._resolve_map: dict[str, str | None] = {}

    def when_resolved(self, cmd, path=None):
        self._resolve_map[cmd] = path
        return self

    def resolve(self, cmd):
        return self._resolve_map.get(cmd)
