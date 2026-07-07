# Model Cache

Place optional project-local emotion2vec and Silero VAD files here to avoid downloading them on first run.

If a model is already present here, `python download_models.py seed` or `python download_models.py base` will detect it and skip the download.

For a connected Pi, the simplest path is to cache the model once from the repository root:

```bash
source venv/bin/activate
python download_models.py seed
```

That stores the model in the normal ModelScope cache. Future runs load from that cache and do not download again.

If you want a fully self-contained copy inside this repository, copy the cached model into this folder after downloading.

Expected layout for a fully prepared USB/offline bundle:

```text
models/
  silero-vad/
    hubconf.py
    src/silero_vad/data/silero_vad.onnx
    src/silero_vad/data/silero_vad.jit
  iic/
    emotion2vec_plus_base/
    emotion2vec_plus_seed/  # optional, only if model.pt is present
```

To prepare Silero VAD from a connected machine that already ran the project once:

```bash
cp -a ~/.cache/torch/hub/snakers4_silero-vad_master models/silero-vad
```

openSMILE does not belong in this folder. It is installed into the Python `venv/` by `setup_pi.sh` through the `opensmile` package in `requirements-pi.txt`.

Lookup order in `src/emotion_model.py`:

1. `models/<model_id>/`
2. `~/.cache/modelscope/hub/models/<model_id>/`
3. ModelScope online download

The model files are intentionally ignored by git. Keep this README tracked so the directory exists in fresh clones.
