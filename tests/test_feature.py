"""Tests for FeatureResult and feature protocol."""

import pytest

import gamemode


class TestFeatureResult:
    @pytest.mark.parametrize(
        "factory,attrs",
        [
            (
                lambda: gamemode.FeatureResult.skip("no niri"),
                {"ok": True, "skipped": True, "changed": False},
            ),
            (
                lambda: gamemode.FeatureResult.did_change("on"),
                {"changed": True, "ok": True},
            ),
            (lambda: gamemode.FeatureResult.error("failed"), {"ok": False}),
        ],
    )
    def test_factories(self, factory, attrs):
        r = factory()
        for attr, expected in attrs.items():
            assert getattr(r, attr) == expected
