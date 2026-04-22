"""SCX scheduler feature."""

from __future__ import annotations

import logging

from gamemode.config import Config
from gamemode.feature import FeatureResult, _BaseFeature
from gamemode.runner import Runner


class SCXScheduler(_BaseFeature):
    _CMD = "scxctl"
    _FEATURE = "SCX scheduler"

    def __init__(self, config: Config, runner: Runner, log: logging.Logger) -> None:
        super().__init__(config, runner, log)
        self._scxctl = runner.make_checked_runner(self._CMD, self._FEATURE)

    def _status(self) -> str:
        result = self._scxctl.run_or_none([self._CMD, "get"])
        return result.stdout.strip() if result else ""

    def _apply(self) -> FeatureResult:
        result = self._scxctl.run_or_none(
            [
                self._CMD,
                "start",
                "-s",
                self._cfg.scx_scheduler,
                "-m",
                self._cfg.scx_mode,
            ]
        )
        ok = result is not None and result.returncode == 0
        return (
            FeatureResult.did_change(f"{self._cfg.scx_scheduler}/{self._cfg.scx_mode}")
            if ok
            else FeatureResult.error("scxctl failed")
        )

    def _set_state(self, desired: str) -> FeatureResult:
        return self._guarded(
            self._cfg.enable_scx, "SCX scheduler", lambda: self._set(desired)
        )

    def _set(self, desired: str) -> FeatureResult:
        if desired == "on":
            return self._enable()
        return self._disable()

    def _enable(self) -> FeatureResult:
        if not self._scxctl.is_available:
            return FeatureResult.skip("scxctl not found")
        return self._check_scx_enabled()

    def _check_scx_enabled(self) -> FeatureResult:
        status = self._status()
        if (
            status
            and self._cfg.scx_scheduler.lower() in status.lower()
            and self._cfg.scx_mode.lower() in status.lower()
        ):
            return FeatureResult.noop()
        return self._apply()

    def _disable(self) -> FeatureResult:
        return self._check_scx_disabled()

    def _check_scx_disabled(self) -> FeatureResult:
        status = self._status()
        if self._scx_is_stopped(status):
            return FeatureResult.noop()
        result = self._scxctl.run_or_none([self._CMD, "stop"])
        if result is not None and result.returncode == 0:
            return FeatureResult.did_change("stopped")
        return FeatureResult.error("stop failed")

    def _scx_is_stopped(self, status: str) -> bool:
        return not status or "no scx scheduler running" in status

    def enable(self, _output: str) -> FeatureResult:
        return self._set_state("on")

    def disable(self, _output: str) -> FeatureResult:
        return self._set_state("off")
