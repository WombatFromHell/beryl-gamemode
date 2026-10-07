"""Input device classification: udevadm / sysfs heuristics for KB&M detection.

Pure module with one public function, ``classify_input`` — testable against a
fake sysfs tree without real ``/dev/input`` devices.
"""

from __future__ import annotations

import os
import subprocess

# ---------------------------------------------------------------------------
# evdev constants (classification only)
# ---------------------------------------------------------------------------

_EV_KEY = 1
_EV_REL = 2
_EV_ABS = 3
_EV_LED = 17
_EV_FF = 21
_REL_X = 0
_BTN_MOUSE = 0x110
_BTN_TOUCH = 0x14A
_BTN_TOOL_FINGER = 0x145
_BTN_STYLUS = 0x14B
_BTN_TOOL_PEN = 0x140

_INPUT_PROP_POINTER = 0x00
_INPUT_PROP_DIRECT = 0x01
_INPUT_PROP_ACCELEROMETER = 0x06

_STEAM_VID = 0x28DE

_SYS_CLASS_INPUT = "/sys/class/input"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_bitmap(path: str) -> list[int] | None:
    """Read a sysfs capability bitmap (space-separated hex words)."""
    try:
        with open(path) as f:
            return [int(x, 16) for x in f.read().strip().split()]
    except (FileNotFoundError, OSError, ValueError):
        return None


def _has_bit(words: list[int], bit: int) -> bool:
    """Test whether *bit* is set in a bitmap stored as uint64[]."""
    idx = bit // 64
    offset = bit % 64
    return idx < len(words) and bool(words[idx] & (1 << offset))


def _classify_via_udevadm(event_path: str) -> str | None:
    """Ask udevadm whether the device is a keyboard, mouse, or touchpad.

    Returns ``"kbm"`` or ``None``.
    """
    try:
        result = subprocess.run(
            ["udevadm", "info", "-q", "property", "-n", event_path],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    props = {}
    for line in result.stdout.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            props[k] = v
    if props.get("ID_INPUT_KEYBOARD") == "1":
        return "kbm"
    if props.get("ID_INPUT_MOUSE") == "1":
        return "kbm"
    if props.get("ID_INPUT_TOUCHPAD") == "1":
        return "kbm"
    return None


def _classify_via_sysfs(event_path: str) -> str | None:
    """Fallback classification by reading sysfs capability bitmaps.

    Used when udevadm is unavailable.
    """
    devname = os.path.basename(event_path)
    base = f"{_SYS_CLASS_INPUT}/{devname}/device/capabilities"
    prop_path = f"{_SYS_CLASS_INPUT}/{devname}/device/properties"

    ev = _read_bitmap(f"{base}/ev")
    if not ev:
        return None

    key = _read_bitmap(f"{base}/key")
    rel = _read_bitmap(f"{base}/rel")
    prop = _read_bitmap(prop_path)

    has_key = _has_bit(ev, _EV_KEY)
    has_rel = _has_bit(ev, _EV_REL)
    has_abs = _has_bit(ev, _EV_ABS)
    has_ff = _has_bit(ev, _EV_FF)

    # Exclude force-feedback devices (e.g. joysticks with rumble).
    if has_ff:
        return None

    # Exclude accelerometers.
    if prop and _has_bit(prop, _INPUT_PROP_ACCELEROMETER):
        return None

    # Exclude pen tablets / stylus digitizers.
    if key and (_has_bit(key, _BTN_STYLUS) or _has_bit(key, _BTN_TOOL_PEN)):
        return None

    # Touchscreens that do NOT have BTN_TOOL_FINGER are KB&M (direct input).
    if (
        has_abs
        and key
        and _has_bit(key, _BTN_TOUCH)
        and not _has_bit(key, _BTN_TOOL_FINGER)
    ):
        return "kbm"
    # Direct-input touch devices without multitouch.
    if (
        has_abs
        and prop
        and _has_bit(prop, _INPUT_PROP_DIRECT)
        and not (key and _has_bit(key, _BTN_TOOL_FINGER))
    ):
        return "kbm"

    # Multitouch touchpads.
    if has_abs and key and _has_bit(key, _BTN_TOOL_FINGER):
        return "kbm"
    # Pointer devices (mice, trackballs) that are direct-input touch.
    if (
        has_abs
        and prop
        and _has_bit(prop, _INPUT_PROP_POINTER)
        and not (prop and _has_bit(prop, _INPUT_PROP_DIRECT))
    ):
        return "kbm"

    # Relative-axis devices with X axis → mice.
    if has_rel and rel and _has_bit(rel, _REL_X):
        return "kbm"
    # Devices with mouse buttons.
    if has_rel and key and _has_bit(key, _BTN_MOUSE):
        return "kbm"

    # Keyboards without relative/absolute axes (have EV_LED for caps/num lock).
    if has_key and not has_rel and not has_abs and _has_bit(ev, _EV_LED):
        return "kbm"

    return None


def _is_steam_controller(event_path: str) -> bool:
    devname = os.path.basename(event_path)
    try:
        with open(f"{_SYS_CLASS_INPUT}/{devname}/device/id/vendor") as f:
            vendor = int(f.read().strip(), 16)
        return vendor == _STEAM_VID
    except (FileNotFoundError, OSError, ValueError):
        return False


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def classify_input(event_path: str) -> str | None:
    """Return ``"kbm"`` if *event_path* is a keyboard, mouse, touchpad, or
    touchscreen; ``None`` otherwise.
    """
    if _is_steam_controller(event_path):
        return None
    cls = _classify_via_udevadm(event_path)
    if cls is None:
        cls = _classify_via_sysfs(event_path)
    return cls
