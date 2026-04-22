"""Tests for FeatureResult and feature protocol."""

import pytest

from gamemode.feature import FeatureResult


class TestFeatureResult:
    @pytest.mark.parametrize(
        "factory,attrs",
        [
            (
                lambda: FeatureResult.skip("no niri"),
                {"ok": True, "skipped": True, "changed": False},
            ),
            (
                lambda: FeatureResult.did_change("on"),
                {"changed": True, "ok": True},
            ),
            (lambda: FeatureResult.error("failed"), {"ok": False}),
        ],
    )
    def test_factories(self, factory, attrs):
        r = factory()
        for attr, expected in attrs.items():
            assert getattr(r, attr) == expected
