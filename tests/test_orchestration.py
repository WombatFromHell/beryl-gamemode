"""Tests for feature orchestration module."""

import logging

from conftest import FakeFeature, _cfg

from gamemode.orchestration import (
    _apply_features,
    collect_features,
    features_disable,
    features_enable,
)
from gamemode.runner import Runner


class TestFeatureOrchestration:
    def test_collect_features_returns_all(self, tmp_path_cfg, logger):
        features = collect_features(tmp_path_cfg, Runner(logger), logger)
        names = [name for name, _ in features]
        assert names == ["tuned", "vrr", "scx", "audio", "inhibit"]

    def test_collect_features_subset(self, logger, tmp_path):
        """When toggle_features is a subset, only those features are collected."""
        cfg = _cfg(
            runtime_dir=str(tmp_path),
            toggle_features={"scx", "vrr"},
        )
        features = collect_features(cfg, Runner(logger), logger)
        names = [name for name, _ in features]
        assert names == ["vrr", "scx"]

    def test_collect_features_empty(self, logger, tmp_path):
        """When toggle_features is empty, collect_features returns empty list."""
        cfg = _cfg(
            runtime_dir=str(tmp_path),
            toggle_features=set(),
        )
        features = collect_features(cfg, Runner(logger), logger)
        assert features == []

    def test_features_enable_calls_enable(self, logger):
        """features_enable should call .enable() on each feature."""
        f1 = FakeFeature("f1")
        f2 = FakeFeature("f2")
        features = [("f1", f1), ("f2", f2)]
        features_enable(features, logger)
        assert f1.enable_calls == [True]
        assert f2.enable_calls == [True]

    def test_features_disable_calls_disable(self, logger):
        """features_disable should call .disable() on each feature."""
        f1 = FakeFeature("f1")
        f2 = FakeFeature("f2")
        features = [("f1", f1), ("f2", f2)]
        features_disable(features, logger)
        assert f1.disable_calls == [True]
        assert f2.disable_calls == [True]

    def test_apply_features_logging(self, logger, caplog):
        """_apply_features should log the method and output."""
        caplog.set_level(logging.DEBUG)
        f1 = FakeFeature("f1")
        features = [("f1", f1)]
        _apply_features(features, logger, "enable")
        assert any("features" in r.message.lower() for r in caplog.records)
