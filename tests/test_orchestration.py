"""Tests for feature orchestration module."""

from gamemode.orchestration import collect_features
from gamemode.runner import Runner


class TestFeatureOrchestration:
    def test_collect_features_returns_all(self, tmp_path_cfg, logger):
        features = collect_features(tmp_path_cfg, Runner(logger), logger)
        names = [name for name, _ in features]
        assert names == ["tuned", "vrr", "scx", "audio", "inhibit"]
