"""Tests for actions module: action_on/off/status/wrapper, _watch_parent, lock lifetime."""

import ctypes
import ctypes.util
import json
import os
import signal
import subprocess
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from conftest import FakeFeature, _cfg, _state, mock_collect_features, spawn_child

from gamemode import actions as gamemode_actions
from gamemode.actions import (
    _watch_parent,
    action_off,
    action_on,
    action_status,
    action_wrapper,
)
from gamemode.runner import Runner
from gamemode.state import StateManager


def _spawn_signal_wrapper(tmp_path: Path, tag: str) -> subprocess.Popen:
    """Spawn a wrapper child running /bin/sleep 60, recording feature disable
    to feature_state_<tag>.json and exiting with action_wrapper's return code."""
    state_file = tmp_path / f"feature_state_{tag}.json"
    ready_file = tmp_path / "signal_ready"
    gamemode_dir = str(Path(__file__).parent.parent / "src")
    return spawn_child(
        tmp_path,
        f"""
import sys, os, time, json, signal
from unittest.mock import MagicMock, patch
from contextlib import contextmanager
sys.path.insert(0, {gamemode_dir!r})
from gamemode.config import Config
from gamemode.state import StateManager
from gamemode.feature import FeatureResult
from gamemode.runner import Runner
from gamemode import actions
import logging

logger = logging.getLogger("gamemode")
logger.setLevel(logging.DEBUG)
logger.addHandler(logging.NullHandler())

cfg = Config(
    enable_scx=False, enable_vrr=False, enable_tuned=False,
    enable_inhibit=False, enable_audio=False, enable_steam=False,
    runtime_dir={str(tmp_path)!r}, vrr_output_default="DP-1",
)
state = StateManager(cfg)
state.init()
state_file = {str(state_file)!r}
ready_file = {str(ready_file)!r}

class RecordFeature:
    def __init__(self):
        self.en = []
        self.dis = []
    def enable(self):
        self.en.append(True)
        return FeatureResult.did_change("en")
    def disable(self):
        self.dis.append(True)
        with open(state_file, "w") as f:
            json.dump({{"en": self.en, "dis": self.dis}}, f)
        return FeatureResult.did_change("dis")

feat = RecordFeature()
features = [("fake", feat)]
runner = Runner(logger)

# Capture the original guard
original_signal_guard = actions._signal_guard

@contextmanager
def delayed_ready_guard(log, child_proc):
    # Write the ready file ONLY after the signal handlers are safely installed
    with original_signal_guard(log, child_proc) as pending:
        with open(ready_file, "w") as f:
            f.write("ready")
        yield pending

with patch.object(actions, "collect_features", return_value=features):
    with patch.object(actions, "_signal_guard", delayed_ready_guard):
        rc = actions.action_wrapper(cfg, runner, logger, ["/bin/sleep", "60"])
sys.exit(rc)
""",
        script_name=f"wrapper_signal_{tag}.py",
        ready_name="signal_ready",
    )


