"""
Microphone capture with sliding-window chunking.

Runs a sounddevice.InputStream in a callback; accumulates audio in a
thread-safe ring buffer.  Call get_chunk() from any thread to retrieve
the latest analysis window once enough *new* samples have arrived.
"""

import threading
from typing import Optional
import numpy as np
import sounddevice as sd


class AudioCapture:
    # Maximum chunk duration we'll ever support (sets ring buffer size)
    MAX_CHUNK_SECONDS = 10.0

    def __init__(
        self,
        sample_rate: int = 16000,
        channels: int = 1,
        chunk_duration: float = 2.0,
        hop_duration: float = 1.0,
    ):
        self.sample_rate = sample_rate
        self.channels = channels
        self.chunk_samples = int(chunk_duration * sample_rate)
        self.hop_samples = int(hop_duration * sample_rate)

        # Ring buffer sized for MAX_CHUNK_SECONDS × 4
        buf_size = int(self.MAX_CHUNK_SECONDS * sample_rate) * 4
        self._buffer = np.zeros(buf_size, dtype=np.float32)
        self._write_pos = 0
        self._unread_samples = 0
        self._total_written = 0
        self._lock = threading.Lock()

        self._stream: Optional[sd.InputStream] = None

    def set_chunk_duration(self, seconds: float) -> None:
        """Change the analysis window and hop size at runtime (thread-safe).

        Hop equals window (no overlap) — each chunk is independent.
        """
        new_samples = int(seconds * self.sample_rate)
        max_samples = int(self.MAX_CHUNK_SECONDS * self.sample_rate)
        new_samples = min(new_samples, max_samples)
        with self._lock:
            self.chunk_samples = new_samples
            self.hop_samples = new_samples

    # ---- sounddevice callback (runs in audio thread) ----

    def _callback(self, indata, frames, time_info, status):
        if status:
            print(f"[audio] {status}")
        audio = indata[:, 0]  # mono
        n = len(audio)
        with self._lock:
            end = self._write_pos + n
            if end <= len(self._buffer):
                self._buffer[self._write_pos : end] = audio
            else:
                first = len(self._buffer) - self._write_pos
                self._buffer[self._write_pos :] = audio[:first]
                self._buffer[: n - first] = audio[first:]
            self._write_pos = end % len(self._buffer)
            self._unread_samples += n
            self._total_written += n

    # ---- public API ----

    def start(self):
        self._stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=self.channels,
            dtype="float32",
            callback=self._callback,
        )
        self._stream.start()

    def stop(self):
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()

    def get_chunk(self) -> Optional[np.ndarray]:
        """Return the latest chunk_samples of audio, or None if not enough new data."""
        with self._lock:
            if self._unread_samples < self.hop_samples:
                return None

            read_end = self._write_pos
            read_start = (read_end - self.chunk_samples) % len(self._buffer)

            if read_start < read_end:
                chunk = self._buffer[read_start:read_end].copy()
            else:
                chunk = np.concatenate(
                    [self._buffer[read_start:], self._buffer[:read_end]]
                )

            self._unread_samples = 0
            return chunk

    def get_latest_audio(self, seconds: float) -> Optional[np.ndarray]:
        """Read last *seconds* of audio from ring buffer (non-consuming).

        Unlike get_chunk(), this does NOT reset the unread counter —
        it's meant for an independent reader (e.g. the prosody LLD thread).
        Returns None if not enough audio has been captured yet.
        """
        n_samples = int(seconds * self.sample_rate)
        with self._lock:
            if self._total_written < n_samples:
                return None
            n_samples = min(n_samples, len(self._buffer) // 2)
            read_end = self._write_pos
            read_start = (read_end - n_samples) % len(self._buffer)
            if read_start < read_end:
                return self._buffer[read_start:read_end].copy()
            else:
                return np.concatenate(
                    [self._buffer[read_start:], self._buffer[:read_end]]
                )
