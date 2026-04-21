"""Compositor detection and output resolution."""

from __future__ import annotations

import os
import shutil
import subprocess
from functools import lru_cache

from gamemode.config import Config


def _session_contains(substring: str) -> bool:
    session = os.environ.get("XDG_SESSION_DESKTOP", "")
    current = os.environ.get("XDG_CURRENT_DESKTOP", "")
    return substring in (session + current).lower()


@lru_cache(maxsize=1)
def compositor_is_niri() -> bool:
    if _session_contains("niri"):
        return True
    if shutil.which("pgrep") is None:
        return False
    return (
        subprocess.run(
            ["pgrep", "-x", "niri"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode
        == 0
    )


def session_is_kde() -> bool:
    return _session_contains("kde")


def output_resolve(config: Config) -> str:
    return os.environ.get("NIRI_OUTPUT_NAME", config.vrr_output_default)
