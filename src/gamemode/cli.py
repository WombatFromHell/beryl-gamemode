"""CLI parser, usage string, and main entry point."""

from __future__ import annotations

import os
import sys
import textwrap

from gamemode.__version__ import _get_version
from gamemode.actions import action_off, action_on, action_status, action_wrapper
from gamemode.config import Config, load_config_file
from gamemode.dependencies import validate_deps
from gamemode.logging_setup import setup_logging
from gamemode.runner import Runner

USAGE = textwrap.dedent("""\
Usage: gamemode.py [MODE] [COMMAND...]

Gamemode {_version_} — Performance toggle for gaming sessions.

MODES:
  on              Activate gaming mode (applies TOGGLE_FEATURES)
  off             Deactivate gaming mode, restore desktop defaults (always force cleanup)
  status          Show current state and diagnostics
  -- <command>    Wrapper mode: enable features, run <command>, auto-cleanup on exit
                  Applies WRAPPER_FEATURES only. Skips features if 'on' was already run.
                  <command> may also be given without -- (bare command is wrapper mode).

CONFIGURATION:
  File: $HOME/.config/gamemode.conf (KEY=VALUE format, # comments supported)
  Env vars override file values.

FEATURE ROUTING (comma-separated, case-insensitive):
  TOGGLE_FEATURES   Applied by 'on'/'off'. Default: vrr,scx,tuned,audio,inhibit,steam
  WRAPPER_FEATURES  Applied by wrapper mode. Default: systemd_run,steam,inhibit

ENVIRONMENT:
  NIRI_OUTPUT_NAME   Override target output (falls back to VRR_OUTPUT)
  DEBUG=1            Enable debug logging
  XDG_RUNTIME_DIR    State dir and log file location (default: /tmp)

EXAMPLES:
  python3 gamemode.py on                          # Toggle on
  python3 gamemode.py off                         # Toggle off
  python3 gamemode.py -- steam                    # Wrapper: launch steam
  python3 gamemode.py -- ~/Games/hero/main.sh     # Wrapper: run a game directly
  ENABLE_SYSTEMD_RUN=false python3 gamemode.py -- steam  # Disable systemd-run wrapper
  TOGGLE_FEATURES=vrr,scx python3 gamemode.py on  # Only enable VRR & SCX
""").replace("{_version_}", _get_version())


VERSION = "Gamemode " + _get_version()


def cli_parse(argv: list[str] | None = None) -> tuple[str | None, list[str]]:
    if argv is None:
        argv = sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help", "-V", "--version"):
        print(USAGE, end="")
        return None, []
    mode = argv[0]
    if mode in ("on", "off", "status"):
        return mode, []
    if mode == "--":
        command = argv[1:]
        if not command:
            print("Error: wrapper mode requires a command after '--'", file=sys.stderr)
            print(USAGE, end="", file=sys.stderr)
            return None, []
        return "wrapper", command
    return "wrapper", argv


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if not argv or argv[0] in ("-V", "--version"):
        print(VERSION)
        return 0

    load_config_file()

    config = Config()
    debug = os.environ.get("DEBUG", "") in ("1", "true", "yes")
    log = setup_logging(config, to_file=False, debug=debug)
    mode, command = cli_parse(argv)
    if mode is None:
        return 1

    runner = Runner(log)
    if not validate_deps(config, runner, log):
        return 1

    if mode == "on":
        return action_on(config, runner, log, debug=debug)
    if mode == "off":
        return action_off(config, runner, log, debug=debug)
    if mode == "status":
        return action_status(config)
    if mode == "wrapper":
        return action_wrapper(config, runner, log, command, debug=debug)

    log.error("Unknown subcommand: '%s'", mode)
    return 1
