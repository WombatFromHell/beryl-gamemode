"""Negotiate a shell invocation that can resolve shell functions.

Verified by tests/smoketest_shell_function.py:
  fish — fresh `fish -c` auto-loads config, so a plain spawn works.
  bash — non-interactive `bash -c` sources $BASH_ENV before the command.
  zsh  — non-interactive `zsh -c` always sources .zshenv (ZDOTDIR defaults
         to $HOME); no env manipulation needed.
  sh   — no function export, no startup-file mechanism; no fallback.
"""

from __future__ import annotations

import os
import shlex
import shutil
from collections.abc import Mapping


def shell_fallback(
    command: list[str], env: Mapping[str, str] | None = None
) -> tuple[list[str], dict[str, str]] | None:
    """Return (argv, extra_env) that can resolve shell functions, or None.

    Pure: reads only *env* (defaults to os.environ) and the filesystem for
    the bash ~/.bashrc check. The command line is joined with shlex so the
    spawned shell sees the same words.

    fish and zsh only pick up rc-defined functions in interactive mode
    (fish -c loads no rc files at all; zsh -c reads only .zshenv), so they
    run with -i. bash sources $BASH_ENV non-interactively, so -c suffices.
    """
    env = os.environ if env is None else env
    shell = os.path.basename(env.get("SHELL", ""))
    if shell not in ("bash", "zsh", "fish") or shutil.which(shell) is None:
        return None
    if shell == "bash":
        bashrc = os.path.expanduser("~/.bashrc")
        if not os.path.isfile(bashrc):
            return None
        return [env["SHELL"], "-c", shlex.join(command)], {"BASH_ENV": bashrc}
    return [env["SHELL"], "-ic", shlex.join(command)], {}
