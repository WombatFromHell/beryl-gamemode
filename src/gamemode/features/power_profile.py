"""PowerProfile (tuned-adm performance profile) feature."""

from __future__ import annotations

import logging

from gamemode.config import Config
from gamemode.feature import FeatureResult, _BaseFeature
from gamemode.runner import Runner


class PowerProfile(_BaseFeature):
    _CMD = "tuned-adm"
    _FEATURE = "Performance mode"

    def __init__(self, config: Config, runner: Runner, log: logging.Logger) -> None:
        super().__init__(config, runner, log)
        self._tuned = runner.make_checked_runner(self._CMD, self._FEATURE)

    def _current(self) -> str:
        result = self._tuned.run_or_none([self._CMD, "active"])
        if result is None:
            return ""
        for line in result.stdout.splitlines():
            if line.startswith("Active profile:"):
                return line.split(":", 1)[1].strip()
        return ""

    def _set(self, profile: str) -> bool:
        result = self._tuned.run_or_none([self._CMD, "profile", profile])
        return result is not None and result.returncode == 0

    def _set_state(self, desired: str) -> FeatureResult:
        gate = self._gate(self._cfg.enable_tuned, "Performance mode")
        if gate is not None:
            return gate
        return self._check_profile_set(desired)

    def _check_profile_set(self, desired: str) -> FeatureResult:
        current = self._current()
        if current == desired:
            return FeatureResult.noop()
        self._log.info("Profile: %s → %s", current or "none", desired)
        ok = self._set(desired)
        return self._profile_set_result(current, desired, ok)

    def _profile_set_result(
        self, current: str, desired: str, ok: bool
    ) -> FeatureResult:
        if ok:
            return FeatureResult.did_change(f"{current or 'none'} → {desired}")
        self._log.error("failed to set %s", desired)
        return FeatureResult.error(f"failed to set {desired}")

    def enable(self, _output: str) -> FeatureResult:
        return self._set_state(self._cfg.profile_game)

    def disable(self, _output: str) -> FeatureResult:
        return self._set_state(self._cfg.profile_desktop)
