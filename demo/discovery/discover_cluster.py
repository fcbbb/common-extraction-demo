"""CLI: run tool-signal discovery + LLM gate for clusters -> final discovery.json.

Per cluster writes under results/<dataset>/signal/<cluster_id>/:
  discovery_candidates.json  tool candidates (audit)
  discovery_evidence.json    per-signal evidence (audit)
  gate_*.txt/json            LLM gate artifacts (when not --skip-gate)
  discovery.json             final valid clusters + noise (baseline_b-compatible)
  status.json                cluster-stage status
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from demo.baselines.json_utils import require_discovery_schema
from demo.baselines.runner_utils import load_json_if_exists, load_manifest, selected_clusters, write_json

from . import ast_signal, clone_signal, fusion, semantic_signal

ROOT = Path(__file__).resolve().parents[2]


def _cluster_sources(dataset_dir: Path, cluster: dict[str, Any]) -> list[dict[str, str]]:
    cid = cluster["cluster_id"]
    out = []
    for f in cluster["files"]:
        path = dataset_dir / "clusters" / cid / "original" / f["file_id"]
        out.append({"file_id": f["file_id"], "source_code": path.read_text(encoding="utf-8")})
    return out


def run_discovery(
    sources: list[dict[str, str]],
    cluster_id: str,
    dataset_key: str,
    skip_semantic: bool,
    embedding_model: str,
    force_semantic: bool,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return (signal_evidences, evidence_audit). S3 cached unless force_semantic."""
    t0 = time.time()
    s1 = clone_signal.cluster_evidence(sources)
    s2 = ast_signal.cluster_evidence(sources)
    evidences = [s1, s2]
    semantic: dict[str, Any] = {}
    if not skip_semantic:
        semantic = semantic_signal.cluster_evidence(
            sources,
            dataset_key=dataset_key,
            cluster_id=cluster_id,
            model=embedding_model,
            force=force_semantic,
        )
        evidences.append(semantic)
    audit = {
        "signal": "evidence",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "clone": {
            "n_pairs": len(s1["pairs"]),
            "per_file_tokens": s1["per_file_tokens"],
            "parse_errors": s1.get("parse_errors", {}),
        },
        "skeleton": {"n_pairs": len(s2["pairs"])},
        "semantic": {"n_pairs": len(semantic.get("pairs", []))} if semantic else {"skipped": True},
        "elapsed_sec": round(time.time() - t0, 2),
    }
    return evidences, audit


def discover_cluster(
    cluster: dict[str, Any],
    dataset_dir: Path,
    results_dir: Path,
    cfg: dict[str, Any],
    skip_semantic: bool,
    skip_gate: bool,
    embedding_model: str,
    force_semantic: bool,
    resume: bool,
    gate_cfg: dict[str, Any],
) -> dict[str, Any]:
    from . import gate  # noqa: PLC0415 - optional import keeps CLI importable pre-gate

    cluster_id = cluster["cluster_id"]
    out_dir = results_dir / "signal" / cluster_id
    file_ids = [f["file_id"] for f in cluster["files"]]
    sources = _cluster_sources(dataset_dir, cluster)
    if resume and (load_json_if_exists(out_dir / "discovery_candidates.json") is not None):
        candidates_doc = load_json_if_exists(out_dir / "discovery_candidates.json")
        print(f"signal cluster {cluster_id}: resume hit, reusing discovery_candidates.json", file=sys.stderr, flush=True)
    else:
        evidences, audit = run_discovery(sources, cluster_id, dataset_dir.name, skip_semantic, embedding_model, force_semantic)
        fused = fusion.fuse(evidences, file_ids, cfg=cfg)
        candidates_doc = {
            "cluster_id": cluster_id,
            "n_files": len(file_ids),
            "n_candidates": len(fused["candidates"]),
            "candidates": fused["candidates"],
            "noise": fused["noise"],
            "edges_total": fused["edges_total"],
            "edges_kept": fused["edges_kept"],
            "config": fused["config"],
        }
        write_json(out_dir / "discovery_candidates.json", candidates_doc)
        write_json(out_dir / "discovery_evidence.json", audit)
    if resume and load_json_if_exists(out_dir / "discovery.json") is not None:
        final_doc = load_json_if_exists(out_dir / "discovery.json")
        print(f"signal cluster {cluster_id}: resume hit, reusing discovery.json", file=sys.stderr, flush=True)
    else:
        final_doc = gate.run_gate(cluster, sources, candidates_doc, out_dir, skip_gate=skip_gate, cfg=gate_cfg)
    if final_doc is None:
        status = {"signal": "signal", "cluster_id": cluster_id, "stage": "discovery", "status": "candidates_ready", "n_candidates": candidates_doc["n_candidates"]}
    else:
        require_discovery_schema(final_doc)
        write_json(out_dir / "discovery.json", final_doc)
        status = {
            "signal": "signal",
            "cluster_id": cluster_id,
            "stage": "discovery",
            "status": "ok",
            "n_candidates": candidates_doc["n_candidates"],
            "valid_clusters": len(final_doc.get("clusters", [])),
            "noise_files": len(final_doc.get("noise", [])),
        }
    write_json(out_dir / "status.json", status)
    return status


def main() -> None:
    parser = argparse.ArgumentParser(description="Signal discovery + LLM gate.")
    parser.add_argument("--manifest", type=Path, default=ROOT / "demo" / "datasets" / "codecontest" / "cluster_manifest.json")
    parser.add_argument("--dataset-dir", type=Path, default=ROOT / "demo" / "datasets" / "codecontest")
    parser.add_argument("--results-dir", type=Path, default=ROOT / "demo" / "results" / "codecontest")
    parser.add_argument("--cluster-id", action="append")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--embedding-model", default=semantic_signal.DEFAULT_MODEL)
    parser.add_argument("--skip-semantic", action="store_true")
    parser.add_argument("--force-semantic", action="store_true", help="Re-run embedding even when cached.")
    parser.add_argument("--skip-gate", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--api-timeout-sec", type=float, default=None)
    parser.add_argument("--max-output-tokens", type=int, default=None)
    args = parser.parse_args()
    manifest = load_manifest(args.manifest)
    clusters = selected_clusters(manifest, args.cluster_id, args.limit)
    # Candidate cfg (frozen defaults; tuned on codecontest clusters 0/1, eyeball vs baseline_b).
    cfg = fusion.DEFAULT_CFG
    gate_cfg = {"api_timeout_sec": args.api_timeout_sec, "max_output_tokens": args.max_output_tokens}
    statuses = []
    for cluster in clusters:
        t0 = time.time()
        try:
            st = discover_cluster(
                cluster,
                args.dataset_dir,
                args.results_dir,
                cfg,
                args.skip_semantic,
                args.skip_gate,
                args.embedding_model,
                args.force_semantic,
                args.resume,
                gate_cfg,
            )
        except Exception as exc:
            st = {"signal": "signal", "cluster_id": cluster["cluster_id"], "stage": "discovery", "status": "failed", "error": repr(exc)}
            write_json(args.results_dir / "signal" / cluster["cluster_id"] / "status.json", st)
        print(f"signal cluster {cluster['cluster_id']}: {json.dumps(st, ensure_ascii=False)} elapsed {time.time() - t0:.1f}s")
        statuses.append(st)
    write_json(args.results_dir / "signal" / "discovery_summary.json", statuses)


if __name__ == "__main__":
    main()
