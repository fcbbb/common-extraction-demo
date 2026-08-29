from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from pathlib import Path
from typing import Any

from demo.eval.measure_tokens import strip_non_code


ROOT = Path(__file__).resolve().parents[2]


def concat_files(paths: list[Path]) -> str:
    return "\n\n".join(path.read_text(encoding="utf-8") for path in paths if path.exists())


def stable_logsumexp(values: Any) -> float:
    max_value = float(max(values))
    return max_value + math.log(sum(math.exp(float(value) - max_value) for value in values))


def log_progress(message: str) -> None:
    print(f"[MDL] {message}", file=sys.stderr, flush=True)


def batched_nll(scores: Any, targets: list[int], batch_size: int) -> float:
    """Compute token NLL with NumPy instead of Python loops over the vocabulary."""
    import numpy as np

    target_array = np.asarray(targets, dtype=np.intp)
    logits = np.asarray(scores, dtype=np.float32)
    # llama.cpp may expose the whole preallocated score buffer.  The final
    # context chunk can contain fewer tokens than that buffer, so only the
    # rows corresponding to the requested target tokens are valid here.
    logits = logits[: len(target_array)]
    if logits.ndim != 2 or logits.shape[0] != len(target_array):
        raise ValueError(
            f"Expected one score row per target token, got scores shape {logits.shape} "
            f"for {len(target_array)} targets"
        )
    total = 0.0
    for start in range(0, len(target_array), batch_size):
        block = logits[start : start + batch_size]
        block_targets = target_array[start : start + batch_size]
        row_max = np.max(block, axis=1)
        with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
            log_z = row_max + np.log(
                np.exp(block - row_max[:, None]).sum(axis=1, dtype=np.float64)
            )
        row_indices = np.arange(len(block_targets))
        total += float(
            np.sum(log_z - block[row_indices, block_targets], dtype=np.float64)
        )
    return total


def negative_log_likelihood(
    llm: Any,
    text: str,
    n_ctx: int,
    label: str = "NLL",
    score_batch_size: int = 64,
) -> dict[str, object]:
    tokens = llm.tokenize(text.encode("utf-8"), add_bos=True)
    if len(tokens) < 2:
        return {"nll": 0.0, "tokens_scored": 0, "tokens_total": len(tokens)}
    if score_batch_size <= 0:
        raise ValueError(f"score_batch_size must be positive, got {score_batch_size}")

    chunk_payload = max(1, n_ctx - 1)
    total_tokens = len(tokens) - 1
    total_chunks = (total_tokens + chunk_payload - 1) // chunk_payload
    nll = 0.0
    scored = 0
    chunks = 0
    started = time.monotonic()

    for start in range(1, len(tokens), chunk_payload):
        chunk = [tokens[0], *tokens[start : start + chunk_payload]]
        if len(chunk) < 2:
            continue
        chunk_tokens = len(chunk) - 1
        log_progress(
            f"{label}: starting chunk {chunks + 1}/{total_chunks} "
            f"({scored}/{total_tokens} tokens complete)"
        )
        llm.reset()
        llm.eval(chunk)
        nll += batched_nll(llm.scores, chunk[1:], score_batch_size)
        scored += chunk_tokens
        chunks += 1
        elapsed = time.monotonic() - started
        rate = scored / elapsed if elapsed else 0.0
        log_progress(
            f"{label}: finished chunk {chunks}/{total_chunks} "
            f"({scored}/{total_tokens} tokens, {rate:.1f} tokens/s, {elapsed:.1f}s elapsed)"
        )

    return {
        "nll": nll,
        "tokens_scored": scored,
        "tokens_total": len(tokens),
        "chunks": chunks,
    }


