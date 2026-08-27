"""PowerProfile (tuned-adm performance profile) feature."""

from __future__ import annotations

import logging

from gamemode.config import Config
from gamemode.feature import FeatureResult, _BaseFeature
from gamemode.runner import Runner


class PowerProfile(_BaseFeature):
    _feature_name = "Performance mode"

    def __init__(self, config: Config, runner: Runner, log: logging.Logger) -> None:
        super().__init__(config, runner, log)
        self._tuned = self.make_checked_cmd("tuned-adm", "Performance mode")

    @property
    def _feature_enabled(self) -> bool:
        return self._cfg.enable_tuned

    def _current(self) -> str:
        result = self._tuned.run_or_none(["tuned-adm", "active"])
        if result is None:
            return ""
        for line in result.stdout.splitlines():
            if line.startswith("Active profile:"):
                return line.split(":", 1)[1].strip()
        return ""

    def _profile_set(self, desired: str) -> FeatureResult:
        current = self._current()
        if current == desired:
            return FeatureResult.noop()
        self._log.info("Profile: %s → %s", current or "none", desired)
        ok = self._tuned.run_ok(["tuned-adm", "profile", desired])
        if ok:
            return FeatureResult.did_change(f"{current or 'none'} → {desired}")
        self._log.error("failed to set %s", desired)
        return FeatureResult.error(f"failed to set {desired}")

    def _do_enable(self) -> FeatureResult:
        return self._profile_set(self._cfg.profile_game)

    def _do_disable(self) -> FeatureResult:
        return self._profile_set(self._cfg.profile_desktop)
