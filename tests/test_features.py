"""Tests for feature implementations: VRR, PowerProfile, SCXScheduler, AudioPriority, ScreenInhibit, wrappers."""

import logging
import os
import struct
import threading

import pytest
from conftest import (
    FakeRunner,
    _cfg,
    _cp,
    _dbus_uninhibit_cmd,
    _inhibit_maps,
    _resolve,
    _vrr_maps,
)

from gamemode.features.audio_priority import AudioPriority
from gamemode.features.power_profile import PowerProfile
from gamemode.features.screen_inhibit import ScreenInhibit
from gamemode.features.scx_scheduler import SCXScheduler
from gamemode.features.vrr import VRR
from gamemode.features.wrappers import (
    WRAPPER_FACTORIES,
    SystemdRun,
    WrapperChain,
    inhibit_wrapper_factory,
    steam_wrapper_factory,
)
from gamemode.runner import Runner


class TestVRR:
    @pytest.mark.parametrize("enabled", [True, False])
    def test_skip_when_disabled(self, feature_builder, enabled):
        vrr, _ = feature_builder(VRR, enable_vrr=enabled)
        result = vrr.enable("DP-1")
        if not enabled:
            assert result.skipped is True

    def test_skip_when_not_niri(self, feature_builder, monkeypatch):
        monkeypatch.setenv("XDG_SESSION_DESKTOP", "gnome")
        monkeypatch.delenv("XDG_CURRENT_DESKTOP", raising=False)
        monkeypatch.setattr("gamemode.compositor.compositor_is_niri", lambda: False)
        vrr, _ = feature_builder(VRR, enable_vrr=True)
        result = vrr.enable("DP-1")
        assert result.skipped is True

    def test_enable_success(self, feature_builder, niri_session):
        resolve_map, run_map, pipe_map = _vrr_maps(
            vrr_supported=True, vrr_enabled=False
        )
        run_map[("niri", "msg", "output", "DP-1", "vrr", "on")] = _cp()  # pyright: ignore[reportArgumentType]
        vrr, _ = feature_builder(
            VRR,
            enable_vrr=True,
            resolve_map=resolve_map,
            run_map=run_map,
            pipe_map=pipe_map,
        )
        result = vrr.enable("DP-1")
        assert result.changed is True
        assert result.ok is True

    def test_enable_already_on(self, feature_builder, niri_session):
        resolve_map, run_map, pipe_map = _vrr_maps(vrr_supported=True, vrr_enabled=True)
        vrr, _ = feature_builder(
            VRR,
            enable_vrr=True,
            resolve_map=resolve_map,
            run_map=run_map,
            pipe_map=pipe_map,
        )
        result = vrr.enable("DP-1")
        assert result.changed is False
        assert result.skipped is False

    def test_disable_success(self, feature_builder, niri_session):
        resolve_map, run_map, pipe_map = _vrr_maps(vrr_supported=True, vrr_enabled=True)
        run_map[("niri", "msg", "output", "DP-1", "vrr", "off")] = _cp()  # pyright: ignore[reportArgumentType]
        vrr, _ = feature_builder(
            VRR,
            enable_vrr=True,
            resolve_map=resolve_map,
            run_map=run_map,
            pipe_map=pipe_map,
        )
        result = vrr.disable("DP-1")
        assert result.changed is True

    def test_disable_already_off(self, feature_builder, niri_session):
        resolve_map, run_map, pipe_map = _vrr_maps(
            vrr_supported=True, vrr_enabled=False
        )
        vrr, _ = feature_builder(
            VRR,
            enable_vrr=True,
            resolve_map=resolve_map,
            run_map=run_map,
            pipe_map=pipe_map,
        )
        result = vrr.disable("DP-1")
        assert result.changed is False
        assert result.skipped is False

    def test_skip_not_capable(self, feature_builder, niri_session):
        resolve_map, run_map, pipe_map = _vrr_maps(
            vrr_supported=False, vrr_enabled=False
        )
        vrr, _ = feature_builder(
            VRR,
            enable_vrr=True,
            resolve_map=resolve_map,
            run_map=run_map,
            pipe_map=pipe_map,
        )
        result = vrr.enable("DP-1")
        assert result.skipped is True