class TestActionWrapper:
    def test_cleanup_fires_on_normal_child_exit(self, tmp_path, logger):
        """When the child exits normally, cleanup must run."""
        cfg = _cfg(runtime_dir=str(tmp_path))
        state = _state(cfg)
        state.init()
        feature_a = FakeFeature("a")
        features = [("fake_a", feature_a)]
        true_runner = Runner(logger)
        with (
            mock_collect_features(features),
            patch.object(Runner, "resolve", return_value="/bin/true"),
        ):
            retcode = action_wrapper(cfg, true_runner, logger, ["/bin/true"])
        assert retcode == 0
        assert feature_a.disable_calls == [True]
        assert state.mode == ""

    @pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT])
    def test_cleanup_fires_on_signal(self, tmp_path, logger, signum):
        """When the wrapper receives SIGTERM/SIGINT, cleanup must run before exit."""
        tag = f"cleanup_{signum.name.lower()}"
        child = _spawn_signal_wrapper(tmp_path, tag)
        child.send_signal(signum)
        child.wait(timeout=10)
        cfg = _cfg(runtime_dir=str(tmp_path))
        state_file = tmp_path / f"feature_state_{tag}.json"
        assert state_file.exists(), (
            f"Child did not write feature state (rc={child.returncode})"
        )
        result = json.loads(state_file.read_text())
        assert result["dis"] == [True], f"Cleanup did not run: {result}"
        assert StateManager(cfg).mode == "", "State not cleared"

    @pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT])
    def test_signal_exit_code_is_child_wait_status(self, tmp_path, logger, signum):
        """After a signal, the exit code is the wrapped child's wait() status
        (-9 from SIGKILL, masked to 247 by sys.exit) — never 128+signum."""
        tag = f"rc_{signum.name.lower()}"
        child = _spawn_signal_wrapper(tmp_path, tag)
        child.send_signal(signum)
        child.wait(timeout=10)
        assert child.returncode == (-9) & 0xFF
        assert child.returncode != 128 + signum

    def test_concurrent_wrapper_skips(self, tmp_path_cfg, logger, held_lock):
        """A second wrapper instance should skip when the first holds the lock."""
        cfg = _cfg(runtime_dir=tmp_path_cfg.runtime_dir)
        state = _state(cfg)
        state.init()
        feature_a = FakeFeature("a")
        features = [("fake_a", feature_a)]
        runner = Runner(logger)
        with mock_collect_features(features):
            retcode = action_wrapper(cfg, runner, logger, ["/bin/true"])
        assert retcode == 0
        assert feature_a.enable_calls == []
        assert feature_a.disable_calls == []

    def test_child_nonzero_exitcode_propagated(self, tmp_path, logger):
        """The wrapper must return the child's exit code after cleanup."""
        cfg = _cfg(runtime_dir=str(tmp_path))
        state = _state(cfg)
        state.init()
        features = [("fake", FakeFeature("x"))]
        runner = Runner(logger)
        with (
            mock_collect_features(features),
            patch.object(Runner, "resolve", return_value="/bin/false"),
        ):
            retcode = action_wrapper(cfg, runner, logger, ["/bin/false"])
        assert retcode == 1
        assert features[0][1].disable_calls == [True]
        assert state.mode == ""

    def test_cleanup_runs_even_on_oserror(self, tmp_path, logger):
        """If exec fails (OSError), cleanup must still run."""
        cfg = _cfg(runtime_dir=str(tmp_path))
        state = _state(cfg)
        state.init()
        feature_a = FakeFeature("a")
        features = [("fake_a", feature_a)]
        runner = Runner(logger)
        with (
            mock_collect_features(features),
            patch.object(Runner, "resolve", return_value="/nonexistent/bin/cmd"),
        ):
            retcode = action_wrapper(cfg, runner, logger, ["/nonexistent/bin/cmd"])
        assert retcode == 1
        assert feature_a.disable_calls == [True]
        assert state.mode == ""


