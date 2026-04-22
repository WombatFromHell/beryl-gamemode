"""Screen inhibit feature (DMS + DBus ScreenSaver)."""

from __future__ import annotations

import logging

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

    def __init__(self, config: Config, runner: Runner, log: logging.Logger) -> None:
        super().__init__(config, runner, log)
        self._dms = runner.make_checked_runner(self._DMS_CMD, self._DMS_FEATURE)
        self._dbus_send: str | None = runner.resolve("dbus-send")
        self._screensaver_cookie: int | None = None

    def _dms_inhibit_enabled(self) -> bool:
        result = self._dms.run_or_none(
            [self._DMS_CMD, "ipc", "call", "inhibit", "status"]
        )
        return result is not None and "Idle inhibit is disabled" not in result.stdout

    def _dms_run(self, cmd: list[str], desc: str) -> bool:
        r = self._dms.run_or_none([self._DMS_CMD, "ipc", "call", "inhibit", *cmd])
        if r is None or r.returncode != 0:
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
        if cookie_str.startswith("uint32 "):
            cookie_str = cookie_str[7:]
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

    def _set_state(self, desired: str) -> FeatureResult:
        return self._guarded(
            self._cfg.enable_inhibit, "Screen inhibit", lambda: self._set(desired)
        )

    def _set(self, desired: str) -> FeatureResult:
        if desired == "on":
            return self._enable_inhibition()
        return self._disable_inhibition()

    def _enable_inhibition(self) -> FeatureResult:
        results: list[str] = []
        self._try_dms_inhibit(results)
        self._try_screensaver_inhibit(results)
        if not results:
            return FeatureResult.error("all inhibit mechanisms failed")
        return FeatureResult.did_change("; ".join(results))

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

    def _disable_inhibition(self) -> FeatureResult:
        results: list[str] = []
        if compositor_is_niri():
            self._dms_inhibit_disable()
            results.append("DMS inhibit disabled")
        self._screensaver_inhibit_disable()
        results.append("ScreenSaver cookie released")
        return FeatureResult(changed=True, detail="; ".join(results))

    def enable(self, _output: str) -> FeatureResult:
        return self._set_state("on")

    def disable(self, _output: str) -> FeatureResult:
        return self._set_state("off")