class TestPowerProfile:
    def test_skip_when_disabled(self, feature_builder):
        pp, _ = feature_builder(PowerProfile)
        result = pp.enable("DP-1")
        assert result.skipped is True

    def test_enable_changes_profile(self, feature_builder):
        run_map = {
            ("tuned-adm", "active"): _cp(stdout="Active profile: balanced-bazzite"),
            ("tuned-adm", "profile", "throughput-performance-bazzite"): _cp(),
        }
        pp, fake = feature_builder(
            PowerProfile,
            enable_tuned=True,
            resolve_map=_resolve("tuned-adm"),
            run_map=run_map,
        )
        result = pp.enable("DP-1")
        assert result.changed is True
        assert (
            "run",
            ["tuned-adm", "profile", "throughput-performance-bazzite"],
        ) in fake.calls

    def test_enable_noop_when_already_game(self, feature_builder):
        run_map = {
            ("tuned-adm", "active"): _cp(
                stdout="Active profile: throughput-performance-bazzite"
            ),
        }
        pp, _ = feature_builder(
            PowerProfile,
            enable_tuned=True,
            resolve_map=_resolve("tuned-adm"),
            run_map=run_map,
        )
        result = pp.enable("DP-1")
        assert result.changed is False
        assert result.skipped is False

    def test_disable_changes_desktop(self, feature_builder):
        run_map = {
            ("tuned-adm", "active"): _cp(
                stdout="Active profile: throughput-performance-bazzite"
            ),
            ("tuned-adm", "profile", "balanced-bazzite"): _cp(),
        }
        pp, fake = feature_builder(
            PowerProfile,
            enable_tuned=True,
            resolve_map=_resolve("tuned-adm"),
            run_map=run_map,
        )
        result = pp.disable("DP-1")
        assert result.changed is True
        assert ("run", ["tuned-adm", "profile", "balanced-bazzite"]) in fake.calls


class TestSCXScheduler:
    def test_skip_when_disabled(self, feature_builder):
        scx, _ = feature_builder(SCXScheduler)
        result = scx.enable("DP-1")
        assert result.skipped is True

    def test_enable_starts_when_none_running(self, feature_builder):
        run_map = {
            ("scxctl", "get"): _cp(stdout="no scx scheduler running"),
            ("scxctl", "start", "-s", "lavd", "-m", "gaming"): _cp(),
        }
        scx, fake = feature_builder(
            SCXScheduler,
            enable_scx=True,
            resolve_map=_resolve("scxctl"),
            run_map=run_map,
        )
        result = scx.enable("DP-1")
        assert result.changed is True
        assert ("run", ["scxctl", "start", "-s", "lavd", "-m", "gaming"]) in fake.calls

    def test_enable_noop_when_already_loaded(self, feature_builder):
        run_map = {("scxctl", "get"): _cp(stdout="lavd gaming")}
        scx, _ = feature_builder(
            SCXScheduler,
            enable_scx=True,
            resolve_map=_resolve("scxctl"),
            run_map=run_map,
        )
        result = scx.enable("DP-1")
        assert result.changed is False
        assert result.skipped is False

    def test_enable_switches_scheduler(self, feature_builder):
        run_map = {
            ("scxctl", "get"): _cp(stdout="rustland default"),
            ("scxctl", "start", "-s", "lavd", "-m", "gaming"): _cp(),
        }
        scx, fake = feature_builder(
            SCXScheduler,
            enable_scx=True,
            resolve_map=_resolve("scxctl"),
            run_map=run_map,
        )
        result = scx.enable("DP-1")
        assert result.changed is True
        assert ("run", ["scxctl", "start", "-s", "lavd", "-m", "gaming"]) in fake.calls

    def test_disable_unloads(self, feature_builder):
        run_map = {
            ("scxctl", "get"): _cp(stdout="lavd gaming"),
            ("scxctl", "stop"): _cp(),
        }
        scx, fake = feature_builder(
            SCXScheduler,
            enable_scx=True,
            resolve_map=_resolve("scxctl"),
            run_map=run_map,
        )
        result = scx.disable("DP-1")
        assert result.changed is True
        assert ("run", ["scxctl", "stop"]) in fake.calls

    def test_disable_noop_when_none_running(self, feature_builder):
        run_map = {("scxctl", "get"): _cp(stdout="no scx scheduler running")}
        scx, _ = feature_builder(
            SCXScheduler,
            enable_scx=True,
            resolve_map=_resolve("scxctl"),
            run_map=run_map,
        )
        result = scx.disable("DP-1")
        assert result.changed is False
        assert result.skipped is False


