"""Smoketest: verify evdev-based KB&M idle polling works on this system.

Run directly:  python3 tests/smoketest_evdev_idle.py

Opens keyboard/mouse/touchpad event devices, polls for activity via select(),
and reports idle time.  Press a key or move the mouse to see the counter reset.

This validates that:
  1. We can classify and open only KB&M devices
  2. select() detects input activity
  3. Joysticks/switches/etc are correctly excluded
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

EV_KEY = 1
EV_REL = 2
EV_ABS = 3


def classify_via_udevadm(event_path: str) -> str | None:
    """Use udevadm to classify a device.

    Returns: 'kbm' for keyboard/mouse/touchpad, or None.
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


_EV_KEY = 1
_EV_REL = 2
_EV_ABS = 3
_EV_FF = 21
_REL_X = 0
_BTN_MOUSE = 0x110
_BTN_TOUCH = 0x14A


def classify_via_sysfs(event_path: str) -> str | None:
    """Fallback: classify using sysfs capabilities.

    Returns: 'kbm' or None.
    """
    devname = os.path.basename(event_path)
    base = f"/sys/class/input/{devname}/device/capabilities"

    ev = _read_bitmap(f"{base}/ev")
    if not ev:
        return None

    has_key = _has_bit(ev, _EV_KEY)
    has_rel = _has_bit(ev, _EV_REL)
    has_abs = _has_bit(ev, _EV_ABS)
    has_ff = _has_bit(ev, _EV_FF)

    # Joystick/gamepad check — force-feedback support → not KB&M
    if has_ff:
        return None

    # Mouse: EV_REL with REL_X, or EV_KEY with BTN_MOUSE
    rel = _read_bitmap(f"{base}/rel")
    if rel and _has_bit(rel, _REL_X):
        return "kbm"

    key = _read_bitmap(f"{base}/key")
    if key and _has_bit(key, _BTN_MOUSE):
        return "kbm"

    # Keyboard: EV_KEY without EV_REL, EV_ABS, EV_FF
    if has_key and not has_rel and not has_abs:
        return "kbm"

    # Touchpad: EV_ABS + EV_KEY with BTN_TOUCH
    if has_abs and has_key and key and _has_bit(key, _BTN_TOUCH):
        return "kbm"

    return None


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
        # Filter REL noise: mice may report tiny movements due to sensor jitter.
        # Only count movement above a minimum threshold (abs > 3).
        if ev_type == EV_REL and abs(value) > 3:
            return True
        if ev_type == EV_ABS:
            return True
    return False


def main() -> int:
    print("=== Evdev KB&M Idle Polling Smoketest ===")
    print()

    fds: list[int] = []
    fd_names: dict[int, str] = {}
    unknown_count = 0

    for path in sorted(os.listdir("/dev/input")):
        full = f"/dev/input/{path}"
        if not path.startswith("event"):
            continue

        cls = classify_via_udevadm(full)
        if cls is None:
            cls = classify_via_sysfs(full)
        if cls is None:
            unknown_count += 1
            continue

        try:
            fd = os.open(full, os.O_RDONLY | os.O_NONBLOCK)
            fds.append(fd)
            fd_names[fd] = path
        except PermissionError:
            pass

    print(f"Tracking {len(fds)} KB&M devices")
    for fd in fds:
        print(f"  /dev/input/{fd_names[fd]}")
    print(f"Skipped {unknown_count} non-KB&M devices")
    print()

    if not fds:
        print("ERROR: No KB&M devices found.", file=sys.stderr)
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
                                f"  [ACTIVITY]  {fd_names[fd]}  "
                                f"(idle timer reset)        "
                            )
                    except OSError:
                        pass

            idle = now - last_activity
            if idle >= 3.0:
                print(f"  [IDLE {idle:.0f}s]  no KB&M input        ", end="\r")

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
