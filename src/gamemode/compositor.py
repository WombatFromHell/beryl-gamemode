"""Compositor detection and output resolution."""

from __future__ import annotations

import shutil
import subprocess

from gamemode.config import Config


def _session_contains(config: Config, substring: str) -> bool:
    session = config.xdg_session_desktop
    current = config.xdg_current_desktop
    return substring in (session + current).lower()


def compositor_is_niri(config: Config) -> bool:
    if _session_contains(config, "niri"):
        return True
    if shutil.which("pgrep") is None:
        return False
    return (
        subprocess.run(
            ["pgrep", "-x", "niri"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        ).returncode
        == 0
    )


def session_is_kde(config: Config) -> bool:
    return _session_contains(config, "kde")


def output_resolve(config: Config) -> str:
    return config.niri_output_name or config.vrr_output_default