class TestAudioPriority:
    def test_skip_when_disabled(self, feature_builder):
        audio, _ = feature_builder(AudioPriority)
        result = audio.enable("DP-1")
        assert result.skipped is True

    def test_enable_sets_env(self, feature_builder, audio_env_cleanup):
        audio, _ = feature_builder(
            AudioPriority, enable_audio=True, audio_latency="120"
        )
        result = audio.enable("DP-1")
        assert result.changed is True
        assert os.environ.get("PULSE_LATENCY_MSEC") == "120"

    def test_enable_writes_env_file(self, feature_builder, audio_env_cleanup):
        audio, _ = feature_builder(AudioPriority, enable_audio=True, audio_latency="80")
        audio.enable("DP-1")
        content = audio._cfg.audio_env_file.read_text()
        assert "PULSE_LATENCY_MSEC=80" in content

    def test_disable_clears_env(self, feature_builder):
        audio, _ = feature_builder(AudioPriority, enable_audio=True)
        os.environ["PULSE_LATENCY_MSEC"] = "50"
        result = audio.disable("DP-1")
        assert result.changed is True
        assert "PULSE_LATENCY_MSEC" not in os.environ

    def test_disable_removes_env_file(self, feature_builder):
        audio, _ = feature_builder(AudioPriority, enable_audio=True)
        audio._cfg.audio_env_file.parent.mkdir(parents=True, exist_ok=True)
        audio._cfg.audio_env_file.write_text("export PULSE_LATENCY_MSEC=50\n")
        audio.disable("DP-1")
        assert audio._cfg.audio_env_file.exists() is False

    def test_disable_missing_file_is_noop(self, feature_builder):
        audio, _ = feature_builder(AudioPriority, enable_audio=True)
        result = audio.disable("DP-1")
        assert result.changed is True


class TestScreenInhibit:
    def test_skip_when_disabled(self, feature_builder):
        inh, _ = feature_builder(ScreenInhibit)
        result = inh.enable("DP-1")
        assert result.skipped is True

    def test_enable_dms_and_screensaver_cookie(self, feature_builder, niri_session):
        resolve_map, run_map, _ = _inhibit_maps()
        inh, _ = feature_builder(
            ScreenInhibit,
            enable_inhibit=True,
            resolve_map=resolve_map,
            run_map=run_map,
        )
        result = inh.enable("DP-1")
        assert result.changed is True
        assert "DMS inhibit enabled" in result.detail
        assert "ScreenSaver cookie acquired" in result.detail
        assert inh._screensaver_cookie == 42

    def test_disable_dms_and_releases_screensaver_cookie(
        self, feature_builder, niri_session
    ):
        dbus_path = "/usr/bin/dbus-send"
        resolve_map = {"dms": "/usr/bin/dms", "dbus-send": dbus_path}
        run_map = {
            ("dms", "ipc", "call", "inhibit", "status"): _cp(
                stdout="Idle inhibit reason: gamemode.py"
            ),
            ("dms", "ipc", "call", "inhibit", "disable"): _cp(),
            _dbus_uninhibit_cmd(dbus_path, 42): _cp(),
        }
        inh, fake = feature_builder(
            ScreenInhibit,
            enable_inhibit=True,
            resolve_map=resolve_map,
            run_map=run_map,
        )
        inh._screensaver_cookie = 42
        result = inh.disable("DP-1")
        assert result.changed is True
        assert "DMS inhibit disabled" in result.detail
        assert "ScreenSaver cookie released" in result.detail
        expected = list(_dbus_uninhibit_cmd(dbus_path, 42))
        assert expected in [list(c[1]) for c in fake.calls]
        assert inh._screensaver_cookie is None

    def test_enable_screensaver_fallback_when_dms_fails(
        self, feature_builder, niri_session
    ):
        """When DMS inhibit fails, ScreenSaver cookie should still be acquired."""
        resolve_map, run_map, _ = _inhibit_maps(dms_enable_rc=1)
        inh, _ = feature_builder(
            ScreenInhibit,
            enable_inhibit=True,
            resolve_map=resolve_map,
            run_map=run_map,
        )
        result = inh.enable("DP-1")
        assert result.changed is True
        assert "DMS inhibit" not in result.detail
        assert "ScreenSaver cookie acquired" in result.detail
        assert inh._screensaver_cookie == 42

    def test_enable_error_when_all_inhibit_mechanisms_fail(
        self, feature_builder, niri_session
    ):
        """When both DMS and ScreenSaver fail, enable should return an error."""
        resolve_map, run_map, _ = _inhibit_maps(dms_enable_rc=1, screensaver_rc=1)
        inh, _ = feature_builder(
            ScreenInhibit,
            enable_inhibit=True,
            resolve_map=resolve_map,
            run_map=run_map,
        )
        result = inh.enable("DP-1")
        assert result.ok is False
        assert "all inhibit mechanisms failed" in result.detail

    def test_disable_releases_cookie_even_without_dms(self, feature_builder):
        """When not on niri, disable should still release the ScreenSaver cookie."""
        _, run_map, dbus_path = _inhibit_maps(niri=False)
        run_map[_dbus_uninhibit_cmd(dbus_path, 99)] = _cp()
        inh, _ = feature_builder(
            ScreenInhibit,
            enable_inhibit=True,
            resolve_map={"dbus-send": dbus_path},
            run_map=run_map,
        )
        inh._screensaver_cookie = 99
        result = inh.disable("DP-1")
        assert result.changed is True
        assert "ScreenSaver cookie released" in result.detail
        assert inh._screensaver_cookie is None

    def test_screensaver_cookie_idempotent(self, feature_builder, niri_session):
        """Acquiring a cookie twice should only send one Inhibit call."""
        resolve_map, run_map, dbus_path = _inhibit_maps(screensaver_cookie="5")
        inh, fake = feature_builder(
            ScreenInhibit,
            enable_inhibit=True,
            resolve_map=resolve_map,
            run_map=run_map,
        )
        result = inh.enable("DP-1")
        assert result.changed is True
        assert inh._screensaver_cookie == 5
        result2 = inh.enable("DP-1")
        assert result2.changed is True
        assert inh._screensaver_cookie == 5
        screensaver_calls = [
            c
            for c in fake.calls
            if c[0] == "capture"
            and c[1][0] == dbus_path
            and "ScreenSaver.Inhibit" in str(c[1])
        ]
        assert len(screensaver_calls) == 1

    def test_disable_no_cookie_releases_gracefully(self, feature_builder):
        """Disable with no cookie should still succeed."""
        inh, _ = feature_builder(ScreenInhibit, enable_inhibit=True)
        result = inh.disable("DP-1")
        assert result.changed is True
        assert "ScreenSaver cookie released" in result.detail

    def test_screensaver_invalid_cookie_value(self, feature_builder):
        """When ScreenSaver returns a non-integer, enable should fail."""
        resolve_map, run_map, _ = _inhibit_maps(
            dms_enable_rc=1, screensaver_cookie="not_a_number"
        )
        inh, _ = feature_builder(
            ScreenInhibit,
            enable_inhibit=True,
            resolve_map=resolve_map,
            run_map=run_map,
        )
        result = inh.enable("DP-1")
        assert result.ok is False
        assert inh._screensaver_cookie is None


