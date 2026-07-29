"""Smoketest: verify evdev-based KB&M idle polling works on this system.

Run directly:  python3 tests/smoketest_evdev_idle.py

Opens keyboard/mouse/touchpad/touchscreen event devices, polls for activity
via select(), and reports idle time.  Press a key or move the mouse to see
the counter reset.

This validates that:
  1. We can classify and open only KB&M+touch input devices
  2. select() detects input activity
  3. Non-input devices (joysticks, tablets, accelerometers) are correctly excluded
  4. Idle time tracking works independently of DMS/logind
"""

from __future__ import annotations

import os
import select
import struct
import subprocess
import sys
import time

POLL_INTERVAL = 1.0
INPUT_EVENT_FORMAT = "llHHi"
INPUT_EVENT_SIZE = struct.calcsize(INPUT_EVENT_FORMAT)

# Event types (from linux/input-event-codes.h)
EV_KEY = 1
EV_REL = 2
EV_ABS = 3
EV_LED = 17
EV_FF = 21

# Relative axes
REL_X = 0

# Button / tool codes
BTN_MOUSE = 0x110
BTN_TOUCH = 0x14A
BTN_TOOL_FINGER = 0x145
BTN_STYLUS = 0x14B
BTN_TOOL_PEN = 0x140

# INPUT_PROP_* bit positions (from linux/input.h)
INPUT_PROP_POINTER = 0x00
INPUT_PROP_DIRECT = 0x01
INPUT_PROP_POINTING_STICK = 0x05
INPUT_PROP_ACCELEROMETER = 0x06

# Steam Controller vendor ID (Valve)
STEAM_VID = 0x28DE

REL_NOISE_THRESHOLD = 3

# Device types that indicate active user presence
ACCEPTED_TYPES = frozenset({"keyboard", "mouse", "touchpad", "touchscreen"})


# -- sysfs helpers ---------------------------------------------------------


def _read_bitmap(path: str) -> list[int] | None:
    try:
        with open(path) as f:
            return [int(x, 16) for x in f.read().strip().split()]
    except (FileNotFoundError, OSError, ValueError):
        return None


def _has_bit(words: list[int], bit: int) -> bool:
    idx = bit // 64
    offset = bit % 64
    return idx < len(words) and bool(words[idx] & (1 << offset))


def _read_sysfs_str(event_path: str, attr: str) -> str | None:
    """Read a string attribute from a device's sysfs path."""
    devname = os.path.basename(event_path)
    path = f"/sys/class/input/{devname}/device/{attr}"
    try:
        with open(path) as f:
            return f.read().strip()
    except (FileNotFoundError, OSError):
        return None


def _read_sysfs_hex(event_path: str, attr: str) -> int | None:
    """Read a hex value from a device's sysfs id/ directory."""
    val = _read_sysfs_str(event_path, attr)
    if val is not None:
        try:
            return int(val, 16)
        except ValueError:
            return None
    return None


def _device_name(event_path: str) -> str | None:
    return _read_sysfs_str(event_path, "name")


def _device_vid(event_path: str) -> int | None:
    return _read_sysfs_hex(event_path, "id/vendor")


def _device_pid(event_path: str) -> int | None:
    return _read_sysfs_hex(event_path, "id/product")


# -- classification ---------------------------------------------------------


