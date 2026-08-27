"""Screen inhibit feature (DMS + DBus ScreenSaver + evdev idle monitor)."""

from __future__ import annotations

import logging
import os
import threading

from gamemode.compositor import compositor_is_niri
from gamemode.config import Config
from gamemode.feature import FeatureResult, _BaseFeature
from gamemode.runner import Runner


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
        self._idle_thread: threading.Thread | None = None
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

    def _warn_missing_idle_pair(self, partial_msg: str) -> None:
        if self._cfg.idle_cmd and self._cfg.active_cmd:
            return
        missing = "ACTIVE_CMD" if self._cfg.idle_cmd else "IDLE_CMD"
        if os.environ.get("ENABLE_IDLE_MONITOR") is not None:
            self._log.warning("%s is missing — %s", missing, partial_msg)
        else:
            self._log.warning(
                "%s is missing — set ENABLE_IDLE_MONITOR=1 to enable idle monitor "
                "with partial pair",
                missing,
            )

    def _start_idle_monitor(self) -> str | None:
        if not self._cfg.enable_idle_monitor:
            return None
        if not self._cfg.idle_cmd or not self._cfg.active_cmd:
            self._warn_missing_idle_pair("idle monitor started with partial pair")
            if os.environ.get("ENABLE_IDLE_MONITOR") is None:
                return None
        if self._idle_thread is not None:
            return "idle monitor already running"
        self._idle_stop.clear()
        from gamemode.features.idle_monitor import _IdleMonitorThread

        self._idle_thread = _IdleMonitorThread(self._cfg, self._log, self._idle_stop)
        self._idle_thread.start()
        self._log.debug("Idle monitor thread started")
        return "idle monitor started"

    def _stop_idle_monitor(self) -> str | None:
        if self._idle_thread is None:
            if self._cfg.enable_idle_monitor and (
                not self._cfg.idle_cmd or not self._cfg.active_cmd
            ):
                self._warn_missing_idle_pair(
                    "idle state may not be restored on cleanup"
                )
            return None
        self._idle_stop.set()
        self._idle_thread.join(timeout=2)
        self._idle_thread = None
        self._log.debug("Idle monitor thread stopped")
        if self._cfg.enable_idle_monitor and self._cfg.active_cmd:
            from gamemode.features.idle_monitor import _IdleMonitorThread

            _IdleMonitorThread._fire(self._cfg.active_cmd)
        if self._cfg.enable_idle_monitor and (
            not self._cfg.idle_cmd or not self._cfg.active_cmd
        ):
            self._warn_missing_idle_pair("idle state may not be restored on cleanup")
        return "idle monitor stopped"

    def _do_enable(self) -> FeatureResult:
        results: list[str] = []
        self._try_dms_inhibit(results)
        self._try_screensaver_inhibit(results)
        idle_msg = self._start_idle_monitor()
        if idle_msg:
            results.append(idle_msg)
        if not results:
            return FeatureResult.error("all inhibit mechanisms failed")
        return FeatureResult.did_change("; ".join(results))

    def _do_disable(self) -> FeatureResult:
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
