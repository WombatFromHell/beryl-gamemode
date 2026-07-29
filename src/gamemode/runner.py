"""Runner abstraction over subprocess for host-executable calls."""

from __future__ import annotations

import logging
import shutil
import subprocess
from typing import Any


class Runner:
    def __init__(self, log: logging.Logger) -> None:
        self._log = log

    def resolve(self, cmd: str) -> str | None:
        return shutil.which(cmd)

    def require(self, cmd: str, feature: str = "") -> bool:
        if self.resolve(cmd) is None:
            if feature:
                self._log.error("%s requires '%s' (not found)", feature, cmd)
            return False
        return True

    def run(
        self,
        args: list[str],
        *,
        check: bool = False,
        capture_output: bool = False,
        text: bool = False,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        self._log.debug("exec: %s", " ".join(args))
        try:
            return subprocess.run(
                args, check=check, capture_output=capture_output, text=text, env=env
            )
        except FileNotFoundError:
            self._log.error("command not found: %s", args[0])
            raise

    def capture(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        return self.run(args, capture_output=True, text=True)

    def pipe(
        self, args: list[str], input_data: str
    ) -> subprocess.CompletedProcess[str]:
        self._log.debug("pipe: %s  (stdin: %d bytes)", " ".join(args), len(input_data))
        return subprocess.run(
            args, input=input_data, capture_output=True, text=True, check=False
        )

    def make_checked_runner(self, cmd: str, feature: str = "") -> CheckedCommandRunner:
        return CheckedCommandRunner(self, cmd, feature)


class CheckedCommandRunner:
    def __init__(
        self,
        runner: Runner,
        cmd: str,
        feature: str = "",
        log: logging.Logger | None = None,
    ) -> None:
        self._runner = runner
        self._cmd = cmd
        self._feature = feature
        self._log = log or runner._log
        self._available = runner.require(cmd, feature)

    @property
    def is_available(self) -> bool:
        return self._available

    def run_or_none(
        self, args: list[str], **kwargs: Any
    ) -> subprocess.CompletedProcess[str] | None:
        if not self._available:
            return None
        result = self._runner.run(args, capture_output=True, text=True, **kwargs)
        if result.returncode != 0:
            self._log.debug(
                "%s: %s returned %d",
                self._feature or self._cmd,
                self._cmd,
                result.returncode,
            )
        return result

    def run_ok(self, args: list[str], **kwargs: Any) -> bool:
        """Return True if the command ran successfully (returncode == 0)."""
        result = self.run_or_none(args, **kwargs)
        return result is not None and result.returncode == 0
