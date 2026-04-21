"""Logging setup for gamemode."""

from __future__ import annotations

import logging
import sys

from gamemode.config import Config


def setup_logging(
    config: Config, *, to_file: bool = False, debug: bool = False
) -> logging.Logger:
    log = logging.getLogger("gamemode")
    if log.handlers:
        log.handlers.clear()
    log.setLevel(logging.DEBUG)
    fmt = logging.Formatter(
        "%(asctime)s [gamemode] %(levelname)s: %(message)s", datefmt="%H:%M:%S"
    )
    console = logging.StreamHandler(sys.stderr)
    console.setLevel(logging.DEBUG if debug else logging.INFO)
    console.setFormatter(fmt)
    log.addHandler(console)
    if to_file:
        config.log_file.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(config.log_file, mode="w")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(fmt)
        log.addHandler(fh)
    return log
