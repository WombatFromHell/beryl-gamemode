"""Screen inhibit feature (DMS + DBus ScreenSaver + evdev idle monitor)."""

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

from gamemode.compositor import compositor_is_niri
from gamemode.config import Config
from gamemode.feature import FeatureResult, _BaseFeature
from gamemode.runner import Runner

_INPUT_EVENT_FORMAT = "llHHi"
_INPUT_EVENT_SIZE = struct.calcsize(_INPUT_EVENT_FORMAT)

_EV_KEY = 1
_EV_REL = 2
_EV_ABS = 3
_EV_FF = 21
_REL_X = 0
_BTN_MOUSE = 0x110
_BTN_TOUCH = 0x14A

_REL_NOISE_THRESHOLD = 3

_DMS_SETTINGS_PATH = Path.home() / ".config/DankMaterialShell/settings.json"


class _IdleMonitorThread(threading.Thread):
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

    # -- device classification (shared helpers) --------------------------

    @staticmethod
    def _read_bitmap(path: str) -> list[int] | None:
        try:
            with open(path) as f:
                return [int(x, 16) for x in f.read().strip().split()]
        except (FileNotFoundError, OSError, ValueError):
            return None

    @staticmethod
    def _has_bit(words: list[int], bit: int) -> bool:
        idx = bit // 64
        offset = bit % 64
        return idx < len(words) and bool(words[idx] & (1 << offset))

    @classmethod
    def _classify_via_udevadm(cls, event_path: str) -> str | None:
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
        devname = os.path.basename(event_path)
        base = f"/sys/class/input/{devname}/device/capabilities"

        ev = cls._read_bitmap(f"{base}/ev")
        if not ev:
            return None

        has_key = cls._has_bit(ev, _EV_KEY)
        has_rel = cls._has_bit(ev, _EV_REL)
        has_abs = cls._has_bit(ev, _EV_ABS)
        has_ff = cls._has_bit(ev, _EV_FF)

        if has_ff:
            return None

        rel = cls._read_bitmap(f"{base}/rel")
        if rel and cls._has_bit(rel, _REL_X):
            return "kbm"

        key = cls._read_bitmap(f"{base}/key")
        if key and cls._has_bit(key, _BTN_MOUSE):
            return "kbm"

        if has_key and not has_rel and not has_abs:
            return "kbm"

        if has_abs and has_key and key and cls._has_bit(key, _BTN_TOUCH):
            return "kbm"

        return None

    @classmethod
    def _is_kbm_device(cls, event_path: str) -> bool:
        cls_ = cls._classify_via_udevadm(event_path)
        if cls_ is None:
            cls_ = cls._classify_via_sysfs(event_path)
        return cls_ is not None

    # -- device setup ----------------------------------------------------

    def _setup_devices(self) -> list[int]:
        fds: list[int] = []
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
            try:
                fd = os.open(full, os.O_RDONLY | os.O_NONBLOCK)
            except PermissionError:
                continue
            fds.append(fd)

        for fd in fds:
            try:
                while os.read(fd, 4096):
                    pass
            except (BlockingIOError, OSError):
                pass

        return fds

    # -- event filtering ------------------------------------------------

    @staticmethod
    def _meaningful_activity(data: bytes) -> bool:
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

    # -- timeout from DMS settings --------------------------------------

    @staticmethod
    def _on_ac() -> bool:
        for p in Path("/sys/class/power_supply").glob("A*"):
            online = p / "online"
            if online.exists() and online.read_text().strip() == "1":
                return True
        return False

    def _get_timeout(self) -> int:
        if self._cfg.idle_timeout > 0:
            return self._cfg.idle_timeout
        try:
            raw = _DMS_SETTINGS_PATH.read_text()
            settings = json.loads(raw)
        except (OSError, json.JSONDecodeError):
            return 0
        key = "acLockTimeout" if self._on_ac() else "batteryLockTimeout"
        return int(settings.get(key, 0))

    # -- command execution -----------------------------------------------

    @staticmethod
    def _fire(cmd: str) -> None:
        if cmd:
            subprocess.Popen(cmd, shell=True)

    # -- main loop ------------------------------------------------------

    def _run_loop(self, fds: list[int]) -> None:
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


