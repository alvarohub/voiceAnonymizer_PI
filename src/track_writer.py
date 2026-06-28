"""
Append-only CSV writer for the emotion track + optional .npy for embeddings.

Each CSV row contains a wall-clock timestamp, elapsed time, the top
emotion label, its confidence, per-dimension scores, and prosody features
(88 eGeMAPSv02 columns when openSMILE is available).

If save_embeddings=True, emotion2vec latent vectors (768-d) are
collected in memory and flushed to a .npy file on close().
"""

from __future__ import annotations

import csv
import os
from typing import Optional
import numpy as np


class TrackWriter:
    def __init__(
        self,
        output_path: str,
        dimensions: list[str],
        extra_columns: Optional[list[str]] = None,
        save_embeddings: bool = False,
    ):
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        self._file = open(output_path, "w", newline="")
        self._writer = csv.writer(self._file)
        self._dimensions = dimensions
        self._extra = extra_columns or []
        self._start_ms: Optional[float] = None

        # Embedding storage
        self._save_emb = save_embeddings
        self._emb_path = output_path.replace(".csv", "_embeddings.npy")
        self._embeddings: list[np.ndarray] = []

        # Header
        self._writer.writerow(
            ["timestamp_ms", "elapsed_ms", "label", "confidence"]
            + dimensions
            + self._extra
        )
        self._file.flush()

    def write(self, result: dict, timestamp_ms: float) -> None:
        if self._start_ms is None:
            self._start_ms = timestamp_ms

        elapsed = timestamp_ms - self._start_ms
        row = [
            int(timestamp_ms),
            int(elapsed),
            result["label"],
            f"{result['confidence']:.4f}",
        ] + [f"{result['scores'].get(d, 0.0):.4f}" for d in self._dimensions]

        # Append prosody features if present
        prosody = result.get("prosody", {})
        for col in self._extra:
            row.append(f"{prosody.get(col, 0.0):.6f}")

        self._writer.writerow(row)
        self._file.flush()

        # Collect embedding
        if self._save_emb and "embedding" in result:
            self._embeddings.append(result["embedding"])

    def close(self) -> None:
        self._file.close()
        if self._save_emb and self._embeddings:
            arr = np.stack(self._embeddings)
            np.save(self._emb_path, arr)
            print(f"Embeddings saved → {self._emb_path}  ({arr.shape})")
