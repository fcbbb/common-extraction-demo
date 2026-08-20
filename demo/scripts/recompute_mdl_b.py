"""Recompute MDL for baseline_b subclusters with member-scoped before/after,
loading the reference LM once. Patches metrics.json / mdl_metrics.json /
summary.json in place.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, "/home/xiaoheng/demo_common_extraction")
from demo.eval.measure_mdl import concat_files, negative_log_likelihood

MODEL_PATH = Path("/home/xiaoheng/demo_common_extraction/model/Qwen3.8-27B-Q4_K_M.gguf")
N_CTX = 4096
N_GPU_LAYERS = -1
N_BATCH = 256

from llama_cpp import Llama

print("Loading reference LM once ...", flush=True)
llm = Llama(
    model_path=str(MODEL_PATH),
    n_gpu_layers=N_GPU_LAYERS,
    n_ctx=N_CTX,
    n_batch=N_BATCH,
    logits_all=True,
    verbose=False,
)
print("Model loaded.", flush=True)

reference_lm = {
    "backend": "llama-cpp-python",
    "model_path": str(MODEL_PATH),
    "n_ctx": N_CTX,
    "n_gpu_layers": N_GPU_LAYERS,
    "n_batch": N_BATCH,
}


def measure(original_dir: Path, result_dir: Path, members: list[str]) -> dict:
    orig_files = sorted(original_dir.glob("file_*.py"))
    ref_files = sorted((result_dir / "refactored").glob("file_*.py"))
    ids = set(members)
    orig_files = [f for f in orig_files if f.name in ids]
    ref_files = [f for f in ref_files if f.name in ids]
    before_text = concat_files(orig_files)
    after_text = concat_files([result_dir / "common.py", *ref_files])
    before = negative_log_likelihood(llm, before_text, N_CTX)
    after = negative_log_likelihood(llm, after_text, N_CTX)
    return {
        "status": "ok",
        "reference_lm": reference_lm,
        "mdl_before": float(before["nll"]),
        "mdl_after": float(after["nll"]),
        "mdl_compression": 1 - float(after["nll"]) / float(before["nll"]) if before["nll"] else None,
        "before": before,
        "after": after,
        "files_before": len(orig_files),
        "files_after": len(ref_files),
        "after_includes_common": (result_dir / "common.py").exists(),
    }


for ds, results_dir, dataset_dir in [
    ("code", "demo/results/codecontest", "demo/datasets/codecontest"),
    ("complex", "demo/results/complex", "demo/datasets/complex"),
]:
    summary_path = Path(results_dir) / "baseline_b" / "summary.json"
    summary = json.load(open(summary_path))
    for cluster in summary:
        cid = cluster["cluster_id"]
        orig_dir = Path(dataset_dir) / "clusters" / cid / "original"
        for sub in cluster.get("subclusters", []):
            out_dir = Path(results_dir) / "baseline_b" / cid / sub["subcluster_id"]
            if not (out_dir / "common.py").exists() or not (out_dir / "refactored").exists():
                continue
            mdl = measure(orig_dir, out_dir, sub["members"])
            metrics_path = out_dir / "metrics.json"
            metrics = json.load(open(metrics_path))
            metrics["mdl"] = mdl
            (out_dir / "mdl_metrics.json").write_text(json.dumps(mdl, indent=2) + "\n")
            metrics_path.write_text(json.dumps(metrics, indent=2) + "\n")
            sub["metrics"]["mdl"] = mdl
            print(f"{ds} {cid}/{sub['subcluster_id']}: mdl_compression={mdl['mdl_compression']:.4f} "
                  f"({mdl['files_before']} files -> {mdl['files_after']} files)", flush=True)
    summary_path.write_text(json.dumps(summary, indent=2) + "\n")
    print(f"=== {ds} summary updated: {summary_path}", flush=True)
print("ALL DONE", flush=True)
