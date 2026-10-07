"""Feature registry: the single declaration of the feature set.

Drives `collect_features`, `validate_deps`, the default toggle set, and the
USAGE routing text. Adding a feature is one line here.

`deps` entries are `(command, config attribute)` pairs: the command is
required when that config flag is enabled. `factory` is `None` for
wrapper-only features (steam) that route through no feature class.
"""

from __future__ import annotations

from dataclasses import dataclass

from gamemode.feature import _BaseFeature
from gamemode.features.audio_priority import AudioPriority
from gamemode.features.power_profile import PowerProfile
from gamemode.features.screen_inhibit import ScreenInhibit
from gamemode.features.scx_scheduler import SCXScheduler
from gamemode.features.vrr import VRR


@dataclass(frozen=True, slots=True)
class FeatureSpec:
    factory: type[_BaseFeature] | None
    deps: tuple[tuple[str, str], ...]
    default_toggle: bool


FEATURES: dict[str, FeatureSpec] = {
    "tuned": FeatureSpec(PowerProfile, (("tuned-adm", "enable_tuned"),), True),
    "vrr": FeatureSpec(VRR, (("jq", "enable_vrr"), ("niri", "enable_vrr")), True),
    "scx": FeatureSpec(SCXScheduler, (("scxctl", "enable_scx"),), True),
    "audio": FeatureSpec(AudioPriority, (), True),
    "inhibit": FeatureSpec(
        ScreenInhibit,
        (
            ("dbus-send", "enable_inhibit"),
            ("systemd-inhibit", "enable_sleep_inhibit"),
            ("dms", "enable_inhibit"),
        ),
        True,
    ),
    "steam": FeatureSpec(None, (), True),
}


def default_toggle_string() -> str:
    """Comma-joined default TOGGLE_FEATURES, derived from the registry."""
    return ",".join(name for name, spec in FEATURES.items() if spec.default_toggle)
