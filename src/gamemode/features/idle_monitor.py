"""Idle monitor: evdev-based KB&M input activity detection.

Polls /dev/input/event* devices via select() and fires configured commands on
idle → active and active → idle transitions. Device classification lives in
``gamemode.input_classifier``.
"""

from __future__ import annotations

import json
import logging
import os
import select
import struct
import threading
import time
from pathlib import Path

from gamemode.config import Config
from gamemode.input_classifier import classify_input
from gamemode.runner import Runner

# ---------------------------------------------------------------------------
# evdev event format & constants
# ---------------------------------------------------------------------------

_INPUT_EVENT_FORMAT = "llHHi"
_INPUT_EVENT_SIZE = struct.calcsize(_INPUT_EVENT_FORMAT)

_EV_KEY = 1
_EV_REL = 2
_EV_ABS = 3

# Relative mouse movements below this threshold are treated as noise.
_REL_NOISE_THRESHOLD = 3

_DMS_SETTINGS_PATH = Path.home() / ".config/DankMaterialShell/settings.json"


class _IdleMonitorThread(threading.Thread):
    """Daemon thread that watches evdev devices for activity.

    Opens all KB&M input devices, polls them with select(), and fires
    ``idle_cmd`` / ``active_cmd`` from *config* on state transitions.
    """

    def __init__(
        self,
        config: Config,
        log: logging.Logger,
        stop_event: threading.Event,
        runner: Runner,
    ) -> None:
        super().__init__(daemon=True, name="idle-monitor")
        self._cfg = config
        self._log = log
        self._stop = stop_event
        self._run = runner

    def run(self) -> None:
        fds = self._setup_devices()
        if not fds:
            self._log.debug("No KB&M devices found, idle monitor idle")
            return
        try:
            self._run_loop(fds)
        finally:
            for fd in fds:
                try:
                    os.close(fd)
                except OSError:
                    pass

    # ------------------------------------------------------------------
    # Device enumeration and setup
    # ------------------------------------------------------------------

    def _setup_devices(self) -> list[int]:
        """Open all KB&M evdev devices for non-blocking reads.

        Returns a list of file descriptors.
        """
        fds: list[int] = []
        perm_errors = 0
        classified_count = 0
        try:
            entries = os.listdir("/dev/input")
        except FileNotFoundError:
            return fds

        for path in sorted(entries):
            full = f"/dev/input/{path}"
            if not path.startswith("event"):
                continue
            if classify_input(full) is None:
                continue
            classified_count += 1
            try:
                fd = os.open(full, os.O_RDONLY | os.O_NONBLOCK)
            except PermissionError:
                perm_errors += 1
                continue
            fds.append(fd)

        if not fds and classified_count > 0 and perm_errors == classified_count:
            self._log.warning(
                "No evdev devices accessible — missing 'input' group membership; "
                "evdev idle monitor disabled"
            )

        # Drain any initial events so we start with a clean slate.
        for fd in fds:
            try:
                while os.read(fd, 4096):
                    pass
            except (BlockingIOError, OSError):
                pass

        return fds

    # ------------------------------------------------------------------
    # Event filtering
    # ------------------------------------------------------------------

    @staticmethod
    def _meaningful_activity(data: bytes) -> bool:
        """Return True if *data* contains a meaningful input event.

        Key presses, absolute-position events, and relative moves above
        ``_REL_NOISE_THRESHOLD`` count as meaningful.  Key releases,
        SYN reports, and tiny relative jitter do not.
        """
        for i in range(0, len(data), _INPUT_EVENT_SIZE):
            chunk = data[i : i + _INPUT_EVENT_SIZE]
            if len(chunk) != _INPUT_EVENT_SIZE:
                break
            _sec, _usec, ev_type, _code, value = struct.unpack(
                _INPUT_EVENT_FORMAT, chunk
            )
            if ev_type == _EV_KEY and value > 0:
                return True
            if ev_type == _EV_REL and abs(value) > _REL_NOISE_THRESHOLD:
                return True
            if ev_type == _EV_ABS:
                return True
        return False

    # ------------------------------------------------------------------
    # Timeout: DMS settings fallback
    # ------------------------------------------------------------------

    @staticmethod
    def _on_ac() -> bool:
        """Return True if the system is currently on AC power."""
        for p in Path("/sys/class/power_supply").glob("A*"):
            online = p / "online"
            if online.exists() and online.read_text().strip() == "1":
                return True
        return False

    def _get_timeout(self) -> int:
        """Return the idle timeout in seconds (0 = never idle).

        Prefers the explicit config value; falls back to DMS lock timeout
        settings if available.
        """
        if self._cfg.idle_timeout > 0:
            return self._cfg.idle_timeout
        try:
            raw = _DMS_SETTINGS_PATH.read_text()
            settings = json.loads(raw)
        except (OSError, json.JSONDecodeError):
            return 0
        key = "acLockTimeout" if self._on_ac() else "batteryLockTimeout"
        return int(settings.get(key, 0))

    # ------------------------------------------------------------------
    # Command execution
    # ------------------------------------------------------------------

    def _fire(self, cmd: str) -> None:
        """Run *cmd* in a subprocess (fire-and-forget)."""
        if cmd:
            self._run.spawn(cmd, shell=True)

    # ------------------------------------------------------------------
    # Main polling loop
    # ------------------------------------------------------------------

    def _run_loop(self, fds: list[int]) -> None:
        """Poll evdev FDs for activity and track idle/active transitions."""
        poll_interval = max(self._cfg.idle_poll_interval, 1)
        timeout_secs = self._get_timeout()
        last_activity = time.monotonic()
        was_idle = False

        while not self._stop.is_set():
            timeout = (
                min(poll_interval, timeout_secs) if timeout_secs else poll_interval
            )
            r, _, _ = select.select(fds, [], [], timeout)

            if r:
                for fd in r:
                    try:
                        data = os.read(fd, 4096)
                        if self._meaningful_activity(data):
                            last_activity = time.monotonic()
                            if was_idle:
                                was_idle = False
                                self._fire(self._cfg.active_cmd)
                    except OSError:
                        pass

            if (
                timeout_secs
                and time.monotonic() - last_activity >= timeout_secs
                and not was_idle
            ):
                was_idle = True
                self._fire(self._cfg.idle_cmd)
