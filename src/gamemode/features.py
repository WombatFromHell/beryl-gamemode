"""Feature implementations."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from gamemode.compositor import compositor_is_niri
from gamemode.config import Config
from gamemode.feature import (
    CommandWrapper,
    FeatureResult,
    WrapperFactory,
    _BaseFeature,
)
from gamemode.runner import Runner


class VRR(_BaseFeature):
    _JQ_VRR_SUPPORTED = ".[$o].vrr_supported // true"
    _JQ_VRR_ENABLED = 'if .[$o].vrr_enabled == true then "true" elif .[$o].vrr_enabled == false then "false" else "" end'

    def __init__(self, config: Config, runner: Runner, log: logging.Logger) -> None:
        super().__init__(config, runner, log)
        self._niri_cmd = runner.make_checked_runner("niri", "VRR")

    def _jq_query(self, jq_expr: str, jq_args: dict[str, str] | None) -> str | None:
        if not self._run.require("jq", "VRR"):
            return None
        data_result = self._run.capture(["niri", "msg", "-j", "outputs"])
        if data_result.returncode != 0:
            return None
        jq_argv: list[str] = ["jq", "-r"]
        if jq_args:
            for key, val in jq_args.items():
                jq_argv.extend(["--arg", key, val])
        jq_argv.append(jq_expr)
        jq_result = self._run.pipe(jq_argv, data_result.stdout)
        if jq_result.returncode != 0:
            return None
        return jq_result.stdout.strip()

    def _is_capable(self, output: str) -> bool:
        return self._jq_query(self._JQ_VRR_SUPPORTED, {"o": output}) == "true"

    def _current(self, output: str) -> str:
        result = self._jq_query(self._JQ_VRR_ENABLED, {"o": output})
        if result is None:
            return ""
        if result == "true":
            return "on"
        if result == "false":
            return "off"
        return ""

    def _set(self, output: str, state: str) -> bool:
        if not self._niri_cmd.is_available:
            return False
        result = self._niri_cmd.run_or_none(
            ["niri", "msg", "output", output, "vrr", state]
        )
        return result is not None and result.returncode == 0

    def _toggle(self, output: str, desired: str) -> FeatureResult:
        gate = self._gate(self._cfg.enable_vrr, "VRR")
        if gate is not None:
            return gate
        if not compositor_is_niri():
            return FeatureResult.skip("niri not running")
        if not self._is_capable(output):
            return FeatureResult.skip(f"output '{output}' not VRR-capable")
        current = self._current(output)
        if current == "":
            return FeatureResult.skip(f"output '{output}' not found")
        if current == desired:
            return FeatureResult.noop()
        ok = self._set(output, desired)
        return (
            FeatureResult.did_change(f"{current} → {desired} on {output}")
            if ok
            else FeatureResult.error("toggle failed")
        )

    def enable(self, output: str) -> FeatureResult:
        return self._toggle(output, "on")

    def disable(self, output: str) -> FeatureResult:
        return self._toggle(output, "off")


class PowerProfile(_BaseFeature):
    _CMD = "tuned-adm"
    _FEATURE = "Performance mode"

    def __init__(self, config: Config, runner: Runner, log: logging.Logger) -> None:
        super().__init__(config, runner, log)
        self._tuned = runner.make_checked_runner(self._CMD, self._FEATURE)

    def _current(self) -> str:
        result = self._tuned.run_or_none([self._CMD, "active"])
        if result is None:
            return ""
        for line in result.stdout.splitlines():
            if line.startswith("Active profile:"):
                return line.split(":", 1)[1].strip()
        return ""

    def _set(self, profile: str) -> bool:
        result = self._tuned.run_or_none([self._CMD, "profile", profile])
        return result is not None and result.returncode == 0

    def _set_state(self, desired: str) -> FeatureResult:
        gate = self._gate(self._cfg.enable_tuned, "Performance mode")
        if gate is not None:
            return gate
        current = self._current()
        if current == desired:
            return FeatureResult.noop()
        self._log.info("Profile: %s → %s", current or "none", desired)
        ok = self._set(desired)
        return (
            FeatureResult.did_change(f"{current or 'none'} → {desired}")
            if ok
            else FeatureResult.error(f"failed to set {desired}")
        )

    def enable(self, _output: str) -> FeatureResult:
        return self._set_state(self._cfg.profile_game)

    def disable(self, _output: str) -> FeatureResult:
        return self._set_state(self._cfg.profile_desktop)


class SCXScheduler(_BaseFeature):
    _CMD = "scxctl"
    _FEATURE = "SCX scheduler"

    def __init__(self, config: Config, runner: Runner, log: logging.Logger) -> None:
        super().__init__(config, runner, log)
        self._scxctl = runner.make_checked_runner(self._CMD, self._FEATURE)

    def _status(self) -> str:
        result = self._scxctl.run_or_none([self._CMD, "get"])
        return result.stdout.strip() if result else ""

    def _apply(self) -> FeatureResult:
        result = self._scxctl.run_or_none(
            [
                self._CMD,
                "start",
                "-s",
                self._cfg.scx_scheduler,
                "-m",
                self._cfg.scx_mode,
            ]
        )
        ok = result is not None and result.returncode == 0
        return (
            FeatureResult.did_change(f"{self._cfg.scx_scheduler}/{self._cfg.scx_mode}")
            if ok
            else FeatureResult.error("scxctl failed")
        )

    def _set_state(self, desired: str) -> FeatureResult:
        return self._guarded(
            self._cfg.enable_scx, "SCX scheduler", lambda: self._set(desired)
        )

    def _set(self, desired: str) -> FeatureResult:
        if not self._scxctl.is_available:
            return FeatureResult.skip("scxctl not found")
        status = self._status()
        if desired == "on":
            if (
                status
                and self._cfg.scx_scheduler.lower() in status.lower()
                and self._cfg.scx_mode.lower() in status.lower()
            ):
                return FeatureResult.noop()
            return self._apply()
        else:
            if not status or "no scx scheduler running" in status:
                return FeatureResult.noop()
            result = self._scxctl.run_or_none([self._CMD, "stop"])
            ok = result is not None and result.returncode == 0
            return (
                FeatureResult.did_change("stopped")
                if ok
                else FeatureResult.error("stop failed")
            )

    def enable(self, _output: str) -> FeatureResult:
        return self._set_state("on")

    def disable(self, _output: str) -> FeatureResult:
        return self._set_state("off")


class AudioPriority(_BaseFeature):
    def _set_state(self, desired: str) -> FeatureResult:
        return self._guarded(
            self._cfg.enable_audio, "Audio priority", lambda: self._set(desired)
        )

    def _set(self, desired: str) -> FeatureResult:
        if desired == "on":
            self._log.debug("Audio: PULSE_LATENCY_MSEC=%s", self._cfg.audio_latency)
            os.environ["PULSE_LATENCY_MSEC"] = self._cfg.audio_latency
            self._cfg.audio_env_file.parent.mkdir(parents=True, exist_ok=True)
            self._cfg.audio_env_file.write_text(
                f"export PULSE_LATENCY_MSEC={self._cfg.audio_latency}\n"
            )
            return FeatureResult.did_change(
                f"PULSE_LATENCY_MSEC={self._cfg.audio_latency}"
            )
        else:
            os.environ.pop("PULSE_LATENCY_MSEC", None)
            try:
                self._cfg.audio_env_file.unlink()
            except FileNotFoundError:
                pass
            return FeatureResult.did_change("cleared PULSE_LATENCY_MSEC")

    def enable(self, _output: str) -> FeatureResult:
        return self._set_state("on")

    def disable(self, _output: str) -> FeatureResult:
        return self._set_state("off")


class SystemdRun:
    def __init__(self, config: Config, runner: Runner, log: logging.Logger) -> None:
        self._cfg = config
        self._run = runner
        self._log = log

    def wrap_argv(self, argv: list[str]) -> list[str]:
        if not self._cfg.enable_systemd_run:
            return argv
        if not self._run.require("systemd-run", "systemd-run"):
            return argv
        if not self._cfg.systemd_run_args:
            self._log.warning("ENABLE_SYSTEMD_RUN is set but SYSTEMD_RUN_ARGS is empty")
            return argv
        self._log.debug(
            "systemd-run wrapping: %s",
            " ".join(self._cfg.systemd_run_args + ["--", *argv]),
        )
        return ["systemd-run", *self._cfg.systemd_run_args, "--", *argv]


class ScreenInhibit(_BaseFeature):
    _DMS_CMD = "dms"
    _DMS_FEATURE = "DMS inhibit"
    _DBUS_SERVICE = "org.freedesktop.ScreenSaver"
    _DBUS_PATH = "/ScreenSaver"
    _DBUS_IFACE = "org.freedesktop.ScreenSaver"

    def __init__(self, config: Config, runner: Runner, log: logging.Logger) -> None:
        super().__init__(config, runner, log)
        self._dms = runner.make_checked_runner(self._DMS_CMD, self._DMS_FEATURE)
        self._dbus_send: str | None = runner.resolve("dbus-send")
        self._screensaver_cookie: int | None = None

    def _dms_inhibit_enabled(self) -> bool:
        result = self._dms.run_or_none(
            [self._DMS_CMD, "ipc", "call", "inhibit", "status"]
        )
        return result is not None and "Idle inhibit is disabled" not in result.stdout

    def _dms_inhibit_enable(self, reason: str = "gamemode.py gaming session") -> bool:
        if not self._dms.is_available:
            return False
        if self._dms_inhibit_enabled():
            return True
        for cmd, desc in (
            (["enable"], "enable DMS inhibit"),
            (["reason", reason], "set DMS inhibit reason"),
        ):
            r = self._dms.run_or_none([self._DMS_CMD, "ipc", "call", "inhibit", *cmd])
            if r is None or r.returncode != 0:
                self._log.error("Failed to %s", desc)
                return False
        return True

    def _dms_inhibit_disable(self) -> None:
        if not self._dms.is_available or not self._dms_inhibit_enabled():
            return
        self._dms.run_or_none([self._DMS_CMD, "ipc", "call", "inhibit", "disable"])

    def _screensaver_inhibit_enable(
        self, reason: str = "gamemode.py gaming session"
    ) -> bool:
        if self._screensaver_cookie is not None:
            return True
        if self._dbus_send is None:
            return False
        result = self._run.capture(
            [
                self._dbus_send,
                "--session",
                f"--dest={self._DBUS_SERVICE}",
                "--type=method_call",
                "--print-reply=literal",
                self._DBUS_PATH,
                f"{self._DBUS_IFACE}.Inhibit",
                "string:gamemode.py",
                f"string:{reason}",
            ]
        )
        if result.returncode != 0:
            self._log.warning("ScreenSaver.Inhibit failed: %s", result.stderr.strip())
            return False
        cookie_str = result.stdout.strip()
        if cookie_str.startswith("uint32 "):
            cookie_str = cookie_str[7:]
        try:
            self._screensaver_cookie = int(cookie_str)
        except ValueError:
            self._log.warning("Unexpected cookie value: %r", cookie_str)
            return False
        return True

    def _screensaver_inhibit_disable(self) -> None:
        if self._screensaver_cookie is None:
            return
        if self._dbus_send is None:
            return
        result = self._run.capture(
            [
                self._dbus_send,
                "--session",
                f"--dest={self._DBUS_SERVICE}",
                "--type=method_call",
                "--print-reply=literal",
                self._DBUS_PATH,
                f"{self._DBUS_IFACE}.UnInhibit",
                f"uint32:{self._screensaver_cookie}",
            ]
        )
        if result.returncode != 0:
            err = result.stderr.strip()
            if "invalid cookie" in err.lower():
                self._log.debug("ScreenSaver cookie invalid (already released)")
            else:
                self._log.warning("ScreenSaver.UnInhibit failed: %s", err)
        else:
            self._log.debug("ScreenSaver cookie released: %d", self._screensaver_cookie)
        self._screensaver_cookie = None

    def _set_state(self, desired: str) -> FeatureResult:
        return self._guarded(
            self._cfg.enable_inhibit, "Screen inhibit", lambda: self._set(desired)
        )

    def _set(self, desired: str) -> FeatureResult:
        results: list[str] = []
        if desired == "on":
            if compositor_is_niri():
                if self._dms_inhibit_enable():
                    results.append("DMS inhibit enabled")
                else:
                    self._log.warning("DMS inhibit failed, falling back to DBus")
            if self._screensaver_inhibit_enable():
                results.append("ScreenSaver cookie acquired")
            if not results:
                return FeatureResult.error("all inhibit mechanisms failed")
            return FeatureResult.did_change("; ".join(results))
        else:
            if compositor_is_niri():
                self._dms_inhibit_disable()
                results.append("DMS inhibit disabled")
            self._screensaver_inhibit_disable()
            results.append("ScreenSaver cookie released")
            return FeatureResult(changed=True, detail="; ".join(results))

    def enable(self, _output: str) -> FeatureResult:
        return self._set_state("on")

    def disable(self, _output: str) -> FeatureResult:
        return self._set_state("off")


def steam_wrapper_factory(
    config: Config, _runner: Runner, log: logging.Logger
) -> CommandWrapper | None:
    if not config.enable_steam:
        return None

    def wrap(argv: list[str]) -> list[str]:
        path = Path(config.steam_script)
        if not path.is_file() or not os.access(path, os.X_OK):
            log.debug("Steam wrapper not available: %s", path)
            return argv
        return [str(path), *argv]

    return wrap


def inhibit_wrapper_factory(
    config: Config, runner: Runner, log: logging.Logger
) -> CommandWrapper | None:
    if not config.enable_inhibit:
        return None
    inhibit = runner.resolve("systemd-inhibit")
    if inhibit is None:
        return None

    def wrapper(argv: list[str]) -> list[str]:
        return [
            inhibit,
            "--what=idle:sleep",
            "--mode=block",
            "--why=gamemode.py",
            "--",
            *argv,
        ]

    return wrapper


def systemd_run_wrapper_factory(
    config: Config, runner: Runner, log: logging.Logger
) -> CommandWrapper | None:
    if not config.enable_systemd_run:
        return None
    return SystemdRun(config, runner, log).wrap_argv


WRAPPER_FACTORIES: dict[str, WrapperFactory] = {
    "steam": steam_wrapper_factory,
    "inhibit": inhibit_wrapper_factory,
    "systemd_run": systemd_run_wrapper_factory,
}


class WrapperChain:
    def __init__(self) -> None:
        self._wrappers: list[CommandWrapper] = []

    def add(self, wrapper: CommandWrapper | None) -> None:
        if wrapper is not None:
            self._wrappers.append(wrapper)

    def add_factory(
        self,
        factory: WrapperFactory,
        config: Config,
        runner: Runner,
        log: logging.Logger,
    ) -> None:
        wrapper = factory(config, runner, log)
        if wrapper is not None:
            self._wrappers.append(wrapper)

    def apply(self, argv: list[str]) -> list[str]:
        result = list(argv)
        for wrapper in self._wrappers:
            result = wrapper(result)
        return result