class TestWatchParent:
    """Tests for the parent-death signal mechanism.

    _watch_parent uses prctl(PR_SET_PDEATHSIG, SIGTERM).  We verify the
    Python code path (error handling, CDL loading) rather than the kernel
    guarantee itself, which is tested by the kernel.
    """

    def test_watch_parent_no_libc(self, tmp_path, logger):
        """When find_library returns None, _watch_parent should not raise."""
        with patch.object(
            ctypes.util,  # pyright: ignore[reportAttributeAccessIssue]
            "find_library",
            return_value=None,
        ):
            _watch_parent(logger)  # should not raise

    def test_watch_parent_prctl_fails(self, tmp_path, logger):
        """When prctl returns non-zero, _watch_parent should log a warning."""
        import ctypes

        class FakeLibc:
            @staticmethod
            def prctl(*args):
                return -1

            @staticmethod
            def strerror(err):
                return "error"

        with (
            patch.object(
                ctypes.util,  # pyright: ignore[reportAttributeAccessIssue]
                "find_library",
                return_value="/lib/libc.so",
            ),
            patch.object(ctypes, "CDLL", return_value=FakeLibc),
        ):
            _watch_parent(logger)  # should not raise

    def test_watch_parent_success(self, tmp_path, logger):
        """When prctl succeeds, _watch_parent should not raise."""
        import ctypes

        class FakeLibc:
            @staticmethod
            def prctl(*args):
                return 0

        with (
            patch.object(
                ctypes.util,  # pyright: ignore[reportAttributeAccessIssue]
                "find_library",
                return_value="/lib/libc.so",
            ),
            patch.object(ctypes, "CDLL", return_value=FakeLibc),
        ):
            _watch_parent(logger)  # should not raise


class TestStateManagerLockLifetime:
    def test_lock_held_during_child_execution(self, tmp_path, logger):
        """Integration: while action_wrapper runs a child, the lock must be held."""
        cfg = _cfg(runtime_dir=str(tmp_path))
        state = _state(cfg)
        state.init()
        ready_file = tmp_path / "lock_ready"
        gamemode_dir = str(Path(__file__).parent.parent / "src")
        child = spawn_child(
            tmp_path,
            f"""
import sys, os, time
from unittest.mock import MagicMock, patch
sys.path.insert(0, {gamemode_dir!r})
from gamemode.config import Config
from gamemode.feature import FeatureResult
from gamemode.runner import Runner
from gamemode import actions
import logging

logger = logging.getLogger("gamemode")
logger.setLevel(logging.DEBUG)
logger.addHandler(logging.NullHandler())

cfg = Config(
    enable_scx=False, enable_vrr=False, enable_tuned=False,
    enable_inhibit=False, enable_audio=False, enable_steam=False,
    runtime_dir={str(tmp_path)!r}, vrr_output_default="DP-1",
)

class RecordFeature:
    def __init__(self):
        self.en = []
        self.dis = []
    def enable(self):
        self.en.append(True)
        with open({str(ready_file)!r}, "w") as f:
            f.write("ready")
        time.sleep(0.1)
        return FeatureResult.did_change("en")
    def disable(self):
        self.dis.append(True)
        return FeatureResult.did_change("dis")

feat = RecordFeature()
features = [("fake", feat)]

with patch.object(actions, "collect_features", return_value=features):
    actions.action_wrapper(cfg, Runner(logger), logger, ["/bin/true"])
""",
            script_name="lock_lifetime_child.py",
            ready_name="lock_ready",
        )
        for _ in range(50):
            probe_fd = os.open(str(cfg.lock_file), os.O_CREAT | os.O_WRONLY)
            try:
                held = state._try_lock(probe_fd)
            finally:
                state._unlock(probe_fd)
                os.close(probe_fd)
            if not held:
                break
            time.sleep(0.1)
        else:
            pytest.fail("Lock was not held during child execution")
        child.wait(timeout=10)


class TestActionOn:
    """Tests for action_on flow."""

    def test_action_on_calls_features_enable(self, tmp_path, logger):
        """action_on should call features_enable after marking active."""
        cfg = _cfg(runtime_dir=str(tmp_path))
        state = _state(cfg)
        state.init()
        ff = FakeFeature("test")
        features = [("test", ff)]
        runner = Runner(logger)
        with mock_collect_features(features):
            ret = action_on(cfg, runner, logger)
        assert ret == 0
        assert state.is_active
        assert ff.enable_calls == [True]

    def test_action_on_idempotent(self, tmp_path, logger):
        """action_on when already active should return 0 without re-enabling."""
        cfg = _cfg(runtime_dir=str(tmp_path))
        state = _state(cfg)
        state.init()
        state.mark_active()
        ff = FakeFeature("test")
        features = [("test", ff)]
        runner = Runner(logger)
        with mock_collect_features(features):
            ret = action_on(cfg, runner, logger)
        assert ret == 0
        assert ff.enable_calls == []

    def test_action_on_skips_when_wrapper_active(self, tmp_path, logger):
        """action_on when wrapper mode is active should skip."""
        cfg = _cfg(runtime_dir=str(tmp_path))
        state = _state(cfg)
        state.init()
        state.mark_wrapper(["/bin/test"])
        ff = FakeFeature("test")
        features = [("test", ff)]
        runner = Runner(logger)
        with mock_collect_features(features):
            ret = action_on(cfg, runner, logger)
        assert ret == 0
        assert ff.enable_calls == []


