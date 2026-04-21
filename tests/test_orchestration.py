"""Tests for feature orchestration module."""

import gamemode


class TestFeatureOrchestration:
    def test_collect_features_returns_all(self, tmp_path_cfg, logger):
        features = gamemode.collect_features(
            tmp_path_cfg, gamemode.Runner(logger), logger
        )
        names = [name for name, _ in features]
        assert names == ["tuned", "vrr", "scx", "audio", "inhibit"]
