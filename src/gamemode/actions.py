"""Action implementations for gamemode."""

from __future__ import annotations

import collections.abc
import ctypes
import ctypes.util
import logging
import os
import shutil
import signal
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from gamemode.compositor import compositor_is_niri, output_resolve, session_is_kde
from gamemode.config import Config
from gamemode.feature import _BaseFeature
from gamemode.features.wrappers import WRAPPER_FACTORIES, WrapperChain
from gamemode.orchestration import collect_features, features_disable, features_enable
from gamemode.runner import Runner
from gamemode.shell_fallback import shell_fallback
from gamemode.state import StateManager


def _negotiate_command(
    command: list[str], log: logging.Logger
) -> tuple[list[str], dict[str, str]]:
    """Resolve *command* to an argv that exec can run.

    Shell functions live in the parent shell's memory and are invisible to
    PATH resolution, so a command that isn't an executable is rerouted
    through $SHELL when a mechanism exists (see shell_fallback).
    Returns (argv, extra_env); extra_env is Popen-scoped only.
    """
    if "/" in command[0] or shutil.which(command[0]) is not None:
        return command, {}
    fallback = shell_fallback(command)
    if fallback is None:
        log.warning(
            "Command '%s' not found in PATH and no shell-function fallback "
            "for SHELL=%s — attempting as-is (wrap it in a real executable "
            "to make it work everywhere)",
            command[0],
            os.environ.get("SHELL", "<unset>"),
        )
        return command, {}
    argv, extra_env = fallback
    log.info(
        "Command '%s' not in PATH — shell-function fallback: %s",
        command[0],
        " ".join(argv),
    )
    return argv, extra_env


def _prepare_base(config: Config, log: logging.Logger) -> StateManager:
    """Shared setup for all action entry points."""
    state = StateManager(config)
    state.init()
    return state


def _prepare_action(
    config: Config, runner: Runner, log: logging.Logger
) -> tuple[collections.abc.Sequence[tuple[str, _BaseFeature]], StateManager]:
    state = _prepare_base(config, log)
    features = collect_features(config, runner, log)
    return features, state


def action_on(config: Config, runner: Runner, log: logging.Logger) -> int:
    output = output_resolve(config)
    features, state = _prepare_action(config, runner, log)
    log.info("Activating (output: %s)", output)
    if state.is_wrapper:
        log.debug("Wrapper mode active, skipping on")
        return 0
    if state.is_active:
        log.info("Already active (idempotent)")
        return 0
    state.mark_active()
    features_enable(features, log)
    log.info("Activation complete")
    return 0


def action_off(
    config: Config,
    runner: Runner,
    log: logging.Logger,
) -> int:
    features, state = _prepare_action(config, runner, log)
    features_disable(features, log)
    state.clear()
    log.info("Cleanup complete")
    return 0


def _build_status_lines(config: Config, state: StateManager) -> list[str]:
    mode = state.mode
    pid = state.pid()
    cmd = state.cmd()
    niri = compositor_is_niri()
    kde = session_is_kde()
    session = os.environ.get("XDG_SESSION_DESKTOP", "(unset)")
    current_desktop = os.environ.get("XDG_CURRENT_DESKTOP", "(unset)")
    output = output_resolve(config)
    if mode == "wrapper" and pid is not None:
        alive = state.pid_alive()
        stale = not alive
    else:
        alive = stale = None
    return [
        f"Mode:             {mode or '(none)'}",
        f"PID:              {pid if pid is not None else 'N/A (toggle mode)'}",
        f"Alive:            {alive if alive is not None else 'N/A'}",
        f"Stale:            {stale if stale is not None else 'N/A'}",
        "",
        f"Command:          {' '.join(cmd) if cmd else '(toggle mode)'}",
        "",
        f"Compositor:       {'niri' if niri else 'kde' if kde else 'unknown'}",
        f"  XDG_SESSION_DESKTOP:    {session}",
        f"  XDG_CURRENT_DESKTOP:    {current_desktop}",
        f"Target output:    {output}",
        "",
        f"State dir:        {config.state_dir}",
    ]


def action_status(config: Config) -> int:
    state = StateManager(config)
    state.init()
    print("\n".join(_build_status_lines(config, state)))
    return 0


