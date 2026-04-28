"""Tests for state management module."""

import os

from conftest import _cfg, spawn_child

from gamemode.state import StateManager


class TestStateManager:
    def test_init_creates_dir(self, tmp_path_cfg):
        sm = StateManager(tmp_path_cfg)
        sm.init()
        assert tmp_path_cfg.state_dir.is_dir()

    def test_mark_and_read_wrapper(self, state_manager):
        state_manager.mark_wrapper()
        assert state_manager.mode == "wrapper"
        assert state_manager.is_wrapper is True
        assert state_manager.is_active is False

    def test_mark_and_read_active(self, state_manager):
        state_manager.mark_active()
        assert state_manager.mode == "active"
        assert state_manager.is_active is True
        assert state_manager.is_wrapper is False

    def test_clear(self, state_manager):
        state_manager.mark_active()
        state_manager.clear()
        assert state_manager.mode == ""

    def test_lock_serialisation(self, state_manager):
        with state_manager.locked() as acquired:
            assert acquired is True

    def test_is_lock_held_when_free(self, state_manager):
        assert state_manager.is_lock_held() is False

    def test_is_lock_held_when_held(self, state_manager, held_lock):
        assert state_manager.is_lock_held() is True

    def test_lock_contention_returns_false(self, state_manager, held_lock):
        with state_manager.locked() as acquired:
            assert acquired is False

    def test_value_empty_when_missing(self, state_manager):
        assert state_manager.mode == ""

    def test_lock_held_throughout_with_block(self, state_manager):
        """Verify flock spans the entire with block (regression test)."""
        probe_acquired_inside = []
        probe_acquired_after = []
        with state_manager.locked():
            fd = os.open(str(state_manager._config.lock_file), os.O_CREAT | os.O_WRONLY)
            try:
                probe_acquired_inside.append(state_manager._try_lock(fd))
            finally:
                state_manager._unlock(fd)
                os.close(fd)
        fd = os.open(str(state_manager._config.lock_file), os.O_CREAT | os.O_WRONLY)
        try:
            probe_acquired_after.append(state_manager._try_lock(fd))
        finally:
            state_manager._unlock(fd)
            os.close(fd)
        assert probe_acquired_inside[0] is False
        assert probe_acquired_after[0] is True

    def test_lock_released_on_process_death(self, tmp_path):
        """If a process dies while holding the lock, the kernel releases it."""
        cfg = _cfg(runtime_dir=str(tmp_path))
        state = StateManager(cfg)
        state.init()
        ready_file = tmp_path / "lock_grabber_ready"
        proc = spawn_child(
            tmp_path,
            f"""
import sys, os, fcntl, time
lock_file = {str(cfg.lock_file)!r}
ready_file = {str(ready_file)!r}
fd = os.open(lock_file, os.O_CREAT | os.O_WRONLY)
fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
with open(ready_file, "w") as f:
    f.write("ready")
time.sleep(60)
""",
            script_name="lock_grabber.py",
            ready_name="lock_grabber_ready",
        )
        proc.kill()
        proc.wait()
        fd = os.open(str(cfg.lock_file), os.O_CREAT | os.O_WRONLY)
        try:
            acquired = state._try_lock(fd)
        finally:
            state._unlock(fd)
            os.close(fd)
        assert acquired is True

    def test_pid_alive_real_pid(self):
        """pid_alive should return True for a living PID."""
        import os

        assert StateManager._pid_alive(os.getpid()) is True

    def test_cmd_with_data(self, state_manager):
        """cmd() should return the stored command when present."""
        state_manager.mark_wrapper(["/bin/test", "--arg"])
        assert state_manager.cmd() == ["/bin/test", "--arg"]

    def test_clear_glob_cleanup(self, tmp_path_cfg):
        """clear() should remove lock_* files in the state directory."""
        sm = StateManager(tmp_path_cfg)
        sm.init()
        sm.mark_wrapper()
        # Create a lock_* file
        lock_file = tmp_path_cfg.state_dir / "lock_test"
        lock_file.write_text("test")
        sm.clear()
        assert not lock_file.exists()
