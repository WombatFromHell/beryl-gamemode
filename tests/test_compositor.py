"""Tests for compositor detection and output resolution."""

from unittest.mock import patch

from gamemode.compositor import (
    _session_contains,
    compositor_is_niri,
    output_resolve,
    session_is_kde,
)
from gamemode.config import Config


class TestCompositorDetection:
    def test_niri_via_env(self):
        cfg = Config(xdg_session_desktop="niri")
        assert _session_contains(cfg, "niri") is True
        assert compositor_is_niri(cfg) is True

    def test_kde_via_env(self):
        cfg = Config(xdg_session_desktop="KDE")
        assert _session_contains(cfg, "kde") is True
        assert session_is_kde(cfg) is True

    def test_not_kde(self):
        cfg = Config(xdg_session_desktop="niri")
        assert _session_contains(cfg, "kde") is False
        assert session_is_kde(cfg) is False

    def test_niri_pgrep_fallback(self):
        """When env vars are unset but pgrep finds niri, should return True."""
        cfg = Config(xdg_session_desktop="gnome")
        with (
            patch("shutil.which", return_value="/usr/bin/pgrep"),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value.returncode = 0
            assert compositor_is_niri(cfg) is True

    def test_niri_pgrep_not_available(self):
        """When pgrep is not available, should return False."""
        cfg = Config(xdg_session_desktop="gnome")
        with patch("shutil.which", return_value=None):
            assert compositor_is_niri(cfg) is False

    def test_session_contains_xdg_current_desktop(self):
        """_session_contains should check XDG_CURRENT_DESKTOP, not just XDG_SESSION_DESKTOP."""
        cfg = Config(xdg_session_desktop="gnome", xdg_current_desktop="niri")
        assert _session_contains(cfg, "niri") is True


class TestOutputResolve:
    def test_default(self):
        assert output_resolve(Config(runtime_dir="/tmp")) == ""

    def test_niri_output_name_override(self):
        assert output_resolve(Config(niri_output_name="HDMI-A-1")) == "HDMI-A-1"

    def test_vrr_outputs_default(self):
        cfg = Config(runtime_dir="/tmp", vrr_output_default="HDMI-A-1,DP-4")
        assert output_resolve(cfg) == "HDMI-A-1,DP-4"
