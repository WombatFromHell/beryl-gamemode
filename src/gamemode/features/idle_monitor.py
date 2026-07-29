"""Idle monitor: evdev-based KB&M input activity detection.

Polls /dev/input/event* devices via select(), classifies devices as keyboard/
mouse/touchpad/touchscreen (KB&M) or not, and fires configured commands on idle
→ active and active → idle transitions.
"""

from __future__ import annotations

import json
import logging
import os
import select
import struct
import subprocess
import threading
import time
from pathlib import Path

from gamemode.config import Config

# ---------------------------------------------------------------------------
# evdev event format & constants
# ---------------------------------------------------------------------------

_INPUT_EVENT_FORMAT = "llHHi"
_INPUT_EVENT_SIZE = struct.calcsize(_INPUT_EVENT_FORMAT)

_EV_KEY = 1
_EV_REL = 2
_EV_ABS = 3
_EV_LED = 17
_EV_FF = 21
_REL_X = 0
_BTN_MOUSE = 0x110
_BTN_TOUCH = 0x14A
_BTN_TOOL_FINGER = 0x145
_BTN_STYLUS = 0x14B
_BTN_TOOL_PEN = 0x140

_INPUT_PROP_POINTER = 0x00
_INPUT_PROP_DIRECT = 0x01
_INPUT_PROP_ACCELEROMETER = 0x06

_STEAM_VID = 0x28DE

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
    ) -> None:
        super().__init__(daemon=True, name="idle-monitor")
        self._cfg = config
        self._log = log
        self._stop = stop_event

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
    # Device classification helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _read_bitmap(path: str) -> list[int] | None:
        """Read a sysfs capability bitmap (space-separated hex words)."""
        try:
            with open(path) as f:
                return [int(x, 16) for x in f.read().strip().split()]
        except (FileNotFoundError, OSError, ValueError):
            return None

    @staticmethod
    def _has_bit(words: list[int], bit: int) -> bool:
        """Test whether *bit* is set in a bitmap stored as uint64[]."""
        idx = bit // 64
        offset = bit % 64
        return idx < len(words) and bool(words[idx] & (1 << offset))

    @classmethod
    def _classify_via_udevadm(cls, event_path: str) -> str | None:
        """Ask udevadm whether the device is a keyboard, mouse, or touchpad.

        Returns ``"kbm"`` or ``None``.
        """
        try:
            result = subprocess.run(
                ["udevadm", "info", "-q", "property", "-n", event_path],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return None
        if result.returncode != 0:
            return None
        props = {}
        for line in result.stdout.splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                props[k] = v
        if props.get("ID_INPUT_KEYBOARD") == "1":
            return "kbm"
        if props.get("ID_INPUT_MOUSE") == "1":
            return "kbm"
        if props.get("ID_INPUT_TOUCHPAD") == "1":
            return "kbm"
        return None

    @classmethod
    def _classify_via_sysfs(cls, event_path: str) -> str | None:
        """Fallback classification by reading sysfs capability bitmaps.

        Used when udevadm is unavailable.
        """
        devname = os.path.basename(event_path)
        base = f"/sys/class/input/{devname}/device/capabilities"
        prop_path = f"/sys/class/input/{devname}/device/properties"

        ev = cls._read_bitmap(f"{base}/ev")
        if not ev:
            return None

        key = cls._read_bitmap(f"{base}/key")
        rel = cls._read_bitmap(f"{base}/rel")
        prop = cls._read_bitmap(prop_path)

        has_key = cls._has_bit(ev, _EV_KEY)
        has_rel = cls._has_bit(ev, _EV_REL)
        has_abs = cls._has_bit(ev, _EV_ABS)
        has_ff = cls._has_bit(ev, _EV_FF)

        # Exclude force-feedback devices (e.g. joysticks with rumble).
        if has_ff:
            return None

        # Exclude accelerometers.
        if prop and cls._has_bit(prop, _INPUT_PROP_ACCELEROMETER):
            return None

        # Exclude pen tablets / stylus digitizers.
        if key and (cls._has_bit(key, _BTN_STYLUS) or cls._has_bit(key, _BTN_TOOL_PEN)):
            return None

        # Touchscreens that do NOT have BTN_TOOL_FINGER are KB&M (direct input).
        if (
            has_abs
            and key
            and cls._has_bit(key, _BTN_TOUCH)
            and not cls._has_bit(key, _BTN_TOOL_FINGER)
        ):
            return "kbm"
        # Direct-input touch devices without multitouch.
        if (
            has_abs
            and prop
            and cls._has_bit(prop, _INPUT_PROP_DIRECT)
            and not (key and cls._has_bit(key, _BTN_TOOL_FINGER))
        ):
            return "kbm"

        # Multitouch touchpads.
        if has_abs and key and cls._has_bit(key, _BTN_TOOL_FINGER):
            return "kbm"
        # Pointer devices (mice, trackballs) that are direct-input touch.
        if (
            has_abs
            and prop
            and cls._has_bit(prop, _INPUT_PROP_POINTER)
            and not (prop and cls._has_bit(prop, _INPUT_PROP_DIRECT))
        ):
            return "kbm"

        # Relative-axis devices with X axis → mice.
        if has_rel and rel and cls._has_bit(rel, _REL_X):
            return "kbm"
        # Devices with mouse buttons.
        if has_rel and key and cls._has_bit(key, _BTN_MOUSE):
            return "kbm"

        # Keyboards without relative/absolute axes (have EV_LED for caps/num lock).
        if has_key and not has_rel and not has_abs and cls._has_bit(ev, _EV_LED):
            return "kbm"

        return None

    @classmethod
    def _is_steam_controller(cls, event_path: str) -> bool:
        devname = os.path.basename(event_path)
        try:
            with open(f"/sys/class/input/{devname}/device/id/vendor") as f:
                vendor = int(f.read().strip(), 16)
            return vendor == _STEAM_VID
        except (FileNotFoundError, OSError, ValueError):
            return False

    @classmethod
    def _is_kbm_device(cls, event_path: str) -> bool:
        """Return True if *event_path* is a keyboard, mouse, touchpad, or touchscreen."""
        if cls._is_steam_controller(event_path):
            return False
        cls_ = cls._classify_via_udevadm(event_path)
        if cls_ is None:
            cls_ = cls._classify_via_sysfs(event_path)
        return cls_ is not None

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
            if not self._is_kbm_device(full):
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

    @staticmethod
    def _fire(cmd: str) -> None:
        """Run *cmd* in a subprocess (fire-and-forget)."""
        if cmd:
            subprocess.Popen(cmd, shell=True)

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
