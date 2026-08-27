"""Tests for compositor detection and output resolution."""

from unittest.mock import patch

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
        compositor_is_niri.cache_clear()
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

    def test_niri_pgrep_fallback(self, monkeypatch):
        """When env vars are unset but pgrep finds niri, should return True."""
        monkeypatch.setenv("XDG_SESSION_DESKTOP", "gnome")
        monkeypatch.delenv("XDG_CURRENT_DESKTOP", raising=False)
        with (
            patch("shutil.which", return_value="/usr/bin/pgrep"),
            patch(
                "subprocess.run",
                returncode=0,
                create=True,
            ) as mock_run,
        ):
            mock_run.return_value.returncode = 0
            # Clear the lru_cache to force re-evaluation
            compositor_is_niri.cache_clear()
            assert compositor_is_niri() is True

    def test_niri_pgrep_not_available(self, monkeypatch):
        """When pgrep is not available, should return False."""
        monkeypatch.setenv("XDG_SESSION_DESKTOP", "gnome")
        monkeypatch.delenv("XDG_CURRENT_DESKTOP", raising=False)
        with patch("shutil.which", return_value=None):
            compositor_is_niri.cache_clear()
            assert compositor_is_niri() is False

    def test_session_contains_xdg_current_desktop(self, monkeypatch):
        """_session_contains should check XDG_CURRENT_DESKTOP, not just XDG_SESSION_DESKTOP."""
        monkeypatch.setenv("XDG_SESSION_DESKTOP", "gnome")
        monkeypatch.setenv("XDG_CURRENT_DESKTOP", "niri")
        assert _session_contains("niri") is True


class TestOutputResolve:
    def test_default(self, tmp_path_cfg, monkeypatch):
        monkeypatch.delenv("NIRI_OUTPUT_NAME", raising=False)
        monkeypatch.delenv("VRR_OUTPUTS", raising=False)
        assert output_resolve(tmp_path_cfg) == ""

    def test_env_override(self, tmp_path_cfg, monkeypatch):
        monkeypatch.setenv("NIRI_OUTPUT_NAME", "HDMI-A-1")
        assert output_resolve(tmp_path_cfg) == "HDMI-A-1"

    def test_env_vrr_outputs(self, monkeypatch):
        monkeypatch.setenv("VRR_OUTPUTS", "HDMI-A-1,DP-4")
        monkeypatch.delenv("NIRI_OUTPUT_NAME", raising=False)
        from gamemode.config import Config

        assert output_resolve(Config(runtime_dir="/tmp")) == "HDMI-A-1,DP-4"
