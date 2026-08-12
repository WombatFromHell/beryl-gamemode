"""Tests for dependency validation module."""

import logging

import pytest
from conftest import FakeRunner, _cfg, _dep_runner

from gamemode.dependencies import validate_deps


class TestValidateDeps:
    @pytest.mark.parametrize(
        "enable_scx,enable_vrr,enable_tuned,enable_inhibit,enable_sleep_inhibit",
        [
            (False, False, False, False, False),
            (True, True, True, True, True),
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
        enable_sleep_inhibit,
    ):
        cfg = _cfg(
            runtime_dir=str(tmp_path),
            enable_scx=enable_scx,
            enable_vrr=enable_vrr,
            enable_tuned=enable_tuned,
            enable_inhibit=enable_inhibit,
            enable_sleep_inhibit=enable_sleep_inhibit,
        )
        r = _dep_runner(
            logger,
            enable_scx,
            enable_vrr,
            enable_tuned,
            enable_inhibit,
            enable_sleep_inhibit,
        )
        ok = validate_deps(cfg, r, logger)
        if enable_scx or enable_vrr or enable_tuned or enable_inhibit or enable_sleep_inhibit:
            all_present = all(
                [
                    not enable_scx or r.resolve("scxctl") is not None,
                    not enable_vrr or r.resolve("jq") is not None,
                    not enable_tuned or r.resolve("tuned-adm") is not None,
                    not enable_sleep_inhibit
                    or r.resolve("systemd-inhibit") is not None,
                    not enable_inhibit or r.resolve("dbus-send") is not None,
                ]
            )
            assert ok is all_present
        else:
            assert ok is True

    @pytest.mark.parametrize(
        "enable_scx,enable_vrr,enable_tuned,enable_inhibit,enable_sleep_inhibit",
        [
            (True, False, False, False, False),
            (False, True, False, False, False),
            (False, False, True, False, False),
            (False, False, False, True, False),
            (False, False, False, False, True),
        ],
    )
    def test_single_feature_enabled_all_deps_present(
        self,
        tmp_path,
        logger,
        enable_scx,
        enable_vrr,
        enable_tuned,
        enable_inhibit,
        enable_sleep_inhibit,
    ):
        """Each feature individually enabled with all deps present returns True."""
        cfg = _cfg(
            runtime_dir=str(tmp_path),
            enable_scx=enable_scx,
            enable_vrr=enable_vrr,
            enable_tuned=enable_tuned,
            enable_inhibit=enable_inhibit,
            enable_sleep_inhibit=enable_sleep_inhibit,
        )
        r = _dep_runner(
            logger,
            enable_scx,
            enable_vrr,
            enable_tuned,
            enable_inhibit,
            enable_sleep_inhibit,
        )
        ok = validate_deps(cfg, r, logger)
        assert ok is True

    def test_single_feature_enabled_missing_dep(self, tmp_path, logger):
        """When a single feature is enabled but its dep is missing, returns False."""
        cfg = _cfg(
            runtime_dir=str(tmp_path),
            enable_scx=True,
        )
        r = FakeRunner(logger)
        r.when_resolved("scxctl", None)
        ok = validate_deps(cfg, r, logger)
        assert ok is False

    def test_missing_dep_logs_error(self, tmp_path, logger, caplog):
        """Missing dependency should log an error with the missing commands."""
        cfg = _cfg(
            runtime_dir=str(tmp_path),
            enable_scx=True,
            enable_vrr=True,
        )
        r = FakeRunner(logger)
        r.when_resolved("scxctl", None)
        r.when_resolved("jq", None)
        caplog.set_level(logging.ERROR)
        ok = validate_deps(cfg, r, logger)
        assert ok is False
        assert "scxctl" in caplog.text
        assert "jq" in caplog.text

    def test_audio_steam_no_dep_checks(self, tmp_path, logger):
        """enable_audio and enable_steam have no dep checks and should not trigger any."""
        cfg = _cfg(
            runtime_dir=str(tmp_path),
            enable_audio=True,
            enable_steam=True,
        )
        r = FakeRunner(logger)
        ok = validate_deps(cfg, r, logger)
        assert ok is True
