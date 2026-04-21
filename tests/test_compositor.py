"""Tests for compositor detection and output resolution."""

import gamemode


class TestCompositorDetection:
    def test_niri_via_env(self, monkeypatch):
        monkeypatch.setenv("XDG_SESSION_DESKTOP", "niri")
        monkeypatch.delenv("XDG_CURRENT_DESKTOP", raising=False)
        assert gamemode._session_contains("niri") is True
        assert gamemode.compositor_is_niri() is True

    def test_kde_via_env(self, monkeypatch):
        monkeypatch.setenv("XDG_SESSION_DESKTOP", "KDE")
        monkeypatch.delenv("XDG_CURRENT_DESKTOP", raising=False)
        assert gamemode._session_contains("kde") is True
        assert gamemode.session_is_kde() is True

    def test_not_kde(self, monkeypatch):
        monkeypatch.setenv("XDG_SESSION_DESKTOP", "niri")
        monkeypatch.delenv("XDG_CURRENT_DESKTOP", raising=False)
        assert gamemode._session_contains("kde") is False
        assert gamemode.session_is_kde() is False


class TestOutputResolve:
    def test_default(self, tmp_path_cfg, monkeypatch):
        monkeypatch.delenv("NIRI_OUTPUT_NAME", raising=False)
        assert gamemode.output_resolve(tmp_path_cfg) == "DP-1"

    def test_env_override(self, tmp_path_cfg, monkeypatch):
        monkeypatch.setenv("NIRI_OUTPUT_NAME", "HDMI-A-1")
        assert gamemode.output_resolve(tmp_path_cfg) == "HDMI-A-1"
