"""SCX scheduler feature."""

from __future__ import annotations

import logging

from gamemode.config import Config
from gamemode.feature import FeatureResult, _BaseFeature
from gamemode.runner import Runner


class SCXScheduler(_BaseFeature):
    _CMD = "scxctl"
    _FEATURE = "SCX scheduler"

    _feature_name = "SCX scheduler"

    def __init__(self, config: Config, runner: Runner, log: logging.Logger) -> None:
        super().__init__(config, runner, log)
        self._scxctl = self.make_checked_cmd(self._CMD, self._FEATURE)

    @property
    def _feature_enabled(self) -> bool:
        return self._cfg.enable_scx

    def _status(self) -> str:
        result = self._scxctl.run_or_none([self._CMD, "get"])
        return result.stdout.strip() if result else ""

    def _apply(self) -> FeatureResult:
        ok = self._scxctl.run_ok(
            [
                self._CMD,
                "start",
                "-s",
                self._cfg.scx_scheduler,
                "-m",
                self._cfg.scx_mode,
            ]
        )
        return (
            FeatureResult.did_change(f"{self._cfg.scx_scheduler}/{self._cfg.scx_mode}")
            if ok
            else FeatureResult.error("scxctl failed")
        )

    def _do_enable(self, output: str) -> FeatureResult:
        if not self._scxctl.is_available:
            return FeatureResult.skip("scxctl not found")
        status = self._status()
        if (
            status
            and self._cfg.scx_scheduler.lower() in status.lower()
            and self._cfg.scx_mode.lower() in status.lower()
        ):
            return FeatureResult.noop()
        return self._apply()

    def _do_disable(self, output: str) -> FeatureResult:
        status = self._status()
        if self._scx_is_stopped(status):
            return FeatureResult.noop()
        if self._scxctl.run_ok([self._CMD, "stop"]):
            return FeatureResult.did_change("stopped")
        return FeatureResult.error("stop failed")

    def _scx_is_stopped(self, status: str) -> bool:
        return not status or "no scx scheduler running" in status