def _build_cleanup_closure(
    features: collections.abc.Sequence[tuple[str, _BaseFeature]],
    log: logging.Logger,
    state: StateManager,
    *,
    preserve_state: bool = False,
):
    _done = False

    def _cleanup() -> None:
        nonlocal _done
        if _done:
            return
        _done = True
        try:
            features_disable(features, log)
            if not preserve_state:
                state.clear()
        except Exception:
            log.exception("Error during cleanup")

    return _cleanup


@contextmanager
def _signal_guard(
    log: logging.Logger, child_proc: list[subprocess.Popen | None]
) -> Iterator[int]:
    pending_signal = [0]
    _orig_handlers: dict[int, Any] = {}

    def _handler(signum: int, _frame: object) -> None:
        log.info("Received signal %s, terminating child and cleaning up", signum)
        pending_signal[0] = signum
        proc = child_proc[0]
        if proc is None:
            return
        try:
            proc.kill()
        except OSError:
            pass

    signals_to_hook = [signal.SIGTERM, signal.SIGINT]
    if hasattr(signal, "SIGHUP"):
        signals_to_hook.append(signal.SIGHUP)

    for sig in signals_to_hook:
        try:
            _orig_handlers[sig] = signal.signal(sig, _handler)
        except (ValueError, OSError):
            pass

    try:
        yield pending_signal[0]
    finally:
        for sig, orig in _orig_handlers.items():
            try:
                signal.signal(sig, orig)
            except (ValueError, OSError):
                pass


def _watch_parent(log: logging.Logger) -> None:
    PR_SET_PDEATHSIG = 1
    libc_path = ctypes.util.find_library("c")
    if libc_path is None:
        return
    try:
        libc = ctypes.CDLL(libc_path)
        ret = libc.prctl(PR_SET_PDEATHSIG, signal.SIGTERM)
        if ret != 0:
            log.warning("prctl(PR_SET_PDEATHSIG) failed: %s", os.strerror(-ret))
    except (OSError, AttributeError) as exc:
        log.warning("prctl unavailable for parent-death detection: %s", exc)


def _run_child(
    exec_cmd: list[str],
    log: logging.Logger,
    cleanup: collections.abc.Callable[[], None],
    env: dict[str, str] | None = None,
) -> int:
    child_proc: list[subprocess.Popen | None] = [None]
    popen_env = {**os.environ, **env} if env else None
    try:
        child_proc[0] = subprocess.Popen(
            exec_cmd, start_new_session=True, env=popen_env
        )
    except OSError as exc:
        log.error("Failed to execute command: %s", exc)
        cleanup()
        return 1

    with _signal_guard(log, child_proc) as pending_signal:
        retcode = child_proc[0].wait()
    cleanup()
    if pending_signal:
        sys.exit(128 + pending_signal)
    return retcode


def action_wrapper(
    config: Config,
    runner: Runner,
    log: logging.Logger,
    command: list[str],
) -> int:
    output = output_resolve(config)
    state = _prepare_base(config, log)
    log.info("Wrapper mode (output: %s, command: %s)", output, " ".join(command))
    argv, extra_env = _negotiate_command(command, log)
    _watch_parent(log)

    with state.locked() as acquired:
        if not acquired:
            log.warning(
                "Prior gamemode session holds the lock — WrapperChain disabled, passing through command without wrappers"
            )
            cleanup = _build_cleanup_closure([], log, state, preserve_state=True)
            return _run_child(argv, log, cleanup, extra_env)

        # Stale wrapper state (pid dead) should not block a new wrapper.
        if state.is_wrapper and state.pid() is not None and not state.pid_alive():
            log.info(
                "Stale wrapper state detected (pid %s not alive), clearing", state.pid()
            )
            state.clear()

        already_active = state.is_active or state.is_wrapper
        if already_active:
            log.warning(
                "Prior gamemode session active (%s) — WrapperChain disabled, passing through command without wrappers",
                state.mode,
            )
            cleanup = _build_cleanup_closure([], log, state, preserve_state=True)
            return _run_child(argv, log, cleanup, extra_env)

        state.mark_wrapper(command)
        features = collect_features(config, runner, log)
        features_enable(features, log)

        cleanup = _build_cleanup_closure(features, log, state, preserve_state=False)

        chain = WrapperChain()
        for name, factory in WRAPPER_FACTORIES.items():
            if name in config.wrapper_features:
                chain.add_factory(factory, config, runner, log)

        exec_cmd = chain.apply(argv)
        return _run_child(exec_cmd, log, cleanup, extra_env)
