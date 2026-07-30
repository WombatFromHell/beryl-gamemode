"""Feature protocol, result, and base classes."""

from __future__ import annotations

import logging
from collections.abc import Callable

from gamemode.config import Config
from gamemode.runner import CheckedCommandRunner, Runner


class FeatureResult:
    __slots__ = ("changed", "detail", "ok", "skipped")

    def __init__(
        self,
        ok: bool = True,
        skipped: bool = False,
        changed: bool = False,
        detail: str = "",
    ) -> None:
        self.ok = ok
        self.skipped = skipped
        self.changed = changed
        self.detail = detail

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

    _feature_name: str = ""
    """Human-readable name for logging; override in subclasses."""

    @property
    def _feature_enabled(self) -> bool:
        """Return the config flag that gates this feature."""
        raise NotImplementedError

    def _do_enable(self, output: str) -> FeatureResult:
        """Implement the actual enable logic (no gating)."""
        raise NotImplementedError

    def _do_disable(self, output: str) -> FeatureResult:
        """Implement the actual disable logic (no gating)."""
        raise NotImplementedError

    # -- public API ------------------------------------------------------

    def enable(self, output: str) -> FeatureResult:
        if not self._feature_enabled:
            return FeatureResult.skip("disabled by config")
        return self._do_enable(output)

    def disable(self, output: str) -> FeatureResult:
        if not self._feature_enabled:
            return FeatureResult.skip("disabled by config")
        return self._do_disable(output)

    def make_checked_cmd(self, cmd: str, feature: str = "") -> CheckedCommandRunner:
        """Create a CheckedCommandRunner via the base runner."""
        return self._run.make_checked_runner(cmd, feature)


def log_feature_result(name: str, result: FeatureResult, log: logging.Logger) -> None:
    if result.skipped:
        log.debug("%s: skipped (%s)", name, result.detail)
    elif result.changed:
        log.info("%s: %s", name, result.detail)
    elif not result.ok:
        log.warning("%s: %s", name, result.detail)
    else:
        log.debug("%s: no change", name)
