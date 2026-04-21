"""Gamemode — Performance toggle for gaming sessions."""

import gamemode.actions  # noqa: F401
from gamemode.__version__ import __version__
from gamemode.actions import (
    _watch_parent,
    action_off,
    action_on,
    action_status,
    action_wrapper,
)
from gamemode.cli import USAGE, VERSION, cli_parse, main
from gamemode.compositor import (
    _session_contains,
    compositor_is_niri,
    output_resolve,
    session_is_kde,
)
from gamemode.config import Config, _env_bool, load_config_file
from gamemode.dependencies import validate_deps
from gamemode.feature import (
    CommandWrapper,
    Feature,
    FeatureResult,
    WrapperFactory,
    _BaseFeature,
)
from gamemode.features import (
    VRR,
    WRAPPER_FACTORIES,
    AudioPriority,
    PowerProfile,
    ScreenInhibit,
    SCXScheduler,
    SystemdRun,
    WrapperChain,
    inhibit_wrapper_factory,
    steam_wrapper_factory,
    systemd_run_wrapper_factory,
)
from gamemode.logging_setup import setup_logging
from gamemode.orchestration import (
    collect_features,
    features_disable,
    features_enable,
)
from gamemode.runner import CheckedCommandRunner, Runner
from gamemode.state import StateManager

__all__ = [
    "__version__",
    "Config",
    "_env_bool",
    "load_config_file",
    "setup_logging",
    "Runner",
    "CheckedCommandRunner",
    "validate_deps",
    "compositor_is_niri",
    "session_is_kde",
    "output_resolve",
    "_session_contains",
    "StateManager",
    "FeatureResult",
    "Feature",
    "_BaseFeature",
    "CommandWrapper",
    "WrapperFactory",
    "VRR",
    "PowerProfile",
    "SCXScheduler",
    "AudioPriority",
    "SystemdRun",
    "ScreenInhibit",
    "steam_wrapper_factory",
    "inhibit_wrapper_factory",
    "systemd_run_wrapper_factory",
    "WRAPPER_FACTORIES",
    "WrapperChain",
    "collect_features",
    "features_enable",
    "features_disable",
    "action_on",
    "action_off",
    "action_status",
    "action_wrapper",
    "_watch_parent",
    "USAGE",
    "VERSION",
    "cli_parse",
    "main",
]