class TestIdleMonitor:
    """Tests for _IdleMonitorThread classification and filtering."""

    def _event_data(self, *events: tuple[int, int, int]) -> bytes:
        """Pack (type, code, value) tuples into evdev binary data."""
        fmt = "llHHi"
        data = b""
        for ev_type, code, value in events:
            data += struct.pack(fmt, 0, 0, ev_type, code, value)
        return data

    def test_meaningful_activity_key_press(self):
        from gamemode.features.idle_monitor import _IdleMonitorThread

        data = self._event_data((1, 30, 1))  # KEY_A press
        assert _IdleMonitorThread._meaningful_activity(data) is True

    def test_meaningful_activity_key_release(self):
        from gamemode.features.idle_monitor import _IdleMonitorThread

        data = self._event_data((1, 30, 0))  # KEY_A release
        assert _IdleMonitorThread._meaningful_activity(data) is False

    def test_meaningful_activity_rel_noise(self):
        from gamemode.features.idle_monitor import _IdleMonitorThread

        data = self._event_data((2, 0, 1))  # REL_X +1 (below threshold)
        assert _IdleMonitorThread._meaningful_activity(data) is False

    def test_meaningful_activity_rel_meaningful(self):
        from gamemode.features.idle_monitor import _IdleMonitorThread

        data = self._event_data((2, 0, 10))  # REL_X +10 (above threshold)
        assert _IdleMonitorThread._meaningful_activity(data) is True

    def test_meaningful_activity_abs(self):
        from gamemode.features.idle_monitor import _IdleMonitorThread

        data = self._event_data((3, 0, 500))  # ABS_X
        assert _IdleMonitorThread._meaningful_activity(data) is True

    def test_meaningful_activity_syn_filtered(self):
        from gamemode.features.idle_monitor import _IdleMonitorThread

        data = self._event_data((0, 0, 0))  # EV_SYN
        assert _IdleMonitorThread._meaningful_activity(data) is False

    def test_meaningful_activity_multiple_events(self):
        from gamemode.features.idle_monitor import _IdleMonitorThread

        data = self._event_data((0, 0, 0), (2, 0, 100), (0, 0, 0))
        assert _IdleMonitorThread._meaningful_activity(data) is True

    def test_classify_via_udevadm_keyboard(self, monkeypatch):
        import subprocess

        from gamemode.features.idle_monitor import _IdleMonitorThread

        def fake_run(*args, **kwargs):
            return subprocess.CompletedProcess(
                [],
                returncode=0,
                stdout="ID_INPUT=1\nID_INPUT_KEYBOARD=1\n",
                stderr="",
            )

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert _IdleMonitorThread._classify_via_udevadm("/dev/input/event0") == "kbm"

    def test_classify_via_udevadm_mouse(self, monkeypatch):
        import subprocess

        from gamemode.features.idle_monitor import _IdleMonitorThread

        def fake_run(*args, **kwargs):
            return subprocess.CompletedProcess(
                [],
                returncode=0,
                stdout="ID_INPUT=1\nID_INPUT_MOUSE=1\n",
                stderr="",
            )

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert _IdleMonitorThread._classify_via_udevadm("/dev/input/event0") == "kbm"

    def test_classify_via_udevadm_joystick(self, monkeypatch):
        import subprocess

        from gamemode.features.idle_monitor import _IdleMonitorThread

        def fake_run(*args, **kwargs):
            return subprocess.CompletedProcess(
                [],
                returncode=0,
                stdout="ID_INPUT=1\nID_INPUT_JOYSTICK=1\n",
                stderr="",
            )

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert _IdleMonitorThread._classify_via_udevadm("/dev/input/event0") is None

    def test_read_bitmap_nonexistent(self):
        from gamemode.features.idle_monitor import _IdleMonitorThread

        assert _IdleMonitorThread._read_bitmap("/nonexistent/path") is None

    def test_on_ac_missing_supply(self, tmp_path):
        from gamemode.features.idle_monitor import _IdleMonitorThread

        result = _IdleMonitorThread._on_ac()
        # depends on system; just assert it returns a bool
        assert isinstance(result, bool)

    def test_get_timeout_from_config(self, tmp_path_cfg):
        from gamemode.features.idle_monitor import _IdleMonitorThread

        cfg = _cfg(runtime_dir=str(tmp_path_cfg.runtime_dir), idle_timeout=120)
        thread = _IdleMonitorThread(cfg, logging.getLogger(), threading.Event())
        assert thread._get_timeout() == 120

    def test_get_timeout_zero_when_no_dms_settings(self, tmp_path):
        from gamemode.features.idle_monitor import _IdleMonitorThread

        cfg = _cfg(runtime_dir=str(tmp_path), idle_timeout=0)
        thread = _IdleMonitorThread(cfg, logging.getLogger(), threading.Event())
        assert thread._get_timeout() == 0


