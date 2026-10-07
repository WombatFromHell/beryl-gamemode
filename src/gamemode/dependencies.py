"""Dependency validation for gamemode features."""

from __future__ import annotations

import logging

from gamemode.config import Config
from gamemode.registry import FEATURES
from gamemode.runner import Runner


def validate_deps(config: Config, runner: Runner, log: logging.Logger) -> bool:
    missing = [
        cmd
        for spec in FEATURES.values()
        for cmd, flag in spec.deps
        if getattr(config, flag) and runner.resolve(cmd) is None
    ]
    if missing:
        log.error("Missing dependencies: %s", " ".join(missing))
        return False
    return True