def classify_via_udevadm(event_path: str) -> str | None:
    """Use udevadm to classify a device.

    Returns: 'keyboard', 'mouse', 'touchpad', 'touchscreen', or None.
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
        return "keyboard"
    if props.get("ID_INPUT_MOUSE") == "1":
        return "mouse"
    if props.get("ID_INPUT_TOUCHPAD") == "1":
        return "touchpad"
    if props.get("ID_INPUT_TOUCHSCREEN") == "1":
        return "touchscreen"
    return None


def classify_via_sysfs(event_path: str) -> str | None:
    """Fallback: classify using sysfs capabilities + device properties.

    Returns: 'keyboard', 'mouse', 'touchpad', 'touchscreen', or None.
    """
    devname = os.path.basename(event_path)
    base = f"/sys/class/input/{devname}/device/capabilities"
    prop_path = f"/sys/class/input/{devname}/device/properties"

    ev = _read_bitmap(f"{base}/ev")
    if not ev:
        return None

    key = _read_bitmap(f"{base}/key")
    rel = _read_bitmap(f"{base}/rel")
    prop = _read_bitmap(prop_path)

    has_key = _has_bit(ev, EV_KEY)
    has_rel = _has_bit(ev, EV_REL)
    has_abs = _has_bit(ev, EV_ABS)
    has_ff = _has_bit(ev, EV_FF)

    # Joystick with force feedback
    if has_ff:
        return None

    # Accelerometer
    if prop and _has_bit(prop, INPUT_PROP_ACCELEROMETER):
        return None

    # Tablet (stylus/pen input)
    if key and (_has_bit(key, BTN_STYLUS) or _has_bit(key, BTN_TOOL_PEN)):
        return None

    # Touchscreen: absolute coords + (BTN_TOUCH or INPUT_PROP_DIRECT) + no finger tool
    if (
        has_abs
        and key
        and _has_bit(key, BTN_TOUCH)
        and not _has_bit(key, BTN_TOOL_FINGER)
    ):
        return "touchscreen"
    if (
        has_abs
        and prop
        and _has_bit(prop, INPUT_PROP_DIRECT)
        and not (key and _has_bit(key, BTN_TOOL_FINGER))
    ):
        return "touchscreen"

    # Touchpad: absolute coords + BTN_TOOL_FINGER
    if has_abs and key and _has_bit(key, BTN_TOOL_FINGER):
        return "touchpad"
    if (
        has_abs
        and prop
        and _has_bit(prop, INPUT_PROP_POINTER)
        and not (prop and _has_bit(prop, INPUT_PROP_DIRECT))
    ):
        return "touchpad"

    # Mouse: relative coords + mouse button or relative motion
    if has_rel and rel and _has_bit(rel, REL_X):
        return "mouse"
    if has_rel and key and _has_bit(key, BTN_MOUSE):
        return "mouse"

    # Keyboard: EV_KEY without EV_REL, EV_ABS, EV_FF, and has EV_LED
    # (EV_LED indicates keyboard LED support — present on all real keyboards,
    #  absent on power buttons, media controls, and Steam Controller lizard-mode)
    if has_key and not has_rel and not has_abs:
        if _has_bit(ev, EV_LED):
            return "keyboard"
        return None

    return None


def _is_steam_controller(event_path: str) -> bool:
    """Check if a device is a Steam Controller in lizard mode (VID=Valve)."""
    vendor = _device_vid(event_path)
    return vendor == STEAM_VID


def _classify_device(full: str) -> tuple[str | None, str | None]:
    """Classify device using udevadm first, sysfs as fallback.

    Returns: (type, exclusion_reason)
    """
    cls = classify_via_udevadm(full)
    if cls is None:
        cls = classify_via_sysfs(full)

    if cls is not None and _is_steam_controller(full):
        return (None, f"Steam Controller (VID=0x{STEAM_VID:04X}), excluded")

    return (cls, None)


# -- activity filtering -----------------------------------------------------


def is_meaningful_activity(data: bytes) -> bool:
    """Check if evdev data represents meaningful KB&M activity.

    Filters out EV_SYN and EV_MSC to avoid false positives from
    devices that emit periodic sync or calibration messages.
    """
    for i in range(0, len(data), INPUT_EVENT_SIZE):
        chunk = data[i : i + INPUT_EVENT_SIZE]
        if len(chunk) != INPUT_EVENT_SIZE:
            break
        _sec, _usec, ev_type, _code, value = struct.unpack(INPUT_EVENT_FORMAT, chunk)
        if ev_type == EV_KEY and value > 0:
            return True
        if ev_type == EV_REL and abs(value) > REL_NOISE_THRESHOLD:
            return True
        if ev_type == EV_ABS:
            return True
    return False


# -- main -------------------------------------------------------------------


def _vid_str(event_path: str) -> str:
    vid = _device_vid(event_path)
    pid = _device_pid(event_path)
    if vid is not None and pid is not None:
        return f"{vid:04x}:{pid:04x}"
    return "-"


def _ev_led_str(event_path: str) -> str:
    devname = os.path.basename(event_path)
    ev = _read_bitmap(f"/sys/class/input/{devname}/device/capabilities/ev")
    if ev and _has_bit(ev, EV_LED):
        return "LED"
    return "-"


def _note_for(
    exclusion: str | None, udev_type: str | None, sysfs_type: str | None
) -> str:
    if exclusion:
        return exclusion
    if udev_type is None and sysfs_type is None:
        return "unclassified"
    return ""


def main() -> int:
    print("=== Evdev KB&M Idle Polling Smoketest ===")
    print()

    devices: list[tuple[str, str | None, str | None, str | None, str | None]] = []
    for path in sorted(os.listdir("/dev/input")):
        full = f"/dev/input/{path}"
        if not path.startswith("event"):
            continue
        udev_type = classify_via_udevadm(full)
        sysfs_type = classify_via_sysfs(full)
        final_type, exclusion = _classify_device(full)
        devices.append((path, udev_type, sysfs_type, final_type, exclusion))

    # Per-device classification table
    header = f"{'Device':<10} {'Name':<38} {'VID:PID':<13} {'LED':<5} {'Type':<12} Note"
    print(header)
    print("-" * len(header))
    for dev, udev, sysfs, final, exclusion in devices:
        name = _device_name(f"/dev/input/{dev}") or "-"
        vidpid = _vid_str(f"/dev/input/{dev}")
        led = _ev_led_str(f"/dev/input/{dev}")
        type_str = final if final else "-"
        note = _note_for(exclusion, udev, sysfs)

        # Shorten name for display
        if len(name) > 36:
            name = name[:33] + "..."

        print(f"{dev:<10} {name:<38} {vidpid:<13} {led:<5} {type_str:<12} {note}")

    print()
    unknown = sum(1 for _d, _u, _s, f, _e in devices if f is None)
    print(
        f"Total devices: {len(devices)}  |  Classified: {len(devices) - unknown}  |  Unclassified: {unknown}"
    )
    print()

    # Summary by type
    type_counts: dict[str, int] = {}
    excluded_steam = 0
    for _d, _u, _s, final, exclusion in devices:
        if exclusion and "Steam Controller" in (exclusion or ""):
            excluded_steam += 1
        if final:
            type_counts[final] = type_counts.get(final, 0) + 1

    if type_counts:
        print("Classification summary:")
        for t in ["keyboard", "mouse", "touchpad", "touchscreen"]:
            count = type_counts.get(t, 0)
            print(f"  {t}: {count}")
        if excluded_steam:
            print(f"  Steam Controller (excluded): {excluded_steam}")
        print()

    # Collect accepted devices
    fds: list[int] = []
    fd_names: dict[int, str] = {}
    fd_types: dict[int, str] = {}
    perm_errors = 0
    classified_count = sum(type_counts.values())
    for path, _udev, _sysfs, final_type, _exclusion in devices:
        full = f"/dev/input/{path}"
        if final_type not in ACCEPTED_TYPES:
            continue
        try:
            fd = os.open(full, os.O_RDONLY | os.O_NONBLOCK)
            fds.append(fd)
            fd_names[fd] = path
            fd_types[fd] = final_type
        except PermissionError:
            perm_errors += 1

    print(f"Tracking {len(fds)} user-input devices ({len(type_counts)} types)")
    for fd in fds:
        print(f"  /dev/input/{fd_names[fd]:8}  ({fd_types[fd]})")
    skipped = len(devices) - sum(type_counts.values())
    print(f"Skipped {skipped} non-input devices")
    print()

    if not fds:
        if classified_count > 0 and perm_errors == classified_count:
            msg = (
                "ERROR: No evdev devices accessible — you lack 'input' group membership.\n"
                "       Try:  sudo usermod -aG input $USER  (then log out and back in)"
            )
        else:
            msg = "ERROR: No user-input devices found."
        print(msg, file=sys.stderr)
        return 1

    last_activity = time.monotonic()

    # Drain any stale data queued before we start tracking idle
    for fd in fds:
        try:
            while os.read(fd, 4096):
                pass
        except (BlockingIOError, OSError):
            pass

    print("Watching for input... (press a key or move the mouse)")
    print("  Ctrl+C to exit")
    print()

    try:
        while True:
            r, _, _ = select.select(fds, [], [], POLL_INTERVAL)
            now = time.monotonic()

            if r:
                for fd in r:
                    try:
                        data = os.read(fd, 4096)
                        if is_meaningful_activity(data):
                            last_activity = now
                            print(
                                f"  [ACTIVITY]  {fd_names[fd]:8}  "
                                f"({fd_types[fd]})  "
                                f"(idle timer reset)                  "
                            )
                    except OSError:
                        pass

            idle = now - last_activity
            if idle >= 3.0:
                print(
                    f"  [IDLE {idle:.0f}s]  no input received                ", end="\r"
                )

    except KeyboardInterrupt:
        print()
        print()
        print("Exiting.")
        return 0
    finally:
        for fd in fds:
            try:
                os.close(fd)
            except OSError:
                pass


if __name__ == "__main__":
    sys.exit(main())
