"""Configuration file loader and runtime configuration.

`Config.from_env` is the single env boundary: every gamemode variable is read
once, from a config file (defaults) overridden by os.environ, or from an
explicit mapping in tests.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path


def _default_toggle() -> str:
    """Default TOGGLE_FEATURES string, derived from the feature registry."""
    from gamemode.registry import default_toggle_string

    return default_toggle_string()


_DEFAULT_WRAPPER = "systemd_run,steam,inhibit"
_DEFAULT_SYSTEMD_RUN_ARGS = [
    "--user",
    "--scope",
    "--slice=app.slice",
    "--property=CPUWeight=500",
    "--property=IOWeight=500",
]


def _should_skip_line(line: str) -> bool:
    """Return True if the line should be skipped."""
    return not line or line.startswith("#") or "=" not in line


def _parse_line(line: str) -> tuple[str, str]:
    """Parse a KEY=VALUE line and strip surrounding quotes from the value."""
    key, _, val = line.partition("=")
    key = key.strip()
    val = val.strip()
    if len(val) >= 2 and val[0] in ("'", '"') and val[0] == val[-1]:
        val = val[1:-1]
    return key, val


def load_config_file(path: Path | None = None) -> dict[str, str]:
    """Read KEY=VALUE pairs from the config file; {} if it doesn't exist."""
    if path is None:
        path = Path.home() / ".config" / "gamemode.conf"
    if not path.is_file():
        return {}
    pairs: dict[str, str] = {}
    try:
        for raw_line in path.read_text().splitlines():
            line = raw_line.strip()
            if _should_skip_line(line):
                continue
            key, val = _parse_line(line)
            pairs.setdefault(key, val)
    except OSError:
        pass
    return pairs


def _bool(env: Mapping[str, str], name: str, default: bool) -> bool:
    val = env.get(name)
    if val is None:
        return default
    return val.lower() in ("true", "1", "yes")


def _set(env: Mapping[str, str], name: str, default: str) -> set[str]:
    raw = env.get(name, default)
    return {s.strip().lower() for s in raw.split(",") if s.strip()}


@dataclass(frozen=True, slots=True)
class Config:
    toggle_features: set[str] = field(
        default_factory=lambda: _set({}, "TOGGLE_FEATURES", _default_toggle())
    )
    wrapper_features: set[str] = field(
        default_factory=lambda: _set({}, "WRAPPER_FEATURES", _DEFAULT_WRAPPER)
    )
    enable_scx: bool = True
    enable_vrr: bool = True
    enable_tuned: bool = False
    enable_inhibit: bool = True
    enable_sleep_inhibit: bool = True
    enable_audio: bool = False
    enable_steam: bool = True
    enable_systemd_run: bool = True
    enable_idle_monitor: bool = False
    idle_monitor_explicit: bool = False
    idle_cmd: str = ""
    active_cmd: str = ""
    idle_timeout: int = 300
    idle_poll_interval: int = 1
    scx_scheduler: str = "lavd"
    scx_mode: str = "gaming"
    profile_game: str = "throughput-performance-bazzite"
    profile_desktop: str = "balanced-bazzite"
    audio_latency: str = "60"
    steam_script: str = ""
    vrr_output_default: str = ""
    systemd_run_args: list[str] = field(
        default_factory=lambda: list(_DEFAULT_SYSTEMD_RUN_ARGS)
    )
    runtime_dir: str = "/tmp"
    debug: bool = False
    shell: str = ""
    xdg_session_desktop: str = ""
    xdg_current_desktop: str = ""
    niri_output_name: str = ""

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Config:
        """Build a Config from the config file + os.environ, or a mapping."""
        if env is None:
            env = {**load_config_file(), **os.environ}
        return cls(
            toggle_features=_set(env, "TOGGLE_FEATURES", _default_toggle()),
            wrapper_features=_set(env, "WRAPPER_FEATURES", _DEFAULT_WRAPPER),
            enable_scx=_bool(env, "ENABLE_SCX_SCHEDULER", True),
            enable_vrr=_bool(env, "ENABLE_VRR", True),
            enable_tuned=_bool(env, "ENABLE_PERFORMANCE_MODE", False),
            enable_inhibit=_bool(env, "ENABLE_SCREEN_KEEP_AWAKE", True),
            enable_sleep_inhibit=_bool(env, "ENABLE_SLEEP_INHIBIT", True),
            enable_audio=_bool(env, "ENABLE_AUDIO_PRIORITY_BOOST", False),
            enable_steam=_bool(env, "ENABLE_STEAM_ENV", True),
            enable_systemd_run=_bool(env, "ENABLE_SYSTEMD_RUN", True),
            enable_idle_monitor=_bool(
                env,
                "ENABLE_IDLE_MONITOR",
                bool(env.get("IDLE_CMD") and env.get("ACTIVE_CMD")),
            ),
            idle_monitor_explicit="ENABLE_IDLE_MONITOR" in env,
            idle_cmd=env.get("IDLE_CMD", ""),
            active_cmd=env.get("ACTIVE_CMD", ""),
            idle_timeout=int(env.get("IDLE_TIMEOUT", "300")),
            idle_poll_interval=int(env.get("IDLE_POLL_INTERVAL", "1")),
            scx_scheduler=env.get("SCX_SCHEDULER", "lavd"),
            scx_mode=env.get("SCX_SCHEDULER_MODE", "gaming"),
            profile_game=env.get("GAME_PROFILE", "throughput-performance-bazzite"),
            profile_desktop=env.get("DESKTOP_PROFILE", "balanced-bazzite"),
            audio_latency=env.get("PULSE_LATENCY_MSEC", "60"),
            steam_script=(
                env["STEAM_ENV_SCRIPT"]
                if "STEAM_ENV_SCRIPT" in env
                else (shutil.which("steam-env-base.sh") or "")
            ),
            vrr_output_default=env.get("VRR_OUTPUTS", ""),
            systemd_run_args=(
                env.get("SYSTEMD_RUN_ARGS", "").split()
                or list(_DEFAULT_SYSTEMD_RUN_ARGS)
            ),
            runtime_dir=env.get("XDG_RUNTIME_DIR", "/tmp"),
            debug=_bool(env, "GAMEMODE_DEBUG", False),
            shell=env.get("SHELL", ""),
            xdg_session_desktop=env.get("XDG_SESSION_DESKTOP", ""),
            xdg_current_desktop=env.get("XDG_CURRENT_DESKTOP", ""),
            niri_output_name=env.get("NIRI_OUTPUT_NAME", ""),
        )

    @property
    def state_dir(self) -> Path:
        return Path(self.runtime_dir) / "gamemode"

    @property
    def state_file(self) -> Path:
        return self.state_dir / "gamemode.state"

    @property
    def lock_file(self) -> Path:
        return self.state_dir / "lock"

    @property
    def log_file(self) -> Path:
        return self.state_dir / "gamemode.log"

    @property
    def audio_env_file(self) -> Path:
        return self.state_dir / "audio.env"
