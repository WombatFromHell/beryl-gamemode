"""Dependency validation for gamemode features."""

from __future__ import annotations

import logging

from gamemode.config import Config
from gamemode.runner import Runner


def validate_deps(config: Config, runner: Runner, log: logging.Logger) -> bool:
    checks: dict[str, bool] = {
        "tuned-adm": config.enable_tuned,
        "systemd-inhibit": config.enable_inhibit,
        "dbus-send": config.enable_inhibit,
        "scxctl": config.enable_scx,
        "jq": config.enable_vrr,
    }
    missing = [
        cmd
        for cmd, enabled in checks.items()
        if enabled and runner.resolve(cmd) is None
    ]
    if missing:
        log.error("Missing dependencies: %s", " ".join(missing))
        return False
    return True
