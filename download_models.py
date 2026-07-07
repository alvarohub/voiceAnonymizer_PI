#!/usr/bin/env python3
"""Download/cache emotion2vec models before running live capture.

Run this once after `bash setup_pi.sh` if the Pi has internet access. Later
`strip_monitor.py` runs will load from the local project cache or from the
ModelScope cache instead of downloading again.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from src.emotion_model import Emotion2VecModel


MODEL_IDS = {
    "base": "iic/emotion2vec_plus_base",
    "seed": "iic/emotion2vec_plus_seed",
}


PROJECT_ROOT = Path(__file__).resolve().parent


def model_file(path: Path) -> Path:
    return path / "model.pt"


def project_model_path(model_id: str) -> Path:
    return PROJECT_ROOT / "models" / model_id


def modelscope_cache_path(model_id: str) -> Path:
    return Path.home() / ".cache" / "modelscope" / "hub" / "models" / model_id


def has_model(path: Path) -> bool:
    return model_file(path).exists()


def main() -> None:
    parser = argparse.ArgumentParser(description="Cache emotion2vec models")
    parser.add_argument(
        "models",
        nargs="*",
        choices=sorted(MODEL_IDS),
        default=["seed"],
        help="Model variants to cache. Default: seed.",
    )
    args = parser.parse_args()

    for variant in args.models:
        model_id = MODEL_IDS[variant]

        local_path = project_model_path(model_id)
        if has_model(local_path):
            print(f"[cache] already available in project models/: {local_path}")
            continue

        cache_path = modelscope_cache_path(model_id)
        if has_model(cache_path):
            print(f"[cache] already available in ModelScope cache: {cache_path}")
            continue

        print(f"[cache] downloading {variant} model: {model_id}")
        Emotion2VecModel(model_id, device="cpu")
        print(f"[cache] ready: {variant}")


if __name__ == "__main__":
    main()