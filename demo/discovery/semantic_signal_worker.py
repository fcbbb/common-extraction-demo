"""S3 semantic-signal worker: embed code files with C2LLM-0.5B, emit pairwise cosine.

Runs in a torch-capable env (never in the main qwen-gguf env).  Invoked by
semantic_signal.py as a subprocess:
    python -m demo.discovery.semantic_signal_worker --input in.json --output out.json [--model M]
Input:  {"files": [{"file_id": ..., "source_code": ...}]}
Output: {"model": ..., "files": [file_id...], "pairs": [{"file_a", "file_b", "cosine"}]}
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

# Local vendored copy: upstream modeling imports peft/deepspeed for training-only
# paths that never run here; the copy strips those imports (see models/C2LLM-0.5B).
DEFAULT_MODEL = str(Path(__file__).resolve().parent / "models" / "C2LLM-0.5B")
FALLBACK_MODELS = ["Qwen/Qwen3-Embedding-0.6B"]


def _embed(sources: list[dict[str, str]], model_name: str) -> tuple[Any, list[Any]]:
    from sentence_transformers import SentenceTransformer  # noqa: PLC0415 (torch env)

    texts = [s["source_code"] for s in sources]
    for device in ("cuda", "cpu"):
        try:
            import torch  # noqa: PLC0415

            if device == "cuda" and not torch.cuda.is_available():
                continue
            model = SentenceTransformer(model_name, trust_remote_code=True, device=device)
            embs = model.encode(
                texts, batch_size=8, show_progress_bar=False, normalize_embeddings=True, convert_to_numpy=True
            )
            return model, embs
        except Exception as exc:  # noqa: BLE001
            if device == "cuda" and "out of memory" in str(exc).lower():
                print(f"[semantic] CUDA OOM, falling back to CPU ({exc})", flush=True)
                import gc  # noqa: PLC0415

                try:
                    del model
                except NameError:
                    pass
                gc.collect()
                torch.cuda.empty_cache()
                continue
            raise
    raise RuntimeError("no usable device")  # pragma: no cover


def cosine_matrix(sources: list[dict[str, str]], model_name: str) -> dict[str, Any]:
    _, embs = _embed(sources, model_name)
    try:
        import numpy as np
    except ImportError:  # pragma: no cover
        np = None  # type: ignore[assignment]
    if np is None or not hasattr(embs, "shape"):
        raise RuntimeError("embedding output not numpy")
    file_ids = [s["file_id"] for s in sources]
    pairs = []
    for i in range(len(file_ids)):
        for j in range(i + 1, len(file_ids)):
            pairs.append({
                "file_a": file_ids[i],
                "file_b": file_ids[j],
                "cosine": round(float(embs[i] @ embs[j]), 4),
            })
    pairs.sort(key=lambda p: -p["cosine"])
    return {"model": model_name, "files": file_ids, "pairs": pairs}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    args = ap.parse_args()
    with open(args.input) as fh:
        data = json.load(fh)
    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    last_err: Exception | None = None
    for model in [args.model, *FALLBACK_MODELS]:
        if model != args.model:
            print(f"[semantic] falling back to {model}", flush=True)
        try:
            out = cosine_matrix(data["files"], model)
            break
        except Exception as exc:  # noqa: BLE001 - fallback chain is the point
            last_err = exc
            print(f"[semantic] model {model} failed: {exc!r}", flush=True)
    else:
        print(f"[semantic] all models failed; last error: {last_err!r}", file=sys.stderr)
        return 1
    with open(args.output, "w") as fh:
        json.dump(out, fh)
    print(f"[semantic] done: {len(out['pairs'])} pairs via {out['model']}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