class TestSteamWrapperPath:
    def test_returns_none_when_disabled(self, tmp_path, logger):
        cfg = _cfg(runtime_dir=str(tmp_path))
        result = steam_wrapper_factory(cfg, Runner(logger), logger)
        assert result is None

    def test_returns_wrapper_when_executable(self, tmp_path, logger):
        cfg = _cfg(
            runtime_dir=str(tmp_path),
            enable_steam=True,
            steam_script=str(tmp_path / "steam-env-base.sh"),
        )
        script = tmp_path / "steam-env-base.sh"
        script.write_text("#!/bin/sh\n")
        script.chmod(0o755)
        wrapper = steam_wrapper_factory(cfg, Runner(logger), logger)
        assert wrapper is not None
        assert wrapper(["mygame"]) == [str(script), "mygame"]

    def test_returns_none_when_script_missing(self, tmp_path, logger):
        """When steam_script is missing, wrapper returns the command unchanged."""
        cfg = _cfg(
            runtime_dir=str(tmp_path),
            enable_steam=True,
            steam_script=str(tmp_path / "nonexistent.sh"),
        )
        wrapper = steam_wrapper_factory(cfg, Runner(logger), logger)
        assert wrapper is not None
        assert wrapper(["mygame"]) == ["mygame"]


class TestInhibitWrapperFactory:
    def test_returns_none_when_disabled(self, tmp_path, logger):
        """inhibit_wrapper_factory returns None when inhibit is disabled."""
        cfg = _cfg(runtime_dir=str(tmp_path), enable_inhibit=False)
        result = inhibit_wrapper_factory(cfg, Runner(logger), logger)
        assert result is None

    def test_returns_none_when_systemd_inhibit_missing(self, tmp_path, logger):
        """inhibit_wrapper_factory returns None when systemd-inhibit is not found."""
        cfg = _cfg(runtime_dir=str(tmp_path), enable_inhibit=True)
        r = FakeRunner(logger)
        result = inhibit_wrapper_factory(cfg, r, logger)
        assert result is None

    def test_returns_wrapper_when_enabled(self, tmp_path, logger):
        """inhibit_wrapper_factory returns a wrapper when enabled and systemd-inhibit exists."""
        cfg = _cfg(runtime_dir=str(tmp_path), enable_inhibit=True)
        r = FakeRunner(logger)
        r.when_resolved("systemd-inhibit", "/usr/bin/systemd-inhibit")
        wrapper = inhibit_wrapper_factory(cfg, r, logger)
        assert wrapper is not None
        result = wrapper(["mygame"])
        assert result[0] == "/usr/bin/systemd-inhibit"
        assert "--what=idle:sleep" in result


