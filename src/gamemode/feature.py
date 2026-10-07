"""Feature protocol, result, and base classes."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from gamemode.config import Config
from gamemode.runner import Runner


@dataclass
class FeatureResult:
    ok: bool = True
    skipped: bool = False
    changed: bool = False
    detail: str = ""

    def __repr__(self) -> str:
        if self.skipped:
            return f"FeatureResult(skipped: {self.detail})"
        if self.changed:
            return f"FeatureResult(changed: {self.detail})"
        if not self.ok:
            return f"FeatureResult(error: {self.detail})"
        return "FeatureResult(noop)"

    @classmethod
    def skip(cls, reason: str = "") -> FeatureResult:
        return cls(ok=True, skipped=True, detail=reason)

    @classmethod
    def did_change(cls, detail: str = "") -> FeatureResult:
        return cls(ok=True, changed=True, detail=detail)

    @classmethod
    def noop(cls) -> FeatureResult:
        return cls(ok=True)

    @classmethod
    def error(cls, detail: str = "") -> FeatureResult:
        return cls(ok=False, detail=detail)


CommandWrapper = Callable[[list[str]], list[str]]
WrapperFactory = Callable[[Config, Runner, logging.Logger], CommandWrapper | None]


class _BaseFeature:
    def __init__(self, config: Config, runner: Runner, log: logging.Logger) -> None:
        self._cfg = config
        self._run = runner
        self._log = log

    # -- abstract hooks --------------------------------------------------

    @property
    def _feature_enabled(self) -> bool:
        """Return the config flag that gates this feature."""
        raise NotImplementedError

    def _do_enable(self) -> FeatureResult:
        """Implement the actual enable logic (no gating)."""
        raise NotImplementedError

    def _do_disable(self) -> FeatureResult:
        """Implement the actual disable logic (no gating)."""
        raise NotImplementedError

    # -- public API ------------------------------------------------------

    def enable(self) -> FeatureResult:
        if not self._feature_enabled:
            return FeatureResult.skip("disabled by config")
        return self._do_enable()

    def disable(self) -> FeatureResult:
        if not self._feature_enabled:
            return FeatureResult.skip("disabled by config")
        return self._do_disable()


def log_feature_result(name: str, result: FeatureResult, log: logging.Logger) -> None:
    if result.skipped:
        log.debug("%s: skipped (%s)", name, result.detail)
    elif result.changed:
        log.info("%s: %s", name, result.detail)
    elif not result.ok:
        log.warning("%s: %s", name, result.detail)
    else:
        log.debug("%s: no change", name)
