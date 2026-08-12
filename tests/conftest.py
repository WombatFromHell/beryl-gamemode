"""Centralized fixtures, factories, and test helpers."""

import fcntl
import json
import logging
import os
import subprocess
import time
from typing import Any
from unittest.mock import patch

import pytest

from gamemode import actions as gamemode_actions
from gamemode.config import Config
from gamemode.feature import FeatureResult, _BaseFeature
from gamemode.runner import Runner
from gamemode.state import StateManager


class FakeFeature(_BaseFeature):
    """Trivial feature that records enable/disable calls.

    Usage::

        ff = FakeFeature("my_feature")
        ff = FakeFeature("my_feature", enable_result=FeatureResult.noop())
    """

    def __init__(
        self,
        name: str,
        enable_result: FeatureResult | None = None,
        disable_result: FeatureResult | None = None,
    ):
        self.name = name
        self.enable_calls: list[str] = []
        self.disable_calls: list[str] = []
        self.enable_result = enable_result or FeatureResult.did_change(
            f"{name} enabled"
        )
        self.disable_result = disable_result or FeatureResult.did_change(
            f"{name} disabled"
        )

    def enable(self, output: str) -> FeatureResult:
        self.enable_calls.append(output)
        return self.enable_result

    def disable(self, output: str) -> FeatureResult:
        self.disable_calls.append(output)
        return self.disable_result


# ============================================================================
# Test support helpers
# ============================================================================


def _cfg(**overrides: Any) -> Config:
    """Build a frozen Config with every toggle off and paths in *tmp_path*."""
    defaults: dict[str, Any] = {
        "enable_scx": False,
        "enable_vrr": False,
        "enable_tuned": False,
        "enable_inhibit": False,
        "enable_sleep_inhibit": False,
        "enable_audio": False,
        "enable_steam": False,
        "enable_systemd_run": False,
        "scx_scheduler": "lavd",
        "scx_mode": "gaming",
        "profile_game": "throughput-performance-bazzite",
        "profile_desktop": "balanced-bazzite",
        "audio_latency": "60",
        "steam_script": "",
        "vrr_output_default": "DP-1",
        "runtime_dir": "/tmp",
    }
    defaults.update(overrides)
    return Config(**defaults)