class ScreenInhibit(_BaseFeature):
    _DMS_CMD = "dms"
    _DMS_FEATURE = "DMS inhibit"
    _DBUS_SERVICE = "org.freedesktop.ScreenSaver"
    _DBUS_PATH = "/ScreenSaver"
    _DBUS_IFACE = "org.freedesktop.ScreenSaver"

    _feature_name = "Screen inhibit"

    def __init__(self, config: Config, runner: Runner, log: logging.Logger) -> None:
        super().__init__(config, runner, log)
        self._dms = self.make_checked_cmd(self._DMS_CMD, self._DMS_FEATURE)
        self._dbus_send: str | None = runner.resolve("dbus-send")
        self._screensaver_cookie: int | None = None
        self._idle_thread: _IdleMonitorThread | None = None
        self._idle_stop = threading.Event()

    @property
    def _feature_enabled(self) -> bool:
        return self._cfg.enable_inhibit

    def _dms_inhibit_enabled(self) -> bool:
        result = self._dms.run_or_none(
            [self._DMS_CMD, "ipc", "call", "inhibit", "status"]
        )
        return result is not None and "Idle inhibit is disabled" not in result.stdout

    def _dms_run(self, cmd: list[str], desc: str) -> bool:
        ok = self._dms.run_ok([self._DMS_CMD, "ipc", "call", "inhibit", *cmd])
        if not ok:
            self._log.error("Failed to %s", desc)
            return False
        return True

    def _dms_inhibit_enable(self, reason: str = "gamemode.py gaming session") -> bool:
        if not self._dms.is_available:
            return False
        if self._dms_inhibit_enabled():
            return True
        if not self._dms_run(["enable"], "enable DMS inhibit"):
            return False
        return self._dms_run(["reason", reason], "set DMS inhibit reason")

    def _dms_inhibit_disable(self) -> None:
        if not self._dms.is_available or not self._dms_inhibit_enabled():
            return
        self._dms.run_or_none([self._DMS_CMD, "ipc", "call", "inhibit", "disable"])

    def _screensaver_inhibit_enable(
        self, reason: str = "gamemode.py gaming session"
    ) -> bool:
        if self._screensaver_cookie is not None:
            return True
        if self._dbus_send is None:
            return False
        result = self._run.capture(
            [
                self._dbus_send,
                "--session",
                f"--dest={self._DBUS_SERVICE}",
                "--type=method_call",
                "--print-reply=literal",
                self._DBUS_PATH,
                f"{self._DBUS_IFACE}.Inhibit",
                "string:gamemode.py",
                f"string:{reason}",
            ]
        )
        if result.returncode != 0:
            self._log.warning("ScreenSaver.Inhibit failed: %s", result.stderr.strip())
            return False
        return self._parse_screensaver_cookie(result.stdout.strip())

    def _parse_screensaver_cookie(self, cookie_str: str) -> bool:
        cookie_str = cookie_str.removeprefix("uint32 ")
        try:
            self._screensaver_cookie = int(cookie_str)
            return True
        except ValueError:
            self._log.warning("Unexpected cookie value: %r", cookie_str)
            return False

    def _screensaver_inhibit_disable(self) -> None:
        if self._screensaver_cookie is None:
            return
        if self._dbus_send is None:
            return
        result = self._run.capture(
            [
                self._dbus_send,
                "--session",
                f"--dest={self._DBUS_SERVICE}",
                "--type=method_call",
                "--print-reply=literal",
                self._DBUS_PATH,
                f"{self._DBUS_IFACE}.UnInhibit",
                f"uint32:{self._screensaver_cookie}",
            ]
        )
        self._handle_screensaver_release(result)
        self._screensaver_cookie = None

    def _handle_screensaver_release(self, result) -> None:
        if result.returncode != 0:
            err = result.stderr.strip()
            if "invalid cookie" in err.lower():
                self._log.debug("ScreenSaver cookie invalid (already released)")
            else:
                self._log.warning("ScreenSaver.UnInhibit failed: %s", err)
        else:
            self._log.debug("ScreenSaver cookie released: %d", self._screensaver_cookie)

    def _start_idle_monitor(self) -> str | None:
        if not self._cfg.enable_idle_monitor:
            return None
        if self._idle_thread is not None:
            return "idle monitor already running"
        self._idle_stop.clear()
        self._idle_thread = _IdleMonitorThread(self._cfg, self._log, self._idle_stop)
        self._idle_thread.start()
        self._log.debug("Idle monitor thread started")
        return "idle monitor started"

    def _stop_idle_monitor(self) -> str | None:
        if self._idle_thread is None:
            return None
        self._idle_stop.set()
        self._idle_thread.join(timeout=2)
        self._idle_thread = None
        self._log.debug("Idle monitor thread stopped")
        return "idle monitor stopped"

    def _do_enable(self, output: str) -> FeatureResult:
        results: list[str] = []
        self._try_dms_inhibit(results)
        self._try_screensaver_inhibit(results)
        idle_msg = self._start_idle_monitor()
        if idle_msg:
            results.append(idle_msg)
        if not results:
            return FeatureResult.error("all inhibit mechanisms failed")
        return FeatureResult.did_change("; ".join(results))

    def _do_disable(self, output: str) -> FeatureResult:
        results: list[str] = []
        idle_msg = self._stop_idle_monitor()
        if idle_msg:
            results.append(idle_msg)
        if compositor_is_niri():
            self._dms_inhibit_disable()
            results.append("DMS inhibit disabled")
        self._screensaver_inhibit_disable()
        results.append("ScreenSaver cookie released")
        return FeatureResult(changed=True, detail="; ".join(results))

    def _try_dms_inhibit(self, results: list[str]) -> None:
        if not compositor_is_niri():
            return
        if self._dms_inhibit_enable():
            results.append("DMS inhibit enabled")
        else:
            self._log.warning("DMS inhibit failed, falling back to DBus")

    def _try_screensaver_inhibit(self, results: list[str]) -> None:
        if self._screensaver_inhibit_enable():
            results.append("ScreenSaver cookie acquired")
