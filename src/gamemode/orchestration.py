"""Feature orchestration."""

from __future__ import annotations

import logging
from typing import cast

from gamemode.config import Config
from gamemode.feature import Feature, _BaseFeature
from gamemode.features.audio_priority import AudioPriority
from gamemode.features.power_profile import PowerProfile
from gamemode.features.screen_inhibit import ScreenInhibit
from gamemode.features.scx_scheduler import SCXScheduler
from gamemode.features.vrr import VRR
from gamemode.runner import Runner


def collect_features(
    config: Config, runner: Runner, log: logging.Logger
) -> list[tuple[str, Feature]]:
    result: list[tuple[str, Feature]] = []
    for name, feat in [
        ("tuned", PowerProfile(config, runner, log)),
        ("vrr", VRR(config, runner, log)),
        ("scx", SCXScheduler(config, runner, log)),
        ("audio", AudioPriority(config, runner, log)),
        ("inhibit", ScreenInhibit(config, runner, log)),
    ]:
        if name in config.toggle_features:
            result.append((name, cast(Feature, feat)))
    return result


def _apply_features(
    features: list[tuple[str, Feature]], output: str, log: logging.Logger, method: str
) -> None:
    log.debug("%sing features for output: %s", method.capitalize(), output)
    for name, feat in features:
        result = getattr(feat, method)(output)
        _BaseFeature._log_result(name, result, log)


def features_enable(
    features: list[tuple[str, Feature]], output: str, log: logging.Logger
) -> None:
    _apply_features(features, output, log, "enable")


def features_disable(
    features: list[tuple[str, Feature]], output: str, log: logging.Logger
) -> None:
    _apply_features(features, output, log, "disable")
