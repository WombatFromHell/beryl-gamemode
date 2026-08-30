"""State management with fcntl-based locking."""

from __future__ import annotations

import fcntl
import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from gamemode.config import Config


class StateManager:
    def __init__(self, config: Config) -> None:
        self._config = config

    def init(self) -> None:
        self._config.state_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _try_lock(fd: int) -> bool:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except (BlockingIOError, OSError):
            return False

    @staticmethod
    def _unlock(fd: int) -> None:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass

    @staticmethod
    def _close_lock_fd(fd: int, _lock_path: Path) -> None:
        try:
            os.close(fd)
        except OSError:
            pass

    @contextmanager
    def locked(self):
        lock_path = self._config.lock_file
        fd = os.open(str(lock_path), os.O_CREAT | os.O_WRONLY)
        acquired = self._try_lock(fd)
        try:
            yield acquired
        finally:
            self._unlock(fd)
            self._close_lock_fd(fd, lock_path)

    def is_lock_held(self) -> bool:
        lock_path = self._config.lock_file
        fd = os.open(str(lock_path), os.O_CREAT | os.O_WRONLY)
        try:
            return not self._try_lock(fd)
        finally:
            self._unlock(fd)
            self._close_lock_fd(fd, lock_path)

    def _read_state(self) -> dict[str, Any]:
        try:
            return json.loads(self._config.state_file.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _write_state(self, data: dict[str, Any]) -> None:
        self._config.state_file.write_text(json.dumps(data))

    @property
    def mode(self) -> str:
        return self._read_state().get("mode", "")

    @property
    def is_wrapper(self) -> bool:
        return self.mode == "wrapper"

    @property
    def is_active(self) -> bool:
        return self.mode == "active"

    def mark_wrapper(self, cmd: list[str] | None = None) -> None:
        data: dict[str, Any] = {"mode": "wrapper", "pid": os.getpid()}
        if cmd:
            data["cmd"] = cmd
        self._write_state(data)

    def mark_active(self) -> None:
        self._write_state({"mode": "active"})

    def clear(self) -> None:
        try:
            self._config.state_file.unlink()
        except FileNotFoundError:
            pass
        try:
            self._config.lock_file.unlink()
        except FileNotFoundError:
            pass

    def pid(self) -> int | None:
        return self._read_state().get("pid")

    def cmd(self) -> list[str] | None:
        return self._read_state().get("cmd")

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    def pid_alive(self) -> bool:
        p = self.pid()
        return p is not None and self._pid_alive(p)
