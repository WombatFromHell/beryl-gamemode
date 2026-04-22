"""Tests for actions module: action_on/off/status/wrapper, _watch_parent, lock lifetime."""

import ctypes
import ctypes.util
import json
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

import pytest

from gamemode import actions as gamemode_actions
from gamemode.actions import _watch_parent, action_wrapper
from gamemode.config import Config
from gamemode.feature import Feature, FeatureResult
from gamemode.runner import Runner
from gamemode.state import StateManager


class FakeFeature(Feature):
    """Trivial feature that records enable/disable calls."""

    def __init__(self, name: str):
        self.name = name
        self.enable_calls: list[str] = []
        self.disable_calls: list[str] = []
        self.enable_result: FeatureResult = FeatureResult.did_change(f"{name} enabled")
        self.disable_result: FeatureResult = FeatureResult.did_change(
            f"{name} disabled"
        )

    def enable(self, _output: str) -> FeatureResult:
        self.enable_calls.append(_output)
        return self.enable_result

    def disable(self, _output: str) -> FeatureResult:
        self.disable_calls.append(_output)
        return self.disable_result


class TestActionWrapper:
    def _make_cfg(self, tmp_path):
        return _cfg(
            runtime_dir=str(tmp_path),
            enable_scx=False,
            enable_vrr=False,
            enable_tuned=False,
            enable_inhibit=False,
            enable_audio=False,
            enable_steam=False,
        )

    def test_cleanup_fires_on_normal_child_exit(self, tmp_path, logger):
        """When the child exits normally, cleanup must run."""
        cfg = self._make_cfg(tmp_path)
        state = StateManager(cfg)
        state.init()
        feature_a = FakeFeature("a")
        features = [("fake_a", feature_a)]
        true_runner = Runner(logger)
        with patch.object(gamemode_actions, "collect_features", return_value=features):
            with patch.object(Runner, "resolve", return_value="/bin/true"):
                retcode = action_wrapper(cfg, true_runner, logger, ["/bin/true"])
        assert retcode == 0
        assert feature_a.disable_calls == [cfg.vrr_output_default]
        assert state.mode == ""

    @pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT])
    def test_cleanup_fires_on_signal(self, tmp_path, logger, signum):
        """When the wrapper receives SIGTERM/SIGINT, cleanup must run before exit."""
        cfg = self._make_cfg(tmp_path)
        state_file = tmp_path / f"feature_state_{signum.name.lower()}.json"
        ready_file = tmp_path / "signal_ready"
        gamemode_dir = str(Path(__file__).parent.parent / "src")
        child_script = tmp_path / "wrapper_signal.py"
        child_script.write_text(f"""
import sys, os, time, json, signal
from unittest.mock import patch
from contextlib import contextmanager
sys.path.insert(0, {gamemode_dir!r})
from gamemode.config import Config
from gamemode.state import StateManager
from gamemode.feature import Feature, FeatureResult
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

class RecordFeature(Feature):
    def __init__(self):
        self.en = []
        self.dis = []
    def enable(self, output):
        self.en.append(output)
        return FeatureResult.did_change("en")
    def disable(self, output):
        self.dis.append(output)
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
    with open(ready_file, "w") as f:
        f.write("ready")
    with original_signal_guard(log, child_proc) as pending:
        yield pending

with patch.object(actions, "collect_features", return_value=features):
    with patch.object(actions, "_signal_guard", delayed_ready_guard):
        actions.action_wrapper(cfg, runner, logger, ["/bin/sleep", "60"])
""")
        child = subprocess.Popen(
            ["python3", str(child_script)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(50):
            if ready_file.exists():
                break
            time.sleep(0.1)
        else:
            child.kill()
            child.wait()
            pytest.fail("Wrapper did not reach running state")
        child.send_signal(signum)
        child.wait(timeout=10)
        assert state_file.exists(), (
            f"Child did not write feature state (rc={child.returncode})"
        )
        result = json.loads(state_file.read_text())
        assert result["dis"] == ["DP-1"], f"Cleanup did not run: {result}"
        assert StateManager(cfg).mode == "", "State not cleared"

    def test_concurrent_wrapper_skips(self, tmp_path_cfg, logger, held_lock):
        """A second wrapper instance should skip when the first holds the lock."""
        cfg = _cfg(
            runtime_dir=tmp_path_cfg.runtime_dir,
            enable_scx=False,
            enable_vrr=False,
            enable_tuned=False,
            enable_inhibit=False,
            enable_audio=False,
            enable_steam=False,
        )
        state = StateManager(cfg)
        state.init()
        feature_a = FakeFeature("a")
        features = [("fake_a", feature_a)]
        runner = Runner(logger)
        with patch.object(gamemode_actions, "collect_features", return_value=features):
            retcode = action_wrapper(cfg, runner, logger, ["/bin/true"])
        assert retcode == 0
        assert feature_a.enable_calls == []
        assert feature_a.disable_calls == []

    def test_child_nonzero_exitcode_propagated(self, tmp_path, logger):
        """The wrapper must return the child's exit code after cleanup."""
        cfg = self._make_cfg(tmp_path)
        state = StateManager(cfg)
        state.init()
        features = [("fake", FakeFeature("x"))]
        runner = Runner(logger)
        with patch.object(gamemode_actions, "collect_features", return_value=features):
            with patch.object(Runner, "resolve", return_value="/bin/false"):
                retcode = action_wrapper(cfg, runner, logger, ["/bin/false"])
        assert retcode == 1
        assert features[0][1].disable_calls == [cfg.vrr_output_default]
        assert state.mode == ""

    def test_cleanup_runs_even_on_oserror(self, tmp_path, logger):
        """If exec fails (OSError), cleanup must still run."""
        cfg = self._make_cfg(tmp_path)
        state = StateManager(cfg)
        state.init()
        feature_a = FakeFeature("a")
        features = [("fake_a", feature_a)]
        runner = Runner(logger)
        with patch.object(gamemode_actions, "collect_features", return_value=features):
            with patch.object(Runner, "resolve", return_value="/nonexistent/bin/cmd"):
                retcode = action_wrapper(cfg, runner, logger, ["/nonexistent/bin/cmd"])
        assert retcode == 1
        assert feature_a.disable_calls == [cfg.vrr_output_default]
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

        with patch.object(
            ctypes.util,  # pyright: ignore[reportAttributeAccessIssue]
            "find_library",
            return_value="/lib/libc.so",
        ):
            with patch.object(ctypes, "CDLL", return_value=FakeLibc):
                _watch_parent(logger)  # should not raise

    def test_watch_parent_success(self, tmp_path, logger):
        """When prctl succeeds, _watch_parent should not raise."""
        import ctypes

        class FakeLibc:
            @staticmethod
            def prctl(*args):
                return 0

        with patch.object(
            ctypes.util,  # pyright: ignore[reportAttributeAccessIssue]
            "find_library",
            return_value="/lib/libc.so",
        ):
            with patch.object(ctypes, "CDLL", return_value=FakeLibc):
                _watch_parent(logger)  # should not raise


class TestStateManagerLockLifetime:
    def test_lock_held_during_child_execution(self, tmp_path, logger):
        """Integration: while action_wrapper runs a child, the lock must be held."""
        cfg = _cfg(
            runtime_dir=str(tmp_path),
            enable_scx=False,
            enable_vrr=False,
            enable_tuned=False,
            enable_inhibit=False,
            enable_audio=False,
            enable_steam=False,
        )
        state = StateManager(cfg)
        state.init()
        ready_file = tmp_path / "lock_ready"
        child_script = tmp_path / "lock_lifetime_child.py"
        gamemode_dir = str(Path(__file__).parent.parent / "src")
        child_script.write_text(f"""
import sys, os, time
from unittest.mock import patch
sys.path.insert(0, {gamemode_dir!r})
from gamemode.config import Config
from gamemode.feature import Feature, FeatureResult
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

class RecordFeature(Feature):
    def __init__(self):
        self.en = []
        self.dis = []
    def enable(self, output):
        self.en.append(output)
        with open({str(ready_file)!r}, "w") as f:
            f.write("ready")
        time.sleep(0.1)
        return FeatureResult.did_change("en")
    def disable(self, output):
        self.dis.append(output)
        return FeatureResult.did_change("dis")

feat = RecordFeature()
features = [("fake", feat)]

with patch.object(actions, "collect_features", return_value=features):
    actions.action_wrapper(cfg, Runner(logger), logger, ["/bin/true"])
""")
        child = subprocess.Popen(
            ["python3", str(child_script)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for _ in range(50):
            if ready_file.exists():
                break
            time.sleep(0.1)
        else:
            child.kill()
            child.wait()
            pytest.fail("Child never became ready")
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
