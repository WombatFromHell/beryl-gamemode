"""Feature orchestration."""

from __future__ import annotations

import collections.abc
import logging

from gamemode.config import Config
from gamemode.feature import _BaseFeature, log_feature_result
from gamemode.registry import FEATURES
from gamemode.runner import Runner


def collect_features(
    config: Config, runner: Runner, log: logging.Logger
) -> collections.abc.Sequence[tuple[str, _BaseFeature]]:
    result: list[tuple[str, _BaseFeature]] = []
    for name, spec in FEATURES.items():
        if spec.factory is not None and name in config.toggle_features:
            result.append((name, spec.factory(config, runner, log)))
    return result


def features_enable(
    features: collections.abc.Sequence[tuple[str, _BaseFeature]],
    log: logging.Logger,
) -> None:
    log.debug("Enabling features")
    for name, feat in features:
        log_feature_result(name, feat.enable(), log)


def features_disable(
    features: collections.abc.Sequence[tuple[str, _BaseFeature]],
    log: logging.Logger,
) -> None:
    log.debug("Disabling features")
    for name, feat in features:
        log_feature_result(name, feat.disable(), log)
