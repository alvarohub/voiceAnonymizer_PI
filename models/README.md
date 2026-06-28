# Model Cache

Place optional project-local emotion2vec models here to avoid downloading them on first run.

For a connected Pi, the simplest path is to cache the model once from the repository root:

```bash
source venv/bin/activate
python download_models.py seed
```

That stores the model in the normal ModelScope cache. Future runs load from that cache and do not download again.

If you want a fully self-contained copy inside this repository, copy the cached model into this folder after downloading.

Expected layout:

```text
models/
  iic/
    emotion2vec_plus_base/
    emotion2vec_plus_seed/
```

Lookup order in `src/emotion_model.py`:

1. `models/<model_id>/`
2. `~/.cache/modelscope/hub/models/<model_id>/`
3. ModelScope online download

The model files are intentionally ignored by git. Keep this README tracked so the directory exists in fresh clones.