from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from demo.eval.measure_mdl import concat_files, negative_log_likelihood
from demo.eval.measure_tokens import measure as measure_tokens
from llama_cpp import Llama

RESULTS_DIR = ROOT / "demo" / "results" / "codecontest"
DATASET_DIR = ROOT / "demo" / "datasets" / "codecontest"
MODEL_PATH = Path("/home/xiaoheng/demo_common_extraction/model/Qwen3.8-27B-Q4_K_M.gguf")
N_CTX = 4096
N_GPU_LAYERS = -1
N_BATCH = 256


def measure_mdl(llm: Llama, original_dir: Path, result_dir: Path, file_ids: list[str] | None = None) -> dict:
    original_files = sorted(original_dir.glob("file_*.py"))
    refactored_files = sorted((result_dir / "refactored").glob("file_*.py"))
    if file_ids is not None:
        ids = set(file_ids)
        original_files = [path for path in original_files if path.name in ids]
        refactored_files = [path for path in refactored_files if path.name in ids]
    before = negative_log_likelihood(llm, concat_files(original_files), N_CTX)
    after = negative_log_likelihood(llm, concat_files([result_dir / "common.py", *refactored_files]), N_CTX)
    before_nll = float(before["nll"])
    after_nll = float(after["nll"])
    return {
        "status": "ok",
        "reference_lm": {
            "backend": "llama-cpp-python",
            "model_path": str(MODEL_PATH),
            "n_ctx": N_CTX,
            "n_gpu_layers": N_GPU_LAYERS,
            "n_batch": N_BATCH,
        },
        "mdl_before": before_nll,
        "mdl_after": after_nll,
        "mdl_compression": 1 - after_nll / before_nll if before_nll else None,
        "before": before,
        "after": after,
        "files_before": len(original_files),
        "files_after": len(refactored_files),
        "after_includes_common": (result_dir / "common.py").exists(),
    }


def update(out_dir: Path, original_dir: Path, file_ids: list[str] | None, llm: Llama) -> None:
    metrics_path = out_dir / "metrics.json"
    if not metrics_path.exists():
        return
    metrics = json.loads(metrics_path.read_text())
    tokens = measure_tokens(original_dir, out_dir, file_ids=file_ids)
    mdl = measure_mdl(llm, original_dir, out_dir, file_ids=file_ids)
    metrics["tokens"] = tokens
    metrics["mdl"] = mdl
    (out_dir / "token_metrics.json").write_text(json.dumps(tokens, indent=2) + "\n")
    (out_dir / "mdl_metrics.json").write_text(json.dumps(mdl, indent=2) + "\n")
    metrics_path.write_text(json.dumps(metrics, indent=2) + "\n")
    for path in (out_dir / "status.json",):
        if path.exists():
            status = json.loads(path.read_text())
            if "metrics" in status and isinstance(status["metrics"], dict):
                status["metrics"]["tokens"] = tokens
                status["metrics"]["mdl"] = mdl
                path.write_text(json.dumps(status, indent=2) + "\n")


def main() -> None:
    if not MODEL_PATH.exists():
        raise SystemExit(f"Missing model: {MODEL_PATH}")
    print("Loading reference LM ...", flush=True)
    llm = Llama(
        model_path=str(MODEL_PATH),
        n_gpu_layers=N_GPU_LAYERS,
        n_ctx=N_CTX,
        n_batch=N_BATCH,
        logits_all=True,
        verbose=False,
    )
    manifest = json.loads((DATASET_DIR / "cluster_manifest.json").read_text())
    clusters = manifest["clusters"]
    for cluster in clusters:
        cid = cluster["cluster_id"]
        original_dir = DATASET_DIR / "clusters" / cid / "original"
        out_dir = RESULTS_DIR / "baseline_a" / cid
        if (out_dir / "common.py").exists():
            update(out_dir, original_dir, None, llm)
            print(f"baseline_a {cid}", flush=True)
        summary_path = RESULTS_DIR / "baseline_b" / "summary.json"
        cluster_summary_path = RESULTS_DIR / "baseline_b" / cid / "status.json"
        if not cluster_summary_path.exists():
            continue
        cluster_status = json.loads(cluster_summary_path.read_text())
        for sub in cluster_status.get("subclusters", []):
            sub_id = sub["subcluster_id"]
            sub_dir = RESULTS_DIR / "baseline_b" / cid / sub_id
            if (sub_dir / "common.py").exists():
                update(sub_dir, original_dir, sub.get("members"), llm)
                print(f"baseline_b {cid}/{sub_id}", flush=True)

    for base in ("baseline_a", "baseline_b"):
        summary_path = RESULTS_DIR / base / "summary.json"
        if not summary_path.exists():
            continue
        summary = json.loads(summary_path.read_text())
        for item in summary:
            cid = item["cluster_id"]
            if base == "baseline_a":
                metrics_path = RESULTS_DIR / base / cid / "metrics.json"
                if metrics_path.exists():
                    item["metrics"] = json.loads(metrics_path.read_text())
            else:
                for sub in item.get("subclusters", []):
                    metrics_path = RESULTS_DIR / base / cid / sub["subcluster_id"] / "metrics.json"
                    if metrics_path.exists():
                        sub["metrics"] = json.loads(metrics_path.read_text())
        summary_path.write_text(json.dumps(summary, indent=2) + "\n")


if __name__ == "__main__":
    main()
