#!/usr/bin/env python3
"""Download/cache emotion2vec models before running live capture.

Run this once after `bash setup_pi.sh` if the Pi has internet access. Later
`strip_monitor.py` runs will load from the local project cache or from the
ModelScope cache instead of downloading again.
"""

from __future__ import annotations

import argparse

from src.emotion_model import Emotion2VecModel


MODEL_IDS = {
    "base": "iic/emotion2vec_plus_base",
    "seed": "iic/emotion2vec_plus_seed",
}


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
        print(f"[cache] ensuring {variant} model is available: {model_id}")
        Emotion2VecModel(model_id, device="cpu")
        print(f"[cache] ready: {variant}")


if __name__ == "__main__":
    main()