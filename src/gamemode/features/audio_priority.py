"""Audio priority feature.

The env file is the contract; os.environ is never mutated — wrapper mode
routes the override to the child through the Popen env channel (actions).
"""

from __future__ import annotations

from gamemode.feature import FeatureResult, _BaseFeature


class AudioPriority(_BaseFeature):
    @property
    def _feature_enabled(self) -> bool:
        return self._cfg.enable_audio

    def _do_enable(self) -> FeatureResult:
        self._log.debug("Audio: PULSE_LATENCY_MSEC=%s", self._cfg.audio_latency)
        self._cfg.audio_env_file.parent.mkdir(parents=True, exist_ok=True)
        self._cfg.audio_env_file.write_text(
            f"export PULSE_LATENCY_MSEC={self._cfg.audio_latency}\n"
        )
        return FeatureResult.did_change(f"PULSE_LATENCY_MSEC={self._cfg.audio_latency}")

    def _do_disable(self) -> FeatureResult:
        try:
            self._cfg.audio_env_file.unlink()
        except FileNotFoundError:
            pass
        return FeatureResult.did_change("cleared PULSE_LATENCY_MSEC")
