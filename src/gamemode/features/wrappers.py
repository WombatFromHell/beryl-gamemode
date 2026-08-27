"""Command wrapper infrastructure."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from gamemode.config import Config
from gamemode.feature import (
    CommandWrapper,
    WrapperFactory,
)
from gamemode.runner import Runner


def systemd_run_wrapper_factory(
    config: Config, runner: Runner, log: logging.Logger
) -> CommandWrapper | None:
    if not config.enable_systemd_run:
        return None
    if not runner.require("systemd-run", "systemd-run"):
        return None
    if not config.systemd_run_args:
        log.warning("ENABLE_SYSTEMD_RUN is set but SYSTEMD_RUN_ARGS is empty")
        return None

    def wrap(argv: list[str]) -> list[str]:
        log.debug(
            "systemd-run wrapping: %s",
            " ".join(config.systemd_run_args + ["--", *argv]),
        )
        return ["systemd-run", *config.systemd_run_args, "--", *argv]

    return wrap


def steam_wrapper_factory(
    config: Config, _runner: Runner, log: logging.Logger
) -> CommandWrapper | None:
    if not config.enable_steam:
        return None

    def wrap(argv: list[str]) -> list[str]:
        path = Path(config.steam_script)
        if not path.is_file() or not os.access(path, os.X_OK):
            log.debug("Steam wrapper not available: %s", path)
            return argv
        return [str(path), *argv]

    return wrap


def inhibit_wrapper_factory(
    config: Config, runner: Runner, log: logging.Logger
) -> CommandWrapper | None:
    inhibit = runner.resolve("systemd-inhibit")
    if not config.enable_sleep_inhibit or inhibit is None:
        return None

    def wrapper(argv: list[str]) -> list[str]:
        return [
            inhibit,
            "--what=idle:sleep",
            "--mode=block",
            "--why=gamemode.py",
            "--",
            *argv,
        ]

    return wrapper


WRAPPER_FACTORIES: dict[str, WrapperFactory] = {
    "steam": steam_wrapper_factory,
    "inhibit": inhibit_wrapper_factory,
    "systemd_run": systemd_run_wrapper_factory,
}


class WrapperChain:
    def __init__(self) -> None:
        self._wrappers: list[CommandWrapper] = []

    def add_factory(
        self,
        factory: WrapperFactory,
        config: Config,
        runner: Runner,
        log: logging.Logger,
    ) -> None:
        wrapper = factory(config, runner, log)
        if wrapper is not None:
            self._wrappers.append(wrapper)

    def apply(self, argv: list[str]) -> list[str]:
        result = list(argv)
        for wrapper in self._wrappers:
            result = wrapper(result)
        return result
