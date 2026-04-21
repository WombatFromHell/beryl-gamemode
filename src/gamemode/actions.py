"""Action implementations for gamemode."""

from __future__ import annotations

import ctypes
import ctypes.util
import logging
import os
import signal
import subprocess
import sys
from contextlib import contextmanager
from typing import Iterator

from gamemode.compositor import compositor_is_niri, output_resolve, session_is_kde
from gamemode.config import Config
from gamemode.feature import Feature
from gamemode.features import WRAPPER_FACTORIES, WrapperChain
from gamemode.logging_setup import setup_logging
from gamemode.orchestration import collect_features, features_disable, features_enable
from gamemode.runner import Runner
from gamemode.state import StateManager


def _prepare_action(
    config: Config, runner: Runner, log: logging.Logger, *, debug: bool = False
) -> tuple[str, list[tuple[str, Feature]], StateManager]:
    output = output_resolve(config)
    state = StateManager(config)
    state.init()
    setup_logging(config, to_file=debug)
    features = collect_features(config, runner, log)
    return output, features, state


def action_on(
    config: Config, runner: Runner, log: logging.Logger, *, debug: bool = False
) -> int:
    output, features, state = _prepare_action(config, runner, log, debug=debug)
    log.info("Activating (output: %s)", output)
    if state.is_wrapper:
        log.debug("Wrapper mode active, skipping on")
        return 0
    if state.is_active:
        log.info("Already active (idempotent)")
        return 0
    state.mark_active()
    features_enable(features, output, log)
    log.info("Activation complete")
    return 0


def action_off(
    config: Config,
    runner: Runner,
    log: logging.Logger,
    *,
    debug: bool = False,
) -> int:
    output, features, state = _prepare_action(config, runner, log, debug=debug)
    features_disable(features, output, log)
    state.clear()
    log.info("Cleanup complete")
    return 0


def action_status(config: Config) -> int:
    state = StateManager(config)
    state.init()
    mode = state.mode
    pid = state.pid()
    cmd = state.cmd()
    niri = compositor_is_niri()
    kde = session_is_kde()
    session = os.environ.get("XDG_SESSION_DESKTOP", "(unset)")
    current_desktop = os.environ.get("XDG_CURRENT_DESKTOP", "(unset)")
    output = output_resolve(config)
    stale: bool | None = None
    alive: bool | None = None
    if mode == "wrapper" and pid is not None:
        alive = state.pid_alive()
        stale = not alive
    lines = [
        f"State:            {mode or '(none)'}",
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
    print("\n".join(lines))
    return 0


def _build_cleanup_closure(
    features: list[tuple[str, Feature]],
    output: str,
    log: logging.Logger,
    state: StateManager,
    *,
    preserve_state: bool = False,
):
    _done = [False]

    def _cleanup() -> None:
        if _done[0]:
            return
        _done[0] = True
        try:
            features_disable(features, output, log)
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
    _orig_term = signal.getsignal(signal.SIGTERM)
    _orig_int = signal.getsignal(signal.SIGINT)
    _orig_hup = signal.getsignal(signal.SIGHUP) if hasattr(signal, "SIGHUP") else None

    def _handler(signum: int, _frame: object) -> None:
        log.info("Received signal %s, terminating child and cleaning up", signum)
        pending_signal[0] = signum
        if child_proc[0] is not None:
            try:
                child_proc[0].kill()
            except OSError:
                pass

    try:
        signal.signal(signal.SIGTERM, _handler)
        signal.signal(signal.SIGINT, _handler)
        if hasattr(signal, "SIGHUP"):
            signal.signal(signal.SIGHUP, _handler)
    except (ValueError, OSError):
        pending_signal[0] = 0
        _orig_term = None
        _orig_int = None
        _orig_hup = None
    try:
        yield pending_signal[0]
    finally:
        try:
            signal.signal(signal.SIGTERM, _orig_term)
            signal.signal(signal.SIGINT, _orig_int)
        except (ValueError, OSError):
            pass
        if _orig_hup is not None:
            try:
                signal.signal(signal.SIGHUP, _orig_hup)
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


def action_wrapper(
    config: Config,
    runner: Runner,
    log: logging.Logger,
    command: list[str],
    *,
    debug: bool = False,
) -> int:
    output = output_resolve(config)
    state = StateManager(config)
    state.init()
    log.info("Wrapper mode (output: %s, command: %s)", output, " ".join(command))
    _watch_parent(log)

    with state.locked() as acquired:
        if not acquired:
            log.debug("Another wrapper instance holds the lock, skipping")
            return 0
        setup_logging(config, to_file=debug)

        already_active = state.is_active or state.is_wrapper
        if already_active:
            log.info(
                "Gamemode already active. Wrapper will only apply configured wrappers (feature toggles skipped)."
            )
            features = []
        else:
            state.mark_wrapper(command)
            features = collect_features(config, runner, log)
            features_enable(features, output, log)

        cleanup = _build_cleanup_closure(
            features, output, log, state, preserve_state=already_active
        )

        chain = WrapperChain()
        for name, factory in WRAPPER_FACTORIES.items():
            if name in config.wrapper_features:
                chain.add_factory(factory, config, runner, log)

        exec_cmd = chain.apply(command)
        child_proc: list[subprocess.Popen | None] = [None]
        with _signal_guard(log, child_proc) as pending_signal:
            try:
                child_proc[0] = subprocess.Popen(exec_cmd, start_new_session=True)
                retcode = child_proc[0].wait()
            except OSError as exc:
                log.error("Failed to execute command: %s", exc)
                return 1
            finally:
                cleanup()
        if pending_signal:
            sys.exit(128 + pending_signal)
        return retcode
