"""
Emotion-recognition model wrapper.

Currently wraps **emotion2vec** via the FunASR toolkit.  The rest of the
pipeline only depends on the public interface defined below, so swapping
to a different backbone (HuBERT, wav2vec 2.0, Whisper features + head,
your own model, …) only requires changing *this* file.

=======================================================================
MODEL SWAP GUIDE
=======================================================================
To replace emotion2vec with a custom model:

1.  Create a new class that exposes:

        class YourModel:
            def __init__(self, model_name: str, device: str = "cpu"): ...

            @property
            def dimensions(self) -> list[str]:
                \"\"\"Ordered list of output dimension / label names.\"\"\"
                ...

            def predict(self, audio: np.ndarray, sr: int = 16000) -> dict:
                \"\"\"
                Returns:
                    {
                        "label":      str,              # top-scoring dimension
                        "confidence": float,             # its score in [0, 1]
                        "scores":     dict[str, float],  # {dim_name: score}
                    }
                \"\"\"
                ...

2.  In main.py, change the import:

        from emotion_model import YourModel as EmotionModel

    and update CONFIG["model_name"] if needed.

EXAMPLE — HuBERT with a HuggingFace classification head:

    class HuBERTEmotionModel:
        def __init__(self, model_name="superb/hubert-large-superb-er", device="cpu"):
            from transformers import (
                AutoModelForAudioClassification,
                AutoFeatureExtractor,
            )
            self.extractor = AutoFeatureExtractor.from_pretrained(model_name)
            self.model = AutoModelForAudioClassification.from_pretrained(model_name)
            self.model.to(device).eval()
            self._device = device
            self._dims = list(self.model.config.id2label.values())

        @property
        def dimensions(self):
            return self._dims

        def predict(self, audio, sr=16000):
            import torch
            inputs = self.extractor(audio, sampling_rate=sr, return_tensors="pt")
            inputs = {k: v.to(self._device) for k, v in inputs.items()}
            with torch.no_grad():
                logits = self.model(**inputs).logits
            probs = torch.softmax(logits, dim=-1).squeeze().cpu().numpy()
            scores = {d: float(p) for d, p in zip(self._dims, probs)}
            label = max(scores, key=scores.get)
            return {"label": label, "confidence": scores[label], "scores": scores}

=======================================================================
"""

from __future__ import annotations

import numpy as np


class Emotion2VecModel:
    """Wraps emotion2vec (FunASR) for utterance-level emotion recognition."""

    # --- DIMENSION DEFINITIONS ------------------------------------------------
    # emotion2vec_plus outputs 9 categories.  If you fine-tune or relabel,
    # update this list *and* make sure the model's output order matches.
    DIMENSIONS = [
        "angry",
        "disgusted",
        "fearful",
        "happy",
        "neutral",
        "other",
        "sad",
        "surprised",
        "unknown",
    ]

    def __init__(
        self,
        model_name: str = "iic/emotion2vec_plus_base",
        device: str = "cpu",
    ):
        from funasr import AutoModel  # lazy import keeps startup fast if unused
        import os

        # Model lookup order (first match wins):
        #   1. Project-local: models/<model_name>/
        #      Use this to ship models on a USB stick — no buried caches.
        #   2. Modelscope cache: ~/.cache/modelscope/hub/models/<model_name>/
        #      Auto-populated by first-run downloads.
        #   3. Modelscope hub (online) — triggers download into cache (2).
        #
        # Loading from a local path skips modelscope's download-tracking
        # logic, which would otherwise re-download model.pt even if we
        # placed it there manually.

        # __file__ = .../src/emotion_model.py
        # project_root = repository root
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        local_models = os.path.join(project_root, "models", model_name)
        local_cache = os.path.expanduser(
            f"~/.cache/modelscope/hub/models/{model_name}"
        )

        def _has_model_pt(path):
            return os.path.isdir(path) and os.path.exists(
                os.path.join(path, "model.pt")
            )

        if _has_model_pt(local_models):
            print(f"[model] Loading from project-local: {local_models}")
            model_name = local_models
        elif _has_model_pt(local_cache):
            print(f"[model] Loading from modelscope cache: {local_cache}")
            model_name = local_cache
        else:
            print(f"[model] Not cached — downloading {model_name} from modelscope")

        self._model = AutoModel(model=model_name, device=device)
        self._dimensions = list(self.DIMENSIONS)

    # ---- public interface ----------------------------------------------------

    @property
    def dimensions(self) -> list[str]:
        return self._dimensions

    def predict(self, audio: np.ndarray, sr: int = 16000, extract_embedding: bool = False) -> dict:
        """
        Run utterance-level emotion inference on a mono audio chunk.

        Args:
            audio:             1-D float32 numpy array, mono, sampled at *sr* Hz.
            sr:                Sample rate (emotion2vec expects 16 000).
            extract_embedding: If True, also return the 768-d latent vector.

        Returns:
            {
                "label":      str,
                "confidence": float,
                "scores":     {dimension_name: float, …},
                "embedding":  np.ndarray (768,)  — only if extract_embedding
            }
        """
        result = self._model.generate(
            audio,
            granularity="utterance",
            extract_embedding=extract_embedding,
            disable_pbar=True,
            disable_log=True,
        )

        # funasr returns a list[dict]; take the first entry
        entry = result[0] if result else {}

        labels = entry.get("labels", self._dimensions)
        scores_list = entry.get("scores", [0.0] * len(self._dimensions))

        # emotion2vec_plus returns bilingual labels like "生气/angry".
        # Normalise to English-only so they match our DIMENSIONS list.
        scores: dict[str, float] = {}
        for lbl, sc in zip(labels, scores_list):
            eng = lbl.split("/")[-1] if "/" in lbl else lbl
            scores[eng] = float(sc)

        raw_label = entry.get("text", max(scores, key=scores.get))
        top_label = raw_label.split("/")[-1] if "/" in raw_label else raw_label
        top_score = scores.get(top_label, 0.0)

        out = {
            "label": top_label,
            "confidence": top_score,
            "scores": scores,
        }

        if extract_embedding:
            feats = entry.get("feats", None)
            if feats is not None:
                out["embedding"] = np.asarray(feats, dtype=np.float32)

        return out
