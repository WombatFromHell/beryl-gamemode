"""Smoketest: shell functions are invisible to spawned processes; verify workarounds.

Run directly:  python3 tests/smoketest_shell_function.py

Root cause under test (gamemode -- yy fails with 127):
  A shell function is an in-memory definition of the shell process that
  defines it. It is not a file on PATH, so ANY exec/env/Popen done by a
  child of that shell cannot resolve it by name.

The test:
  1. Detects $SHELL and reports which workaround applies to it.
  2. For every installed shell (bash, sh, zsh, fish), reproduces the bug
     (scenario A, must fail) and then negotiates + verifies the
     per-shell workaround (scenario W, must pass where one exists).

Workaround table (verified empirically):
  bash  'bash -c fn' with BASH_ENV -> fn's definition file
        (non-interactive bash sources $BASH_ENV before -c).
        Also: parent can 'export -f fn' -> 'bash -c fn' inherits it via
        BASH_FUNC_fn%% — but modern GNU env (coreutils 9.x, verified
        9.11) will NOT execute it, so any 'env fn' stage still fails.
  zsh   'zsh -ic fn' with ZDOTDIR -> dir containing .zshrc with the fn
        (zsh -c alone reads only .zshenv; -i adds .zshrc, where functions
        actually live in practice).
  fish  'fish -ic fn' (fish -c loads no rc files at all; -i loads
        config.fish / functions dir; fish has no separate login file).
  sh    none — no function export, no startup-file mechanism.

Exit code 0 iff scenario A fails for every shell and every negotiable
workaround passes.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

FN = "gm-smoke-fn"

DEFS: dict[str, str] = {
    "bash": f"function {FN}() {{ echo FN-RAN; }}",
    "sh": f"{FN}() {{ echo FN-RAN; }}",
    "zsh": f"{FN}() {{ echo FN-RAN; }}",
    "fish": f"function {FN}; echo FN-RAN; end",
}

# Child: mimic gamemode's Popen(["fn"]) — plain execvp resolution, no shell.
CHILD_PY = """\
import subprocess, sys
try:
    sys.exit(subprocess.run(sys.argv[1:]).returncode)
except FileNotFoundError:
    print("FILE-NOT-FOUND")
    sys.exit(127)
"""


def run(cmd: list[str], env: dict[str, str] | None = None) -> tuple[int, str]:
    full = dict(os.environ)
    if env:
        full.update(env)
    try:
        p = subprocess.run(
            cmd, capture_output=True, text=True, timeout=10, env=full, check=False
        )
        return p.returncode, (p.stdout + p.stderr).strip().replace("\n", " | ")
    except FileNotFoundError:
        return 127, "shell not installed"


class Report:
    def __init__(self) -> None:
        self.failed: list[str] = []

    def check(self, name: str, rc: int, expect: int) -> bool:
        ok = (rc == 0) == (expect == 0)
        if not ok:
            self.failed.append(name)
        print(f"  [{'PASS' if ok else 'FAIL'}] {name} (rc={rc})")
        return ok


def bug_repro(report: Report, sh: str, child: str) -> None:
    """A: fn defined in parent only; child execs it directly (must fail)."""
    rc, out = run([sh, "-c", f"{DEFS[sh]}; exec python3 {child} {FN}"])
    report.check(f"{sh} A: fn invisible to child exec (the bug)", rc, 127)
    print(f"       detail: {out}")


def bash_extra(report: Report, child: str) -> None:
    """B: exported bash fn — bash -c sees it, env does not (coreutils 9.x)."""
    for cmd, name, expect in (
        (["bash", "-c", FN], "bash -c fn", 0),
        (["env", FN], "env fn", 127),
    ):
        rc, out = run(
            [
                "bash",
                "-c",
                (
                    f"{DEFS['bash']}; export -f {FN}; exec python3 {child} "
                    f"{' '.join(cmd)}"
                ),
            ],
        )
        report.check(f"bash B: exported fn via {name} (expect rc={expect})", rc, expect)
        print(f"       detail: {out}")


def workaround(report: Report, sh: str, tmp: Path) -> str | None:
    """W: negotiate and verify the per-shell workaround. Returns label."""
    if sh == "bash":
        defs = tmp / "bashrc"
        defs.write_text(DEFS["bash"] + "\n")
        label = f"bash -c fn with BASH_ENV={defs}"
        rc, out = run(["bash", "-c", FN], env={"BASH_ENV": str(defs)})
    elif sh == "zsh":
        zdot = tmp / "zdot"
        zdot.mkdir()
        (zdot / ".zshrc").write_text(DEFS["zsh"] + "\n")
        label = f"zsh -ic fn with ZDOTDIR={zdot} (fn in .zshrc)"
        rc, out = run(["zsh", "-ic", FN], env={"ZDOTDIR": str(zdot)})
    elif sh == "fish":
        fdir = tmp / "xdg" / "fish"
        fdir.mkdir(parents=True)
        (fdir / "config.fish").write_text(DEFS["fish"] + "\n")
        label = "fish -ic fn (fish -c loads no rc files; -i loads config.fish)"
        rc, out = run(["fish", "-ic", FN], env={"XDG_CONFIG_HOME": str(fdir.parent)})
    else:  # sh: no function export, no startup-file mechanism
        return None

    report.check(f"{sh} W: workaround verified — {label}", rc, 0)
    print(f"       detail: {out}")
    return label


def main() -> int:
    report = Report()
    shell_env = os.environ.get("SHELL", "")
    shell_name = Path(shell_env).name if shell_env else "(unset)"
    print(f"$SHELL = {shell_env or '(unset)'} -> {shell_name}")

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        child = str(tmp / "child.py")
        (tmp / "child.py").write_text(CHILD_PY)
        labels: dict[str, str | None] = {}
        for sh in ("bash", "sh", "zsh", "fish"):
            if shutil.which(sh) is None:
                print(f"\n== {sh}: not installed, skipping ==")
                continue
            print(f"\n== {sh} ==")
            bug_repro(report, sh, child)
            if sh == "bash":
                bash_extra(report, child)
            labels[sh] = workaround(report, sh, tmp)

    print()
    if shell_name in labels:
        w = labels[shell_name]
        print(
            f"Verdict for your $SHELL ({shell_name}): "
            + (
                f"workaround = {w}"
                if w
                else "no workaround — fn must be a real executable"
            )
        )
    elif shell_name in ("bash", "sh", "zsh", "fish"):
        print(f"Verdict for your $SHELL: {shell_name} not installed here")
    else:
        print(f"Verdict for your $SHELL ({shell_name}): unsupported shell")
    if report.failed:
        print(f"\nRESULT: {len(report.failed)} unexpected outcome(s): {report.failed}")
        return 1
    print("\nRESULT: root cause confirmed; workarounds verified per shell.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
