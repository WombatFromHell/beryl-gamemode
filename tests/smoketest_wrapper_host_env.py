"""Smoketest: the wrapper's exec_cmd depends on the HOST's binaries.

Run directly:  python3 tests/smoketest_wrapper_host_env.py

Root cause of the CI-only flake (rc=1, then -15/-2, on ubuntu-26.04):

  action_wrapper wraps the command with every feature in WRAPPER_FEATURES
  (default: systemd_run, steam, inhibit). The pytest child script built its
  Config with only some enable_* flags off, so it still inherited the defaults
  enable_systemd_run=True and enable_sleep_inhibit=True. On a host with a
  systemd user session the exec_cmd became:

      systemd-run --user --scope ... -- systemd-inhibit ... -- /bin/sleep 60

  That chain works on a developer box (verified: 3s, rc=0) and dies within
  milliseconds on the Actions runner:

      Failed to inhibit: Access denied as the requested operation requires
      interactive authentication.

  The wrapped child was therefore gone before the test could signal the
  wrapper: the exit code came back as 1, or as -signum when the signal landed
  after action_wrapper had already returned and restored the default
  dispositions. Locally the same tests passed, because the developer box lets
  systemd-inhibit authenticate.

The test:
  A. host-like: the runner claims systemd-inhibit exists and it fails, so the
     wrapped child dies fast and the wrapper returns non-zero without ever
     running the command.
  B. pinned: every wrapper feature disabled, so exec_cmd is exactly the
     command and the command actually runs to completion.

Exit 0 iff A reproduces the CI failure and B is immune to it. The signal half
of the contract is covered deterministically by
tests/test_actions.py::test_signal_exit_code_is_child_wait_status.
"""

import logging
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from gamemode import actions
from gamemode.actions import action_wrapper
from gamemode.config import Config
from gamemode.feature import FeatureResult
from gamemode.runner import Runner

LOG = logging.getLogger("smoketest")


class _Noop:
    """Duck-typed feature: features_enable/disable only call enable/disable."""

    name = "noop"

    def enable(self) -> FeatureResult:
        return FeatureResult.noop()

    def disable(self) -> FeatureResult:
        return FeatureResult.noop()


NO_FEATURES = [("noop", _Noop())]


class SpyRunner(Runner):
    """Claims wrapper binaries exist at *paths* and records what gets spawned."""

    def __init__(self, paths: dict[str, str]):
        super().__init__(LOG)
        self._paths = paths
        self.spawned: list[list[str]] = []

    def resolve(self, cmd: str) -> str | None:
        return self._paths.get(cmd)

    def spawn(self, args, env=None, *, shell=False, start_new_session=False):
        self.spawned.append(list(args))
        return super().spawn(
            args, env=env, shell=shell, start_new_session=start_new_session
        )


def _cfg(tmp: Path, **overrides: Any) -> Config:
    """Features off; *overrides* re-enable the host coupling on purpose."""
    # Any: a heterogeneous kwargs dict has no per-key type to check against
    # Config's fields, and the splat is the whole point of this helper.
    base: dict[str, Any] = {
        "enable_scx": False,
        "enable_vrr": False,
        "enable_tuned": False,
        "enable_inhibit": False,
        "enable_audio": False,
        "enable_steam": False,
        "enable_idle_monitor": False,
        "runtime_dir": str(tmp),
    }
    base.update(overrides)
    return Config(**base)


def _run(cfg: Config, runner: SpyRunner, command: list[str]) -> tuple[int, float]:
    """Run the wrapper, returning (exit code, seconds)."""
    started = time.monotonic()
    with patch.object(actions, "collect_features", return_value=NO_FEATURES):
        rc = action_wrapper(cfg, runner, LOG, command)
    return rc, time.monotonic() - started


def _detail(runner: SpyRunner, rc: int, elapsed: float) -> str:
    cmd = " ".join(runner.spawned[0]) if runner.spawned else "(never spawned)"
    return f"rc={rc} elapsed={elapsed:.2f}s exec_cmd={cmd}"


def scenario_a(tmp: Path) -> tuple[bool, str]:
    """Host-like: systemd-inhibit is present and rejects the request."""
    stub = tmp / "systemd-inhibit-stub"
    # Mimics the runner, including the message CI showed.
    stub.write_text('#!/bin/sh\necho "Failed to inhibit: Access denied" >&2\nexit 1\n')
    stub.chmod(0o755)

    cfg = _cfg(tmp, enable_sleep_inhibit=True, wrapper_features={"inhibit"})
    runner = SpyRunner({"systemd-inhibit": str(stub)})
    rc, elapsed = _run(cfg, runner, ["/bin/sleep", "5"])

    # Reproduced: the command was replaced and died fast, so no test could
    # ever signal a live child.
    ok = rc == 1 and elapsed < 2 and runner.spawned[0] != ["/bin/sleep", "5"]
    return ok, _detail(runner, rc, elapsed)


def scenario_b(tmp: Path) -> tuple[bool, str]:
    """Pinned: no wrapper features, so exec_cmd is the command and it runs.

    The runner resolves the real systemd binaries, so this is the CI host's
    shape (util-linux present, features would apply) with the pin in place.
    """
    cfg = _cfg(
        tmp,
        enable_sleep_inhibit=False,
        enable_systemd_run=False,
        wrapper_features=set(),
    )
    runner = SpyRunner(
        {
            name: path
            for name in ("systemd-inhibit", "systemd-run")
            if (path := shutil.which(name))
        }
    )
    rc, elapsed = _run(cfg, runner, ["/bin/sleep", "1"])

    ok = rc == 0 and runner.spawned == [["/bin/sleep", "1"]] and elapsed >= 1
    return ok, f"{_detail(runner, rc, elapsed)} (resolved: {sorted(runner._paths)})"


def main() -> int:
    results = []
    with tempfile.TemporaryDirectory(prefix="gm-smoketest-") as d:
        tmp = Path(d)
        results.append(("A host-like (must reproduce)", *scenario_a(tmp)))
        results.append(("B pinned (must be immune)", *scenario_b(tmp)))

    print(f"{'scenario':<32} {'result':<6} detail")
    print("-" * 92)
    for name, ok, detail in results:
        print(f"{name:<32} {'PASS' if ok else 'FAIL':<6} {detail}")

    print()
    print("A reproducing and B immune means: a spawned test child must pin")
    print("enable_systemd_run / enable_sleep_inhibit / wrapper_features, or")
    print("its exit code is whatever the host's systemd decides.")
    return 0 if all(ok for _, ok, _ in results) else 1


if __name__ == "__main__":
    sys.exit(main())
