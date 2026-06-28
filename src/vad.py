"""
Voice Activity Detection using Silero VAD.

Gates the emotion inference so we only classify chunks that actually
contain speech, avoiding the "random high-confidence on silence" problem.
"""

from __future__ import annotations

import numpy as np
import torch


class SileroVAD:
    """Lightweight wrapper around Silero VAD (runs on CPU, ~1 ms per chunk)."""

    def __init__(self, threshold: float = 0.3, sample_rate: int = 16000):
        self._model, utils = torch.hub.load(
            "snakers4/silero-vad", "silero_vad",
            trust_repo=True, force_reload=False,
        )
        self._get_speech_ts = utils[0]      # get_speech_timestamps
        self._threshold = threshold
        self._sr = sample_rate

    def speech_ratio(self, audio: np.ndarray) -> float:
        """Return fraction of the audio chunk that contains speech [0..1]."""
        tensor = torch.from_numpy(audio).float()
        timestamps = self._get_speech_ts(
            tensor,
            self._model,
            threshold=self._threshold,
            sampling_rate=self._sr,
            min_speech_duration_ms=250,
        )
        if not timestamps:
            return 0.0
        total_speech = sum(t["end"] - t["start"] for t in timestamps)
        self._model.reset_states()  # required between calls
        return total_speech / len(audio)

    def has_speech(self, audio: np.ndarray, min_ratio: float = 0.1) -> bool:
        """True if at least *min_ratio* of the chunk is voiced."""
        return self.speech_ratio(audio) >= min_ratio
