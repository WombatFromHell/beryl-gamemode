"""VRR (Virtual Refresh Rate) feature."""

from __future__ import annotations

import logging

from gamemode.compositor import compositor_is_niri
from gamemode.config import Config
from gamemode.feature import (
    FeatureResult,
    _BaseFeature,
)
from gamemode.runner import Runner


class VRR(_BaseFeature):
    _JQ_VRR_SUPPORTED = ".[$o].vrr_supported // true"
    _JQ_VRR_ENABLED = 'if .[$o].vrr_enabled == true then "true" elif .[$o].vrr_enabled == false then "false" else "" end'

    _feature_name = "VRR"

    def __init__(self, config: Config, runner: Runner, log: logging.Logger) -> None:
        super().__init__(config, runner, log)
        self._niri_cmd = self.make_checked_cmd("niri", "VRR")

    @property
    def _feature_enabled(self) -> bool:
        return self._cfg.enable_vrr

    def _build_jq_argv(self, jq_expr: str, jq_args: dict[str, str] | None) -> list[str]:
        argv = ["jq", "-r"]
        if jq_args:
            for key, val in jq_args.items():
                argv.extend(["--arg", key, val])
        argv.append(jq_expr)
        return argv

    def _jq_query(self, jq_expr: str, jq_args: dict[str, str] | None) -> str | None:
        if not self._run.require("jq", "VRR"):
            return None
        data_result = self._run.capture(["niri", "msg", "-j", "outputs"])
        if data_result.returncode != 0:
            return None
        jq_argv = self._build_jq_argv(jq_expr, jq_args)
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
        return {"true": "on", "false": "off"}.get(result, "")

    def _set(self, output: str, state: str) -> bool:
        if not self._niri_cmd.is_available:
            return False
        return self._niri_cmd.run_ok(["niri", "msg", "output", output, "vrr", state])

    def _do_enable(self, output: str) -> FeatureResult:
        if not compositor_is_niri():
            return FeatureResult.skip("niri not running")
        return self._vrr_toggle(output, "on")

    def _do_disable(self, output: str) -> FeatureResult:
        if not compositor_is_niri():
            return FeatureResult.skip("niri not running")
        return self._vrr_toggle(output, "off")

    def _vrr_toggle(self, output: str, desired: str) -> FeatureResult:
        current = self._current(output)
        if current == "":
            return FeatureResult.skip(f"output '{output}' not found")
        if not self._is_capable(output):
            return FeatureResult.skip(f"output '{output}' not VRR-capable")
        if current == desired:
            return FeatureResult.noop()
        ok = self._set(output, desired)
        if ok:
            return FeatureResult.did_change(f"{current} → {desired} on {output}")
        return FeatureResult.error("toggle failed")
