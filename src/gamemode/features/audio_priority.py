"""Audio priority feature."""

from __future__ import annotations

import os

from gamemode.feature import FeatureResult, _BaseFeature


class AudioPriority(_BaseFeature):
    _feature_name = "Audio priority"

    @property
    def _feature_enabled(self) -> bool:
        return self._cfg.enable_audio

    def _do_enable(self) -> FeatureResult:
        self._log.debug("Audio: PULSE_LATENCY_MSEC=%s", self._cfg.audio_latency)
        os.environ["PULSE_LATENCY_MSEC"] = self._cfg.audio_latency
        self._cfg.audio_env_file.parent.mkdir(parents=True, exist_ok=True)
        self._cfg.audio_env_file.write_text(
            f"export PULSE_LATENCY_MSEC={self._cfg.audio_latency}\n"
        )
        return FeatureResult.did_change(f"PULSE_LATENCY_MSEC={self._cfg.audio_latency}")

    def _do_disable(self) -> FeatureResult:
        os.environ.pop("PULSE_LATENCY_MSEC", None)
        try:
            self._cfg.audio_env_file.unlink()
        except FileNotFoundError:
            pass
        return FeatureResult.did_change("cleared PULSE_LATENCY_MSEC")
