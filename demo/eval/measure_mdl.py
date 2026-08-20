from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]


def concat_files(paths: list[Path]) -> str:
    return "\n\n".join(path.read_text(encoding="utf-8") for path in paths if path.exists())


def stable_logsumexp(values: Any) -> float:
    max_value = float(max(values))
    return max_value + math.log(sum(math.exp(float(value) - max_value) for value in values))


def negative_log_likelihood(llm: Any, text: str, n_ctx: int) -> dict[str, object]:
    tokens = llm.tokenize(text.encode("utf-8"), add_bos=True)
    if len(tokens) < 2:
        return {"nll": 0.0, "tokens_scored": 0, "tokens_total": len(tokens)}

    chunk_payload = max(1, n_ctx - 1)
    nll = 0.0
    scored = 0
    chunks = 0

    for start in range(1, len(tokens), chunk_payload):
        chunk = [tokens[0], *tokens[start : start + chunk_payload]]
        if len(chunk) < 2:
            continue
        llm.reset()
        llm.eval(chunk)
        scores = llm.scores
        for idx in range(1, len(chunk)):
            target = chunk[idx]
            logits = scores[idx - 1]
            nll += stable_logsumexp(logits) - float(logits[target])
            scored += 1
        chunks += 1

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
    before_text = concat_files(original_files)
    after_text = concat_files([result_dir / "common.py", *refactored_files])
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

    llm = Llama(
        model_path=str(model_path),
        n_gpu_layers=resolved_n_gpu_layers,
        n_ctx=resolved_n_ctx,
        n_batch=resolved_n_batch,
        logits_all=True,
        verbose=resolved_verbose,
    )

    before = negative_log_likelihood(llm, before_text, resolved_n_ctx)
    after = negative_log_likelihood(llm, after_text, resolved_n_ctx)
    mdl_before = float(before["nll"])
    mdl_after = float(after["nll"])
    return {
        "status": "ok",
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
