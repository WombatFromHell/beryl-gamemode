"""Tests for state management module."""

import os
import subprocess
import time
from typing import Any, cast

import pytest

from gamemode.config import Config
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
        lock_grabber = tmp_path / "lock_grabber.py"
        lock_grabber.write_text(f"""
import sys, os, fcntl, time
lock_file = {str(cfg.lock_file)!r}
ready_file = {str(ready_file)!r}
fd = os.open(lock_file, os.O_CREAT | os.O_WRONLY)
fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
with open(ready_file, "w") as f:
    f.write("ready")
time.sleep(60)
""")
        proc = subprocess.Popen(
            ["python3", str(lock_grabber)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(50):
            if ready_file.exists():
                break
            time.sleep(0.1)
        else:
            proc.kill()
            proc.wait()
            pytest.fail("Lock grabber never grabbed the lock")
        proc.kill()
        proc.wait()
        fd = os.open(str(cfg.lock_file), os.O_CREAT | os.O_WRONLY)
        try:
            acquired = state._try_lock(fd)
        finally:
            state._unlock(fd)
            os.close(fd)
        assert acquired is True


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