def measure_mdl(
    original_dir: Path | None = None,
    result_dir: Path | None = None,
    model_path: Path | None = None,
    n_ctx: int | None = None,
    n_gpu_layers: int | None = None,
    n_batch: int | None = None,
    verbose: bool | None = None,
    file_ids: list[str] | None = None,
) -> dict[str, object]:
    env_model_path = os.getenv("MDL_MODEL_PATH") or os.getenv("MODEL_PATH")
    model_path = model_path or (Path(env_model_path) if env_model_path else None)
    if model_path is None:
        return {
            "status": "not_available",
            "reason": "Set MDL_MODEL_PATH or pass --model-path to run a logprob-capable GGUF reference LM.",
            "mdl_before": None,
            "mdl_after": None,
            "mdl_compression": None,
        }

    original_dir = original_dir or (
        Path(os.environ["MDL_ORIGINAL_DIR"]) if os.getenv("MDL_ORIGINAL_DIR") else None
    )
    result_dir = result_dir or (Path(os.environ["MDL_RESULT_DIR"]) if os.getenv("MDL_RESULT_DIR") else None)
    if original_dir is None or result_dir is None:
        return {
            "status": "not_available",
            "reason": "Pass --original-dir and --result-dir, or set MDL_ORIGINAL_DIR and MDL_RESULT_DIR.",
            "mdl_before": None,
            "mdl_after": None,
            "mdl_compression": None,
        }

    if not model_path.exists():
        return {
            "status": "error",
            "reason": f"Model file does not exist: {model_path}",
            "mdl_before": None,
            "mdl_after": None,
            "mdl_compression": None,
        }

    original_files = sorted(original_dir.glob("file_*.py"))
    refactored_files = sorted((result_dir / "refactored").glob("file_*.py"))
    if file_ids is not None:
        ids = set(file_ids)
        original_files = [f for f in original_files if f.name in ids]
        refactored_files = [f for f in refactored_files if f.name in ids]
    before_text = strip_non_code(concat_files(original_files))
    after_text = strip_non_code(concat_files([result_dir / "common.py", *refactored_files]))
    if not before_text or not after_text:
        return {
            "status": "error",
            "reason": "Could not read both before and after source corpora.",
            "mdl_before": None,
            "mdl_after": None,
            "mdl_compression": None,
            "files_before": len(original_files),
            "files_after": len(refactored_files),
        }

    from llama_cpp import Llama

    resolved_n_ctx = n_ctx or int(os.getenv("N_CTX", "4096"))
    resolved_n_gpu_layers = n_gpu_layers if n_gpu_layers is not None else int(os.getenv("N_GPU_LAYERS", "-1"))
    resolved_n_batch = n_batch or int(os.getenv("N_BATCH", "256"))
    resolved_verbose = verbose if verbose is not None else os.getenv("LLAMA_VERBOSE", "0") == "1"
    score_batch_size = int(os.getenv("MDL_SCORE_BATCH_SIZE", "64"))
    if score_batch_size <= 0:
        return {
            "status": "error",
            "reason": f"MDL_SCORE_BATCH_SIZE must be positive, got {score_batch_size}",
            "mdl_before": None,
            "mdl_after": None,
            "mdl_compression": None,
        }

    log_progress(
        f"loading model {model_path} (comments/docstrings excluded, n_ctx={resolved_n_ctx}, "
        f"n_gpu_layers={resolved_n_gpu_layers}, n_batch={resolved_n_batch}, "
        f"score_batch_size={score_batch_size})"
    )
    llm = Llama(
        model_path=str(model_path),
        n_gpu_layers=resolved_n_gpu_layers,
        n_ctx=resolved_n_ctx,
        n_batch=resolved_n_batch,
        logits_all=True,
        verbose=resolved_verbose,
    )
    log_progress("model loaded; starting before NLL")

    before = negative_log_likelihood(
        llm, before_text, resolved_n_ctx, label="before", score_batch_size=score_batch_size
    )
    log_progress("before NLL complete; starting after NLL")
    after = negative_log_likelihood(
        llm, after_text, resolved_n_ctx, label="after", score_batch_size=score_batch_size
    )
    mdl_before = float(before["nll"])
    mdl_after = float(after["nll"])
    log_progress(
        f"after NLL complete; mdl_before={mdl_before:.3f}, mdl_after={mdl_after:.3f}"
    )
    return {
        "status": "ok",
        "comments_excluded": True,
        "docstrings_excluded": True,
        "reference_lm": {
            "backend": "llama-cpp-python",
            "model_path": str(model_path),
            "n_ctx": resolved_n_ctx,
            "n_gpu_layers": resolved_n_gpu_layers,
            "n_batch": resolved_n_batch,
        },
        "mdl_before": mdl_before,
        "mdl_after": mdl_after,
        "mdl_compression": 1 - (mdl_after / mdl_before) if mdl_before else None,
        "before": before,
        "after": after,
        "files_before": len(original_files),
        "files_after": len(refactored_files),
        "after_includes_common": (result_dir / "common.py").exists(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Measure MDL compression with a logprob-capable GGUF reference LM.")
    parser.add_argument("--original-dir", type=Path)
    parser.add_argument("--result-dir", type=Path)
    parser.add_argument("--model-path", type=Path)
    parser.add_argument("--n-ctx", type=int)
    parser.add_argument("--n-gpu-layers", type=int)
    parser.add_argument("--n-batch", type=int)
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    payload = json.dumps(
        measure_mdl(
            original_dir=args.original_dir,
            result_dir=args.result_dir,
            model_path=args.model_path,
            n_ctx=args.n_ctx,
            n_gpu_layers=args.n_gpu_layers,
            n_batch=args.n_batch,
            verbose=args.verbose,
        ),
        indent=2,
        ensure_ascii=False,
    )
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(payload + "\n", encoding="utf-8")
    else:
        print(payload)


if __name__ == "__main__":
    main()
