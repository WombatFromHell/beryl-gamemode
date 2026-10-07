"""Tests for actions module: action_on/off/status/wrapper, _watch_parent, lock lifetime."""

import ctypes
import ctypes.util
import json
import os
import signal
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from conftest import (
    ChildProc,
    FakeFeature,
    _cfg,
    _state,
    mock_collect_features,
    spawn_child,
)

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


def _child_stderr(child: ChildProc) -> str:
    """Tail of the wrapper child's stderr, for failure messages.

    Reads the path spawn_child actually wrote (never a reconstructed name:
    guessing it wrong silently yields an empty tail and hides the traceback
    that explains the failure).
    """
    path = child.stderr_path
    return path.read_text()[-2000:] if path.exists() else ""


@contextmanager
def _spawn_signal_wrapper(
    tmp_path: Path, tag: str, slow_startup: bool = False
) -> Iterator[ChildProc]:
    """Spawn a wrapper child running /bin/sleep 60, recording feature disable
    to feature_state_<tag>.json and exiting with action_wrapper's return code.

    Kills the child in teardown so a failed test leaves no stray process
    holding the state lock. With slow_startup, the child writes the ready
    marker before the wrapped child is spawned (simulating slow startup).
    """
    state_file = tmp_path / f"feature_state_{tag}.json"
    ready_file = tmp_path / "signal_ready"
    gamemode_dir = str(Path(__file__).parent.parent / "src")
    guard_patch = (
        'patch.object(actions, "_watch_parent", slow_watch)'
        if slow_startup
        else 'patch.object(actions, "_signal_guard", delayed_ready_guard)'
    )
    child = spawn_child(
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
    # Pin the wrapper features too: leaving them at their defaults wraps
    # /bin/sleep in systemd-run -> systemd-inhibit, which on a host with a
    # systemd session can die before the test can signal the wrapper (see
    # smoketest_wrapper_host_env.py). exec_cmd must be exactly /bin/sleep 60.
    enable_sleep_inhibit=False, enable_systemd_run=False,
    wrapper_features=set(),
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

def slow_watch(log):
    # Simulate slow startup (e.g. find_library Popen'ing ldconfig on Nix).
    # 0.5s only has to outlive the test's signal delivery (<=0.1s poll
    # granularity), not the whole of startup.
    with open(ready_file, "w") as f:
        f.write("ready")
    time.sleep(0.5)

with patch.object(actions, "collect_features", return_value=features):
    with {guard_patch}:
        rc = actions.action_wrapper(cfg, runner, logger, ["/bin/sleep", "60"])
sys.exit(rc)
""",
        script_name=f"wrapper_signal_{tag}.py",
        ready_name="signal_ready",
    )
    try:
        yield child
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()


class TestActionWrapper:
    def test_cleanup_fires_on_normal_child_exit(
        self, tmp_path_cfg, state_manager, logger
    ):
        """When the child exits normally, cleanup must run."""
        feature_a = FakeFeature("a")
        true_runner = Runner(logger)
        with (
            mock_collect_features([("fake_a", feature_a)]),
            patch.object(Runner, "resolve", return_value="/bin/true"),
        ):
            retcode = action_wrapper(tmp_path_cfg, true_runner, logger, ["/bin/true"])
        assert retcode == 0
        assert feature_a.disable_calls == [True]
        assert state_manager.mode == ""

    @pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT])
    def test_cleanup_fires_on_signal(self, tmp_path, tmp_path_cfg, logger, signum):
        """When the wrapper receives SIGTERM/SIGINT, cleanup must run before exit."""
        tag = f"cleanup_{signum.name.lower()}"
        with _spawn_signal_wrapper(tmp_path, tag) as child:
            child.send_signal(signum)
            child.wait(timeout=10)
        state_file = tmp_path / f"feature_state_{tag}.json"
        assert state_file.exists(), (
            f"Child did not write feature state (rc={child.returncode})\n"
            f"stderr:\n{_child_stderr(child)}"
        )
        result = json.loads(state_file.read_text())
        assert result["dis"] == [True], f"Cleanup did not run: {result}"
        assert StateManager(tmp_path_cfg).mode == "", "State not cleared"

    @pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT])
    def test_signal_exit_code_is_child_wait_status(self, tmp_path, logger, signum):
        """After a signal, the exit code is the wrapped child's wait() status
        (-9 from SIGKILL, masked to 247 by sys.exit) — never 128+signum."""
        tag = f"rc_{signum.name.lower()}"
        with _spawn_signal_wrapper(tmp_path, tag) as child:
            child.send_signal(signum)
            child.wait(timeout=10)
        assert child.returncode == (-9) & 0xFF, (
            f"rc={child.returncode} (expected 247): wrapper died to the signal "
            f"or the child was not killed. stderr:\n{_child_stderr(child)}"
        )

    @pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT])
    def test_signal_during_startup_is_not_lost(self, tmp_path, logger, signum):
        """A signal arriving during slow startup (before the wrapped child is
        spawned) must still yield the child's wait status — never -signum."""
        tag = f"startup_{signum.name.lower()}"
        with _spawn_signal_wrapper(tmp_path, tag, slow_startup=True) as child:
            child.send_signal(signum)
            child.wait(timeout=15)
        assert child.returncode == (-9) & 0xFF, (
            f"rc={child.returncode} (expected 247): signal was lost during "
            f"startup. stderr:\n{_child_stderr(child)}"
        )

    def test_concurrent_wrapper_skips(self, tmp_path_cfg, logger, held_lock):
        """A second wrapper instance should skip when the first holds the lock."""
        feature_a = FakeFeature("a")
        with mock_collect_features([("fake_a", feature_a)]):
            retcode = action_wrapper(
                tmp_path_cfg, Runner(logger), logger, ["/bin/true"]
            )
        assert retcode == 0
        assert feature_a.enable_calls == []
        assert feature_a.disable_calls == []

    def test_child_nonzero_exitcode_propagated(self, tmp_path_cfg, logger):
        """The wrapper must return the child's exit code after cleanup."""
        feature = FakeFeature("x")
        with (
            mock_collect_features([("fake", feature)]),
            patch.object(Runner, "resolve", return_value="/bin/false"),
        ):
            retcode = action_wrapper(
                tmp_path_cfg, Runner(logger), logger, ["/bin/false"]
            )
        assert retcode == 1
        assert feature.disable_calls == [True]
        assert StateManager(tmp_path_cfg).mode == ""

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


class _FakeLibc:
    """Minimal libc stand-in: prctl returns a fixed code, strerror is stable."""

    def __init__(self, rc: int):
        self._rc = rc

    def prctl(self, *_args: object) -> int:
        return self._rc

    @staticmethod
    def strerror(err: int) -> str:
        return f"error {err}"


def _fake_libc(rc: int):
    """(find_library, CDLL) patches making _watch_parent see a _FakeLibc."""
    return (
        patch.object(
            ctypes.util,  # pyright: ignore[reportAttributeAccessIssue]
            "find_library",
            return_value="/lib/libc.so",
        ),
        patch.object(ctypes, "CDLL", return_value=_FakeLibc(rc)),
    )


class TestWatchParent:
    """Tests for the parent-death signal mechanism.

    _watch_parent uses prctl(PR_SET_PDEATHSIG, SIGTERM).  We verify the
    Python code path (error handling, CDL loading) rather than the kernel
    guarantee itself, which is tested by the kernel.
    """

    def test_watch_parent_no_libc(self, logger):
        """When find_library returns None, _watch_parent should not raise."""
        with patch.object(
            ctypes.util,  # pyright: ignore[reportAttributeAccessIssue]
            "find_library",
            return_value=None,
        ):
            _watch_parent(logger)  # should not raise

    def test_watch_parent_prctl_fails(self, logger, caplog):
        """When prctl returns non-zero, _watch_parent should log a warning."""
        find_library, cdll = _fake_libc(rc=-1)
        with find_library, cdll:
            _watch_parent(logger)  # should not raise
        assert any("prctl" in r.message for r in caplog.records), (
            f"expected a prctl warning, got: {[r.message for r in caplog.records]}"
        )

    def test_watch_parent_success(self, logger):
        """When prctl succeeds, _watch_parent should not raise."""
        find_library, cdll = _fake_libc(rc=0)
        with find_library, cdll:
            _watch_parent(logger)  # should not raise


class TestStateManagerLockLifetime:
    def test_lock_held_during_child_execution(self, tmp_path_cfg, logger):
        """Integration: while action_wrapper runs a child, the lock must be held.

        Reuses _spawn_signal_wrapper: its child runs /bin/sleep 60, so the lock
        is held for the whole run and no separate probe script is needed.
        """
        state = _state(tmp_path_cfg)
        with _spawn_signal_wrapper(Path(tmp_path_cfg.runtime_dir), "lock"):
            for _ in range(50):
                # Public API, not a hand-rolled flock: locked() reports False
                # when the wrapper child holds it.
                with state.locked() as acquired:
                    if not acquired:
                        break
                time.sleep(0.1)
            else:
                pytest.fail("Lock was not held during child execution")


class TestActionOn:
    """Tests for action_on flow."""

    def test_action_on_calls_features_enable(self, tmp_path_cfg, state_manager, logger):
        """action_on should call features_enable after marking active."""
        ff = FakeFeature("test")
        with mock_collect_features([("test", ff)]):
            ret = action_on(tmp_path_cfg, Runner(logger), logger)
        assert ret == 0
        assert state_manager.is_active
        assert ff.enable_calls == [True]

    def test_action_on_idempotent(self, tmp_path_cfg, state_manager, logger):
        """action_on when already active should return 0 without re-enabling."""
        state_manager.mark_active()
        ff = FakeFeature("test")
        with mock_collect_features([("test", ff)]):
            ret = action_on(tmp_path_cfg, Runner(logger), logger)
        assert ret == 0
        assert ff.enable_calls == []

    def test_action_on_skips_when_wrapper_active(
        self, tmp_path_cfg, state_manager, logger
    ):
        """action_on when wrapper mode is active should skip."""
        state_manager.mark_wrapper(["/bin/test"])
        ff = FakeFeature("test")
        with mock_collect_features([("test", ff)]):
            ret = action_on(tmp_path_cfg, Runner(logger), logger)
        assert ret == 0
        assert ff.enable_calls == []


class TestActionOff:
    """Tests for action_off flow."""

    def test_action_off_calls_features_disable_and_clears(
        self, tmp_path_cfg, state_manager, logger
    ):
        """action_off should call features_disable then clear state."""
        state_manager.mark_active()
        ff = FakeFeature("test")
        with mock_collect_features([("test", ff)]):
            ret = action_off(tmp_path_cfg, Runner(logger), logger)
        assert ret == 0
        assert ff.disable_calls == [True]
        assert state_manager.mode == ""


class TestActionStatus:
    """Tests for action_status."""

    def test_action_status_output(self, tmp_path_cfg, capsys):
        """action_status should print state information."""
        ret = action_status(tmp_path_cfg)
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
        """Run action_wrapper with Popen mocked for the child."""
        popen_caller = MagicMock(return_value=popen)
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
        popen_caller = MagicMock(return_value=popen)
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
