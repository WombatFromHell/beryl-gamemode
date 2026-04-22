"""Audio priority feature."""

from __future__ import annotations

import os

from gamemode.feature import FeatureResult, _BaseFeature


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