class TestActionOff:
    """Tests for action_off flow."""

    def test_action_off_calls_features_disable_and_clears(self, tmp_path, logger):
        """action_off should call features_disable then clear state."""
        cfg = _cfg(runtime_dir=str(tmp_path))
        state = _state(cfg)
        state.init()
        state.mark_active()
        ff = FakeFeature("test")
        features = [("test", ff)]
        runner = Runner(logger)
        with mock_collect_features(features):
            ret = action_off(cfg, runner, logger)
        assert ret == 0
        assert ff.disable_calls == [True]
        assert state.mode == ""


class TestActionStatus:
    """Tests for action_status."""

    def test_action_status_output(self, tmp_path, capsys):
        """action_status should print state information."""
        cfg = _cfg(runtime_dir=str(tmp_path))
        state = _state(cfg)
        state.init()
        ret = action_status(cfg)
        assert ret == 0
        output = capsys.readouterr().out
        assert "Mode:" in output
        assert "Compositor:" in output


class TestCleanupClosure:
    """Tests for _build_cleanup_closure."""

    def test_cleanup_idempotent(self, tmp_path, logger):
        """Calling cleanup twice should only run once."""
        cfg = _cfg(runtime_dir=str(tmp_path))
        state = _state(cfg)
        state.init()
        ff = FakeFeature("test")
        features = [("test", ff)]
        cleanup = gamemode_actions._build_cleanup_closure(features, logger, state)
        cleanup()
        cleanup()
        assert ff.disable_calls == [True]

    def test_cleanup_preserve_state(self, tmp_path, logger):
        """Cleanup with preserve_state=True should not clear state."""
        cfg = _cfg(runtime_dir=str(tmp_path))
        state = _state(cfg)
        state.init()
        state.mark_active()
        ff = FakeFeature("test")
        features = [("test", ff)]
        cleanup = gamemode_actions._build_cleanup_closure(
            features,
            logger,
            state,
            preserve_state=True,
        )
        cleanup()
        assert state.is_active


