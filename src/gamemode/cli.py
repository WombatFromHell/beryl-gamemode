"""CLI parser, usage string, and main entry point."""

from __future__ import annotations

import os
import sys

from gamemode.__version__ import _get_version
from gamemode.actions import action_off, action_on, action_status, action_wrapper
from gamemode.config import Config, load_config_file
from gamemode.dependencies import validate_deps
from gamemode.logging_setup import setup_logging
from gamemode.runner import Runner


def _invocation_name() -> str:
    """Return the command the user typed to invoke this tool."""
    return os.path.basename(sys.argv[0])


USAGE_TEMPLATE = """\
Usage: {cmd} [MODE] [COMMAND...]

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
  All env vars override config file values.
  Booleans accept: true/false, 1/0, yes/no.

  General:
    DEBUG=1              Enable debug logging
    XDG_RUNTIME_DIR      State dir and log file location (default: /tmp)

  Feature Toggles:
    ENABLE_VRR               Enable VRR feature (default: true)
    ENABLE_SCX_SCHEDULER     Enable SCX scheduler feature (default: true)
    ENABLE_PERFORMANCE_MODE  Enable tuned power profile (default: false)
    ENABLE_SCREEN_KEEP_AWAKE Enable screen inhibit (default: true)
    ENABLE_AUDIO_PRIORITY_BOOST  Enable PulseAudio low-latency (default: false)
    ENABLE_STEAM_ENV         Enable Steam env wrapper (default: true)
    ENABLE_SYSTEMD_RUN       Enable systemd-run wrapper (default: true)

  VRR:
    VRR_OUTPUT          Target display output (default: DP-1)
    NIRI_OUTPUT_NAME    Override target output (falls back to VRR_OUTPUT)

  SCX Scheduler:
    SCX_SCHEDULER       Scheduler name (default: lavd)
    SCX_SCHEDULER_MODE  Scheduler mode (default: gaming)

  Tuned:
    GAME_PROFILE     Power profile for gaming (default: throughput-performance-bazzite)
    DESKTOP_PROFILE  Power profile for desktop (default: balanced-bazzite)

  Audio:
    PULSE_LATENCY_MSEC  PulseAudio latency in ms (default: 60)

  Steam:
    STEAM_ENV_SCRIPT  Path to Steam env script (default: ~/.local/bin/scripts/steam-env-base.sh)

  Systemd-Run:
    SYSTEMD_RUN_ARGS  systemd-run arguments
                      (default: --user --scope --slice=app.slice
                       --property=CPUWeight=500 --property=IOWeight=500)

EXAMPLES:
  {cmd} on                          # Toggle on
  {cmd} off                         # Toggle off
  {cmd} -- steam                    # Wrapper: launch steam
  {cmd} -- ~/Games/hero/main.sh     # Wrapper: run a game directly
  ENABLE_SYSTEMD_RUN=false {cmd} -- steam  # Disable systemd-run wrapper
  TOGGLE_FEATURES=vrr,scx {cmd} on  # Only enable VRR & SCX
"""


def _get_usage() -> str:
    cmd = _invocation_name()
    return USAGE_TEMPLATE.format(cmd=cmd, _version_=_get_version())


VERSION = "Gamemode " + _get_version()


def cli_parse(argv: list[str] | None = None) -> tuple[str | None, list[str]]:
    if argv is None:
        argv = sys.argv[1:]
    if not argv:
        print(_get_usage(), end="")
        return None, []
    if argv[0] in ("-h", "--help"):
        print(_get_usage(), end="")
        return None, []
    mode = argv[0]
    if mode in ("on", "off", "status"):
        return mode, []
    if mode == "--":
        command = argv[1:]
        if not command:
            print("Error: wrapper mode requires a command after '--'", file=sys.stderr)
            print(_get_usage(), end="", file=sys.stderr)
            return None, []
        return "wrapper", command
    return "wrapper", argv


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help"):
        print(_get_usage(), end="")
        return 0
    if argv[0] in ("-V", "--version"):
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

    if mode not in ("on", "off", "status", "wrapper"):
        log.error("Unknown subcommand: '%s'", mode)
        return 1
    return {
        "on": lambda: action_on(config, runner, log, debug=debug),
        "off": lambda: action_off(config, runner, log, debug=debug),
        "status": lambda: action_status(config),
        "wrapper": lambda: action_wrapper(config, runner, log, command, debug=debug),
    }[mode]()