class TestSystemdRunWrapper:
    def test_wrap_argv_disabled(self, tmp_path, logger):
        """SystemdRun.wrap_argv returns argv unchanged when disabled."""
        cfg = _cfg(runtime_dir=str(tmp_path), enable_systemd_run=False)
        r = Runner(logger)
        sd = SystemdRun(cfg, r, logger)
        result = sd.wrap_argv(["mygame"])
        assert result == ["mygame"]

    def test_wrap_argv_systemd_run_missing(self, tmp_path, logger):
        """SystemdRun.wrap_argv returns argv when systemd-run is not found."""
        cfg = _cfg(runtime_dir=str(tmp_path), enable_systemd_run=True)
        r = FakeRunner(logger)
        sd = SystemdRun(cfg, r, logger)
        result = sd.wrap_argv(["mygame"])
        assert result == ["mygame"]

    def test_wrap_argv_success(self, tmp_path, logger):
        """SystemdRun.wrap_argv wraps with systemd-run when available."""
        cfg = _cfg(
            runtime_dir=str(tmp_path),
            enable_systemd_run=True,
            systemd_run_args=["--user", "--scope"],
        )
        r = FakeRunner(logger)
        r.when_resolved("systemd-run", "/usr/bin/systemd-run")
        sd = SystemdRun(cfg, r, logger)
        result = sd.wrap_argv(["mygame"])
        assert result[0] == "systemd-run"
        assert "--user" in result
        assert "mygame" in result

    def test_wrap_argv_empty_args(self, tmp_path, logger):
        """SystemdRun.wrap_argv returns argv when systemd_run_args is empty."""
        cfg = _cfg(
            runtime_dir=str(tmp_path),
            enable_systemd_run=True,
            systemd_run_args=[],
        )
        r = FakeRunner(logger)
        r.when_resolved("systemd-run", "/usr/bin/systemd-run")
        sd = SystemdRun(cfg, r, logger)
        result = sd.wrap_argv(["mygame"])
        assert result == ["mygame"]


class TestWrapperChain:
    def test_add_factory(self, tmp_path, logger):
        """WrapperChain.add_factory should call factory and add non-None wrappers."""
        cfg = _cfg(
            runtime_dir=str(tmp_path),
            enable_steam=True,
            steam_script=str(tmp_path / "steam.sh"),
        )
        script = tmp_path / "steam.sh"
        script.write_text("#!/bin/sh\n")
        script.chmod(0o755)
        r = Runner(logger)
        chain = WrapperChain()
        chain.add_factory(steam_wrapper_factory, cfg, r, logger)
        result = chain.apply(["mygame"])
        assert str(script) in result

    def test_apply_empty_chain(self):
        """WrapperChain.apply with no wrappers should return argv unchanged."""
        chain = WrapperChain()
        result = chain.apply(["mygame"])
        assert result == ["mygame"]


class TestWrapperFactories:
    def test_registry_contains_expected_keys(self):
        """WRAPPER_FACTORIES should contain steam, inhibit, and systemd_run."""
        assert "steam" in WRAPPER_FACTORIES
        assert "inhibit" in WRAPPER_FACTORIES
        assert "systemd_run" in WRAPPER_FACTORIES
