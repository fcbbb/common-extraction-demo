"""S3 semantic-signal worker: embed code units with C2LLM-0.5B, emit pairwise cosine.

Runs in a torch-capable env (never in the main qwen-gguf env).  Invoked by
semantic_signal.py as a subprocess:
    python -m demo.discovery.semantic_signal_worker --input in.json --output out.json [--model M]
Input:  {"files": [{"file_id": ..., "source_code": ...}]}
Output: {"model": ..., "units": [{"file_id", "unit_id"}],
         "pairs": [{"file_a", "unit_a", "file_b", "unit_b", "cosine"}]}
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from .units import CONTAINER_TYPES, method_containers, parse_units

# The model is downloaded into the Hugging Face cache on first use.  A local
# path can be supplied with --embedding-model or DEMO_EMBED_PYTHON can point to
# a dedicated environment.
DEFAULT_MODEL = "codefuse-ai/C2LLM-0.5B"
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


def _line_text(lines: list[str], start: int, end: int) -> str:
    return "".join(lines[start - 1:end])


def _unit_sources(files: list[dict[str, str]]) -> list[dict[str, str]]:
    """Split files into top-level units, class methods, and module remainder."""
    result: list[dict[str, str]] = []
    for entry in files:
        file_id, source = entry["file_id"], entry["source_code"]
        tree, units, error = parse_units(source)
        if tree is None or units is None or error:
            if source.strip():
                result.append({"file_id": file_id, "unit_id": "module", "source_code": source})
            continue
        lines = source.splitlines(keepends=True)
        covered: list[tuple[int, int]] = []
        for node in tree.body:
            if not isinstance(node, CONTAINER_TYPES):
                continue
            decorator_rows = [decorator.lineno for decorator in getattr(node, "decorator_list", [])]
            start = min([node.lineno, *decorator_rows])
            end = node.end_lineno or node.lineno
            covered.append((start, end))
            kind = "function" if node.__class__.__name__ in {"FunctionDef", "AsyncFunctionDef"} else "class"
            text = _line_text(lines, start, end)
            if text.strip():
                result.append({"file_id": file_id, "unit_id": f"{kind}:{node.name}", "source_code": text})
        for unit_id, node in method_containers(tree):
            decorator_rows = [decorator.lineno for decorator in getattr(node, "decorator_list", [])]
            start = min([node.lineno, *decorator_rows])
            end = node.end_lineno or node.lineno
            text = _line_text(lines, start, end)
            if text.strip():
                result.append({"file_id": file_id, "unit_id": unit_id, "source_code": text})
        module_lines = [
            line for index, line in enumerate(lines, 1)
            if not any(start <= index <= end for start, end in covered)
        ]
        module_text = "".join(module_lines)
        if module_text.strip():
            result.append({"file_id": file_id, "unit_id": "module", "source_code": module_text})
    return result


def cosine_matrix(sources: list[dict[str, str]], model_name: str) -> dict[str, Any]:
    unit_sources = _unit_sources(sources)
    if not unit_sources:
        return {"model": model_name, "units": [], "pairs": []}
    _, embs = _embed(unit_sources, model_name)
    try:
        import numpy as np
    except ImportError:  # pragma: no cover
        np = None  # type: ignore[assignment]
    if np is None or not hasattr(embs, "shape"):
        raise RuntimeError("embedding output not numpy")
    units = [{"file_id": s["file_id"], "unit_id": s["unit_id"]} for s in unit_sources]
    pairs = []
    for i in range(len(units)):
        for j in range(i + 1, len(units)):
            if units[i]["file_id"] == units[j]["file_id"]:
                continue
            pairs.append({
                "file_a": units[i]["file_id"],
                "unit_a": units[i]["unit_id"],
                "file_b": units[j]["file_id"],
                "unit_b": units[j]["unit_id"],
                "cosine": round(float(embs[i] @ embs[j]), 4),
            })
    pairs.sort(key=lambda p: -p["cosine"])
    return {"model": model_name, "units": units, "pairs": pairs}


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
    print(f"[semantic] done: {len(out['pairs'])} unit pairs via {out['model']}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
