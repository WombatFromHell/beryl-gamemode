"""Tests for compositor detection and output resolution."""

from gamemode.compositor import (
    _session_contains,
    compositor_is_niri,
    output_resolve,
    session_is_kde,
)


class TestCompositorDetection:
    def test_niri_via_env(self, monkeypatch):
        monkeypatch.setenv("XDG_SESSION_DESKTOP", "niri")
        monkeypatch.delenv("XDG_CURRENT_DESKTOP", raising=False)
        assert _session_contains("niri") is True
        assert compositor_is_niri() is True

    def test_kde_via_env(self, monkeypatch):
        monkeypatch.setenv("XDG_SESSION_DESKTOP", "KDE")
        monkeypatch.delenv("XDG_CURRENT_DESKTOP", raising=False)
        assert _session_contains("kde") is True
        assert session_is_kde() is True

    def test_not_kde(self, monkeypatch):
        monkeypatch.setenv("XDG_SESSION_DESKTOP", "niri")
        monkeypatch.delenv("XDG_CURRENT_DESKTOP", raising=False)
        assert _session_contains("kde") is False
        assert session_is_kde() is False


class TestOutputResolve:
    def test_default(self, tmp_path_cfg, monkeypatch):
        monkeypatch.delenv("NIRI_OUTPUT_NAME", raising=False)
        assert output_resolve(tmp_path_cfg) == "DP-1"

    def test_env_override(self, tmp_path_cfg, monkeypatch):
        monkeypatch.setenv("NIRI_OUTPUT_NAME", "HDMI-A-1")
        assert output_resolve(tmp_path_cfg) == "HDMI-A-1"