class TestWrapperShellFunctionFallback:
    """A command missing from PATH falls back to $SHELL (see .pi/PLAN.md).

    The smoketest (tests/smoketest_shell_function.py) proves the per-shell
    mechanisms; these tests prove action_wrapper negotiates and applies
    them. Popen is mocked — no real shells are spawned here.
    """

    def _run(self, tmp_path, logger, command, popen):
        """Run action_wrapper with Popen mocked for the child.

        ctypes.util.find_library (from _watch_parent) also Popen's
        /sbin/ldconfig, so delegate those calls to the real Popen.
        """

        real_popen = subprocess.Popen

        def fake_popen(*args, **kwargs):
            if args and os.path.basename(str(args[0][0])) in ("ldconfig", "ld"):
                return real_popen(*args, **kwargs)
            return popen

        popen_caller = MagicMock(side_effect=fake_popen)
        cfg = _cfg(runtime_dir=str(tmp_path))
        state = _state(cfg)
        state.init()
        runner = Runner(logger)
        with (
            mock_collect_features([]),
            patch.object(Runner, "resolve", return_value="/bin/true"),
            patch("subprocess.Popen", popen_caller),
        ):
            retcode = action_wrapper(cfg, runner, logger, command)
        assert retcode == 0
        assert state.mode == ""
        return popen_caller

    def _popen_mock(self):
        """The mock child process (Popen's return value)."""
        popen = MagicMock()
        popen.wait.return_value = 0
        return popen

    def test_fallback_fish(self, tmp_path, logger, monkeypatch, shell_stub):
        """SHELL=fish, fn not on PATH -> spawn `fish -ic <joined>`."""
        shell_stub("fish")
        monkeypatch.setenv("SHELL", "/bin/fish")
        popen = self._popen_mock()
        caller = self._run(tmp_path, logger, ["gm-fake-fn"], popen)
        assert caller.call_args.args[0] == ["/bin/fish", "-ic", "gm-fake-fn"]
        assert caller.call_args.kwargs.get("env") is None

    def test_no_fallback_when_command_found(self, tmp_path, logger, monkeypatch):
        """A PATH command must never be rerouted through a shell."""
        monkeypatch.setenv("SHELL", "/bin/fish")
        popen = self._popen_mock()
        caller = self._run(tmp_path, logger, ["/bin/true"], popen)
        assert caller.call_args.args[0] == ["/bin/true"]

    def test_bash_fallback_env_is_popen_scoped(self, tmp_path, logger, monkeypatch):
        """BASH_ENV goes to the child's env only, never os.environ."""
        rc = tmp_path / ".bashrc"
        rc.write_text("# defs\n")
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("SHELL", "/bin/bash")
        popen = self._popen_mock()
        caller = self._run(tmp_path, logger, ["gm-fake-fn"], popen)
        argv, kwargs = caller.call_args.args, caller.call_args.kwargs
        assert argv[0] == ["/bin/bash", "-c", "gm-fake-fn"]
        assert kwargs["env"]["BASH_ENV"] == str(rc)
        assert "BASH_ENV" not in os.environ

    def test_no_fallback_unsupported_shell(self, tmp_path, logger, monkeypatch, caplog):
        """SHELL=sh: no mechanism exists -> run as-is and warn."""
        monkeypatch.setenv("SHELL", "/bin/sh")
        popen = self._popen_mock()
        caller = self._run(tmp_path, logger, ["gm-fake-fn"], popen)
        assert caller.call_args.args[0] == ["gm-fake-fn"]
        assert any("not found" in r.message for r in caplog.records)


class TestWrapperAudioEnv:
    """Wrapper mode: PULSE_LATENCY_MSEC reaches the child through the Popen
    env channel; os.environ is never mutated (SoC, .pi/PLAN.md S7)."""

    def test_wrapper_child_receives_audio_env_without_mutating(
        self, tmp_path, logger, monkeypatch
    ):
        monkeypatch.delenv("PULSE_LATENCY_MSEC", raising=False)
        cfg = _cfg(runtime_dir=str(tmp_path), enable_audio=True, audio_latency="120")
        state = _state(cfg)
        state.init()
        popen = MagicMock()
        popen.wait.return_value = 0

        real_popen = subprocess.Popen

        def fake_popen(*args, **kwargs):
            if args and os.path.basename(str(args[0][0])) in ("ldconfig", "ld"):
                return real_popen(*args, **kwargs)
            return popen

        popen_caller = MagicMock(side_effect=fake_popen)
        with (
            mock_collect_features([]),
            patch.object(Runner, "resolve", return_value="/bin/true"),
            patch("subprocess.Popen", popen_caller),
        ):
            retcode = action_wrapper(cfg, Runner(logger), logger, ["/bin/true"])
        assert retcode == 0
        env_kwarg = popen_caller.call_args.kwargs.get("env")
        assert env_kwarg is not None
        assert env_kwarg["PULSE_LATENCY_MSEC"] == "120"
        assert "PULSE_LATENCY_MSEC" not in os.environ
