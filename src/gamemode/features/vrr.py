"""VRR (Virtual Refresh Rate) feature."""

from __future__ import annotations

import json
import logging
import os

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

    def _outputs_json(self) -> dict | None:
        result = self._run.capture(["niri", "msg", "-j", "outputs"])
        if result.returncode != 0 or not result.stdout:
            return None
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            return None

    def _valid_outputs(self, candidates: str) -> list[str]:
        # ponytail: single niri capture per call; no cross enable/disable cache.
        # present in map == connected; logical != null == enabled.
        data = self._outputs_json()
        if data is None:
            return []
        return [
            name
            for name in (c.strip() for c in candidates.split(",") if c.strip())
            if (info := data.get(name)) and info.get("logical") is not None
        ]

    def _all_enabled_outputs(self) -> list[str]:
        data = self._outputs_json()
        if data is None:
            return []
        return [name for name, info in data.items() if info.get("logical") is not None]

    def _targets(self) -> list[str]:
        if env := os.environ.get("VRR_OUTPUTS"):
            return self._valid_outputs(env)
        return self._all_enabled_outputs()

    def _aggregate(self, results: list[FeatureResult]) -> FeatureResult:
        errors = [r for r in results if not r.ok]
        if errors:
            return FeatureResult.error("; ".join(r.detail for r in errors))
        changed = [r for r in results if r.changed]
        if changed:
            return FeatureResult.did_change("; ".join(r.detail for r in changed))
        if all(r.skipped for r in results):
            return FeatureResult.skip("; ".join(r.detail for r in results if r.skipped))
        return FeatureResult.noop()

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

    def _do_enable(self) -> FeatureResult:
        if not compositor_is_niri():
            return FeatureResult.skip("niri not running")
        targets = self._targets()
        if not targets:
            return FeatureResult.skip("no connected+enabled output")
        return self._aggregate([self._vrr_toggle(t, "on") for t in targets])

    def _do_disable(self) -> FeatureResult:
        if not compositor_is_niri():
            return FeatureResult.skip("niri not running")
        targets = self._targets()
        if not targets:
            return FeatureResult.skip("no connected+enabled output")
        return self._aggregate([self._vrr_toggle(t, "off") for t in targets])

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
