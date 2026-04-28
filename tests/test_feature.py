"""Tests for FeatureResult and feature protocol."""

import logging

import pytest

from gamemode.config import Config
from gamemode.feature import FeatureResult, _BaseFeature
from gamemode.runner import Runner


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

    def test_noop_factory(self):
        r = FeatureResult.noop()
        assert r.ok is True
        assert r.skipped is False
        assert r.changed is False
        assert r.detail == ""

    def test_repr_skipped(self):
        r = FeatureResult.skip("reason")
        assert "skipped" in repr(r)
        assert "reason" in repr(r)

    def test_repr_changed(self):
        r = FeatureResult.did_change("detail")
        assert "changed" in repr(r)
        assert "detail" in repr(r)

    def test_repr_error(self):
        r = FeatureResult.error("fail")
        assert "error" in repr(r)
        assert "fail" in repr(r)

    def test_repr_noop(self):
        r = FeatureResult.noop()
        assert repr(r) == "FeatureResult(noop)"


class TestBaseFeature:
    def _make_base(self, logger):
        cfg = Config(
            enable_scx=False,
            enable_vrr=False,
            enable_tuned=False,
            enable_inhibit=False,
            enable_audio=False,
            enable_steam=False,
            runtime_dir="/tmp",
        )
        runner = Runner(logger)
        return _BaseFeature(cfg, runner, logger)

    def test_gate_disabled(self, logger):
        base = self._make_base(logger)
        result = base._gate(enabled=False, _name="test")
        assert result is not None
        assert result.skipped is True
        assert "disabled" in result.detail

    def test_gate_enabled(self, logger):
        base = self._make_base(logger)
        result = base._gate(enabled=True, _name="test")
        assert result is None

    def test_guarded_disabled(self, logger):
        base = self._make_base(logger)
        called = [False]
        result = base._guarded(
            enabled=False,
            name="test",
            fn=lambda: (called.__setitem__(0, True), FeatureResult.noop())[1],
        )
        assert result.skipped is True
        assert called[0] is False

    def test_guarded_enabled(self, logger):
        base = self._make_base(logger)
        result = base._guarded(
            enabled=True,
            name="test",
            fn=lambda: FeatureResult.did_change("applied"),
        )
        assert result.changed is True
        assert result.detail == "applied"

    def test_log_result_skipped(self, caplog):
        log = logging.getLogger("test_log_skip")
        log.handlers.clear()
        log.setLevel(logging.DEBUG)
        caplog.set_level(logging.DEBUG)
        result = FeatureResult.skip("reason")
        _BaseFeature._log_result("test", result, log)
        assert any("skipped" in r.message for r in caplog.records)

    def test_log_result_changed(self, caplog):
        log = logging.getLogger("test_log_changed")
        log.handlers.clear()
        log.setLevel(logging.DEBUG)
        caplog.set_level(logging.INFO)
        result = FeatureResult.did_change("detail")
        _BaseFeature._log_result("test", result, log)
        assert any("detail" in r.message for r in caplog.records)

    def test_log_result_error(self, caplog):
        log = logging.getLogger("test_log_error")
        log.handlers.clear()
        log.setLevel(logging.DEBUG)
        caplog.set_level(logging.WARNING)
        result = FeatureResult.error("fail")
        _BaseFeature._log_result("test", result, log)
        assert any("fail" in r.message for r in caplog.records)

    def test_log_result_noop(self, caplog):
        log = logging.getLogger("test_log_noop")
        log.handlers.clear()
        log.setLevel(logging.DEBUG)
        caplog.set_level(logging.DEBUG)
        result = FeatureResult.noop()
        _BaseFeature._log_result("test", result, log)
        assert any("no change" in r.message for r in caplog.records)
