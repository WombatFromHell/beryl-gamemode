"""Configuration file loader and runtime configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _should_skip_line(line: str) -> bool:
    """Return True if the line should be skipped."""
    return not line or line.startswith("#") or "=" not in line


def _parse_line(line: str) -> tuple[str, str] | None:
    """Parse a KEY=VALUE line and strip surrounding quotes from the value."""
    key, _, val = line.partition("=")
    key = key.strip()
    val = val.strip()
    if len(val) >= 2 and val[0] in ("'", '"') and val[0] == val[-1]:
        val = val[1:-1]
    return key, val


def load_config_file(path: Path | None = None) -> None:
    """Load KEY=VALUE config from file into os.environ (env vars take precedence)."""
    if path is None:
        path = Path.home() / ".config" / "gamemode.conf"
    if not path.is_file():
        return
    try:
        for raw_line in path.read_text().splitlines():
            line = raw_line.strip()
            if _should_skip_line(line):
                continue
            parsed = _parse_line(line)
            if parsed is not None:
                key, val = parsed
                if key not in os.environ:
                    os.environ[key] = val
    except OSError:
        pass


def _env_bool(name: str, default: bool) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.lower() in ("true", "1", "yes")


def _env_set(name: str, default: str) -> set[str]:
    raw = os.environ.get(name, default)
    return {s.strip().lower() for s in raw.split(",") if s.strip()}


@dataclass(frozen=True, slots=True)
class Config:
    toggle_features: set[str] = field(
        default_factory=lambda: _env_set(
            "TOGGLE_FEATURES", "vrr,scx,tuned,audio,inhibit,steam"
        ),
    )
    wrapper_features: set[str] = field(
        default_factory=lambda: _env_set(
            "WRAPPER_FEATURES", "systemd_run,steam,inhibit"
        ),
    )
    enable_scx: bool = field(
        default_factory=lambda: _env_bool("ENABLE_SCX_SCHEDULER", True)
    )
    enable_vrr: bool = field(default_factory=lambda: _env_bool("ENABLE_VRR", True))
    enable_tuned: bool = field(
        default_factory=lambda: _env_bool("ENABLE_PERFORMANCE_MODE", False)
    )
    enable_inhibit: bool = field(
        default_factory=lambda: _env_bool("ENABLE_SCREEN_KEEP_AWAKE", True)
    )
    enable_sleep_inhibit: bool = field(
        default_factory=lambda: _env_bool("ENABLE_SLEEP_INHIBIT", True)
    )
    enable_audio: bool = field(
        default_factory=lambda: _env_bool("ENABLE_AUDIO_PRIORITY_BOOST", False)
    )
    enable_steam: bool = field(
        default_factory=lambda: _env_bool("ENABLE_STEAM_ENV", True)
    )
    enable_systemd_run: bool = field(
        default_factory=lambda: _env_bool("ENABLE_SYSTEMD_RUN", True)
    )
    enable_idle_monitor: bool = field(
        default_factory=lambda: _env_bool(
            "ENABLE_IDLE_MONITOR",
            bool(os.environ.get("IDLE_CMD") and os.environ.get("ACTIVE_CMD")),
        )
    )
    idle_cmd: str = field(default_factory=lambda: os.environ.get("IDLE_CMD", ""))
    active_cmd: str = field(default_factory=lambda: os.environ.get("ACTIVE_CMD", ""))
    idle_timeout: int = field(
        default_factory=lambda: int(os.environ.get("IDLE_TIMEOUT", "300"))
    )
    idle_poll_interval: int = field(
        default_factory=lambda: int(os.environ.get("IDLE_POLL_INTERVAL", "1"))
    )
    scx_scheduler: str = field(
        default_factory=lambda: os.environ.get("SCX_SCHEDULER", "lavd")
    )
    scx_mode: str = field(
        default_factory=lambda: os.environ.get("SCX_SCHEDULER_MODE", "gaming")
    )
    profile_game: str = field(
        default_factory=lambda: os.environ.get(
            "GAME_PROFILE", "throughput-performance-bazzite"
        )
    )
    profile_desktop: str = field(
        default_factory=lambda: os.environ.get("DESKTOP_PROFILE", "balanced-bazzite")
    )
    audio_latency: str = field(
        default_factory=lambda: os.environ.get("PULSE_LATENCY_MSEC", "60")
    )
    steam_script: str = field(
        default_factory=lambda: os.environ.get(
            "STEAM_ENV_SCRIPT",
            str(Path.home() / ".local" / "bin" / "scripts" / "steam-env-base.sh"),
        )
    )
    vrr_output_default: str = field(
        default_factory=lambda: os.environ.get("VRR_OUTPUTS", "DP-1")
    )
    systemd_run_args: list[str] = field(
        default_factory=lambda: (
            os.environ.get("SYSTEMD_RUN_ARGS", "").split()
            or [
                "--user",
                "--scope",
                "--slice=app.slice",
                "--property=CPUWeight=500",
                "--property=IOWeight=500",
            ]
        )
    )
    runtime_dir: str = field(
        default_factory=lambda: os.environ.get("XDG_RUNTIME_DIR", "/tmp")
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
        return Path(self.runtime_dir) / "gamemode.log"

    @property
    def audio_env_file(self) -> Path:
        return self.state_dir / "audio.env"