def spawn_child(tmp_path, script_body, script_name="child.py", ready_name="ready"):
    """Write a script, spawn it, wait for the ready file, return the Popen."""
    script = tmp_path / script_name
    script.write_text(script_body)
    ready_path = tmp_path / ready_name
    proc = subprocess.Popen(
        ["python3", str(script)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    for _ in range(50):
        if ready_path.exists():
            return proc
        time.sleep(0.1)
    proc.kill()
    proc.wait()
    pytest.fail(f"Child {script_name} did not become ready")


def mock_collect_features(features):
    """Context manager that patches collect_features to return the given features."""
    return patch.object(gamemode_actions, "collect_features", return_value=features)


def _dep_runner(logger, enable_scx, enable_vrr, enable_tuned, enable_inhibit, enable_sleep_inhibit):
    """Build a FakeRunner with dependency resolutions for the given feature toggles."""
    r = FakeRunner(logger)
    r.when_resolved("scxctl", "/usr/bin/scxctl" if enable_scx else None)
    r.when_resolved("jq", "/usr/bin/jq" if enable_vrr else None)
    r.when_resolved("tuned-adm", "/usr/bin/tuned-adm" if enable_tuned else None)
    r.when_resolved(
        "systemd-inhibit",
        "/usr/bin/systemd-inhibit" if enable_sleep_inhibit else None,
    )
    r.when_resolved("dbus-send", "/usr/bin/dbus-send" if enable_inhibit else None)
    return r


def _state(cfg):
    """Return an initialized StateManager for the given config."""
    sm = StateManager(cfg)
    sm.init()
    return sm


def _cp(stdout: str = "", stderr: str = "", rc: int = 0):
    """Shorthand for a successful CompletedProcess."""
    return subprocess.CompletedProcess([], returncode=rc, stdout=stdout, stderr=stderr)


def _resolve(cmd: str) -> dict[str, str]:
    """Build a single-entry resolve map for a command assumed to be in /usr/bin."""
    return {cmd: f"/usr/bin/{cmd}"}


class FakeRunner(Runner):
    """Runner subclass that returns canned responses.

    Usage::

        r = FakeRunner(logger)
        r.when_resolved("niri", "/usr/bin/niri")
        r.when_run(("niri", "msg"), stdout='{"DP-1": {}}')
        r.when_pipe(("jq", "-r", "..."), stdout="true")
    """

    def __init__(self, log):
        super().__init__(log)
        self._resolve_map: dict[str, str | None] = {}
        self._run_map: dict[tuple, subprocess.CompletedProcess[str]] = {}
        self._pipe_map: dict[tuple, subprocess.CompletedProcess[str]] = {}
        self.calls: list[tuple[str, list[str]]] = []

    def when_resolved(self, cmd: str, path: str | None = None):
        self._resolve_map[cmd] = path
        return self

    def when_run(
        self, args: tuple | list, stdout: str = "", stderr: str = "", rc: int = 0
    ):
        self._run_map[tuple(args)] = _cp(stdout=stdout, stderr=stderr, rc=rc)
        return self

    def when_pipe(
        self, args: tuple | list, stdout: str = "", stderr: str = "", rc: int = 0
    ):
        self._pipe_map[tuple(args)] = _cp(stdout=stdout, stderr=stderr, rc=rc)
        return self

    def resolve(self, cmd):
        return self._resolve_map.get(cmd)

    def run(self, args, **kwargs):
        self.calls.append(("run", list(args)))
        key = tuple(args)
        if key in self._run_map:
            return self._run_map[key]
        return _cp()

    def capture(self, args):
        self.calls.append(("capture", list(args)))
        return self.run(args)

    def pipe(self, args, input_data):
        self.calls.append(("pipe", list(args)))
        key = tuple(args)
        if key in self._pipe_map:
            return self._pipe_map[key]
        return self.run(args)


def _make_feature(
    FeatureClass, cfg, logger, *, resolve_map=None, run_map=None, pipe_map=None
):
    """Create a FakeRunner + instantiate a feature, returning both."""
    r = FakeRunner(logger)
    if resolve_map:
        for cmd, path in resolve_map.items():
            r.when_resolved(cmd, path)
    if run_map:
        for args, result in run_map.items():
            r.when_run(
                args, stdout=result.stdout, stderr=result.stderr, rc=result.returncode
            )
    if pipe_map:
        for args, result in pipe_map.items():
            r.when_pipe(
                args, stdout=result.stdout, stderr=result.stderr, rc=result.returncode
            )
    return FeatureClass(cfg, r, logger), r


def _vrr_maps(vrr_supported=True, vrr_enabled=False, output="DP-1"):
    """Return (resolve_map, run_map, pipe_map) for a VRR test scenario."""
    niri_json = json.dumps(
        {output: {"vrr_supported": vrr_supported, "vrr_enabled": vrr_enabled}}
    )
    resolve_map = {"niri": "/usr/bin/niri", "jq": "/usr/bin/jq"}
    run_map = {("niri", "msg", "-j", "outputs"): _cp(stdout=niri_json)}
    pipe_map = {
        ("jq", "-r", "--arg", "o", output, ".[$o].vrr_supported // true"): _cp(
            stdout=str(vrr_supported).lower()
        ),
        (
            "jq",
            "-r",
            "--arg",
            "o",
            output,
            (
                'if .[$o].vrr_enabled == true then "true" '
                'elif .[$o].vrr_enabled == false then "false" '
                'else "" end'
            ),
        ): _cp(stdout=str(vrr_enabled).lower()),
    }
    return resolve_map, run_map, pipe_map


def _inhibit_maps(
    *,
    dms_status="Idle inhibit is disabled",
    dms_enable_rc=0,
    screensaver_cookie="42",
    screensaver_rc=0,
    niri=True,
):
    """Return (resolve_map, run_map, dbus_path) for a ScreenInhibit test scenario."""
    dbus_path = "/usr/bin/dbus-send"
    resolve_map = {"dms": "/usr/bin/dms" if niri else None, "dbus-send": dbus_path}
    run_map = {
        ("dms", "ipc", "call", "inhibit", "status"): _cp(stdout=dms_status),
        ("dms", "ipc", "call", "inhibit", "enable"): _cp(rc=dms_enable_rc),
        (
            "dms",
            "ipc",
            "call",
            "inhibit",
            "reason",
            "gamemode.py gaming session",
        ): _cp(),
        (
            dbus_path,
            "--session",
            "--dest=org.freedesktop.ScreenSaver",
            "--type=method_call",
            "--print-reply=literal",
            "/ScreenSaver",
            "org.freedesktop.ScreenSaver.Inhibit",
            "string:gamemode.py",
            "string:gamemode.py gaming session",
        ): _cp(stdout=screensaver_cookie, rc=screensaver_rc),
    }
    return resolve_map, run_map, dbus_path


def _dbus_uninhibit_cmd(dbus_path, cookie):
    """Build the ScreenSaver.UnInhibit command as the implementation does."""
    return (
        dbus_path,
        "--session",
        "--dest=org.freedesktop.ScreenSaver",
        "--type=method_call",
        "--print-reply=literal",
        "/ScreenSaver",
        "org.freedesktop.ScreenSaver.UnInhibit",
        f"uint32:{cookie}",
    )


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture()
def tmp_path_cfg(tmp_path):
    """Provide a Config with all toggles off and state paths in *tmp_path*."""
    return _cfg(runtime_dir=str(tmp_path))


@pytest.fixture()
def logger():
    """A deterministic logger."""
    log = logging.getLogger("gamemode.test")
    log.handlers.clear()
    log.setLevel(logging.DEBUG)
    log.addHandler(logging.NullHandler())
    return log


@pytest.fixture()
def runner(logger):
    """A real Runner backed by the fixture logger."""
    return Runner(logger)


@pytest.fixture()
def niri_session(monkeypatch):
    """Fake a niri compositor session via environment variables."""
    monkeypatch.setenv("XDG_SESSION_DESKTOP", "niri")
    monkeypatch.delenv("XDG_CURRENT_DESKTOP", raising=False)
    monkeypatch.setattr("gamemode.compositor.compositor_is_niri", lambda: True)
    # Also patch in feature modules that import compositor_is_niri at module level
    monkeypatch.setattr("gamemode.features.vrr.compositor_is_niri", lambda: True)
    monkeypatch.setattr(
        "gamemode.features.screen_inhibit.compositor_is_niri", lambda: True
    )


@pytest.fixture()
def fake_runner(logger):
    """A FakeRunner with canned responses."""
    return FakeRunner(logger)


@pytest.fixture()
def feature_builder(tmp_path_cfg, logger):
    """Factory for building feature instances with canned responses."""

    def build(
        FeatureClass, *, resolve_map=None, run_map=None, pipe_map=None, **cfg_overrides
    ):
        cfg = _cfg(runtime_dir=tmp_path_cfg.runtime_dir, **cfg_overrides)
        return _make_feature(
            FeatureClass,
            cfg,
            logger,
            resolve_map=resolve_map or {},
            run_map=run_map or {},
            pipe_map=pipe_map or {},
        )

    return build


@pytest.fixture()
def state_manager(tmp_path_cfg):
    """Provide an already-initialised StateManager."""
    sm = StateManager(tmp_path_cfg)
    sm.init()
    return sm


@pytest.fixture()
def held_lock(tmp_path_cfg):
    """Hold the state manager lock for the duration of the test."""
    tmp_path_cfg.state_dir.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(tmp_path_cfg.lock_file), os.O_CREAT | os.O_WRONLY)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    yield fd
    fcntl.flock(fd, fcntl.LOCK_UN)
    os.close(fd)


@pytest.fixture()
def disabled_features_env(monkeypatch):
    """Disable all feature env vars so Config() uses defaults."""
    monkeypatch.setenv("ENABLE_SCX_SCHEDULER", "false")
    monkeypatch.setenv("ENABLE_VRR", "false")
    monkeypatch.setenv("ENABLE_PERFORMANCE_MODE", "false")
    monkeypatch.setenv("ENABLE_SCREEN_KEEP_AWAKE", "false")
    monkeypatch.setenv("ENABLE_AUDIO_PRIORITY_BOOST", "false")
    monkeypatch.setenv("ENABLE_STEAM_ENV", "false")
    monkeypatch.setenv("ENABLE_SYSTEMD_RUN", "false")


@pytest.fixture()
def audio_env_cleanup(monkeypatch):
    """Reset PULSE_LATENCY_MSEC after each test."""
    monkeypatch.delenv("PULSE_LATENCY_MSEC", raising=False)
