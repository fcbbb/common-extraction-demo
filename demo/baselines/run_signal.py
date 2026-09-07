"""Signal-method runner: discovery (signals + gate) then extraction via run_subcluster.

Mirrors run_baseline_b.py; the only differences are the method directory ("signal")
and that discovery.json is produced by demo.discovery (tool signals + LLM gate)
instead of one LLM call.  Extraction reuses run_baseline_b.run_subcluster untouched,
so subcluster artifacts have the same layout and the same eval can scan both methods.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from demo.baselines.llm_client import DeepSeekClient, LLMConfigurationError
from demo.baselines.run_baseline_b import progress, run_subcluster, sanitize_subcluster
from demo.discovery import fusion
from demo.discovery.semantic_signal import DEFAULT_MODEL as DEFAULT_EMBEDDING_MODEL
from demo.baselines.runner_utils import (
    load_json_if_exists,
    load_manifest,
    selected_clusters,
    status_payload,
    write_json,
)
from demo.discovery.discover_cluster import discover_cluster

ROOT = Path(__file__).resolve().parents[2]


def run_cluster(
    client: DeepSeekClient | None,
    dataset_dir: Path,
    results_dir: Path,
    cluster: dict[str, Any],
    timeout_sec: float,
    test_limit: int | None,
    compare_mode: str,
    normalize: str,
    test_mode: str,
    resume: bool,
    rerun_metrics: bool,
    skip_metrics: bool,
    discovery_only: bool,
    cfg: dict[str, Any],
    embedding_model: str,
    gate_cfg: dict[str, Any],
) -> dict[str, Any]:
    cluster_id = cluster["cluster_id"]
    cluster_out_dir = results_dir / "signal" / cluster_id
    try:
        previous_cluster = load_json_if_exists(cluster_out_dir / "status.json")
        previous_discovery = load_json_if_exists(cluster_out_dir / "discovery.json")
        if (
            resume
            and previous_cluster
            and previous_cluster.get("status") == "ok"
            and previous_discovery
            and (not rerun_metrics or skip_metrics)
        ):
            print(
                f"signal cluster {cluster_id}: resume hit, skipping cluster",
                file=sys.stderr,
                flush=True,
            )
            return previous_cluster
        if resume and previous_discovery:
            print(f"signal cluster {cluster_id}: resume hit, reusing discovery", file=sys.stderr, flush=True)
            discovery = previous_discovery
        else:
            discovery_status = discover_cluster(
                cluster,
                dataset_dir,
                results_dir,
                cfg,
                skip_semantic=False,
                skip_gate=False,
                embedding_model=embedding_model,
                force_semantic=False,
                resume=resume,
                gate_cfg=gate_cfg,
            )
            discovery = load_json_if_exists(cluster_out_dir / "discovery.json")
            if discovery is None:
                result = {
                    "baseline": "signal",
                    "cluster_id": cluster_id,
                    **status_payload("failed", f"discovery produced no valid clusters: {discovery_status}"),
                }
                write_json(cluster_out_dir / "status.json", result)
                return result
        if discovery_only:
            result = {
                "baseline": "signal",
                "cluster_id": cluster_id,
                "status": "ok",
                "discovery_only": True,
                "valid_subclusters": len([c for c in discovery.get("clusters", []) if len(c.get("members", [])) >= 2]),
            }
            write_json(cluster_out_dir / "status.json", result)
            return result
        cluster_files = {item["file_id"] for item in cluster["files"]}
        subclusters = [
            sanitize_subcluster(item, cluster_files, index)
            for index, item in enumerate(discovery.get("clusters", []))
        ]
        valid = [item for item in subclusters if len(item["members"]) >= 2]
        sub_results = []
        total_subclusters = len(valid)
        for sub_index, subcluster in enumerate(valid, 1):
            progress(f"signal cluster {cluster_id} subclusters", sub_index - 1, total_subclusters)
            try:
                sub_result = run_subcluster(
                    client,
                    cluster,
                    dataset_dir,
                    cluster_out_dir,
                    subcluster,
                    timeout_sec,
                    test_limit,
                    compare_mode,
                    normalize,
                    test_mode,
                    resume,
                    rerun_metrics,
                    skip_metrics,
                )
            except Exception as exc:
                sub_result = {
                    "baseline": "signal",
                    "cluster_id": cluster_id,
                    "subcluster_id": subcluster["cluster_id"],
                    "members": subcluster["members"],
                    **status_payload("failed", repr(exc)),
                }
            write_json(cluster_out_dir / subcluster["cluster_id"] / "status.json", sub_result)
            sub_results.append(sub_result)
            progress(f"signal cluster {cluster_id} subclusters", sub_index, total_subclusters)
        covered = set().union(*(set(item["members"]) for item in valid)) if valid else set()
        cluster_status = "ok" if all(item.get("status") == "ok" for item in sub_results) else "failed"
        result = {
            "baseline": "signal",
            "cluster_id": cluster_id,
            "status": cluster_status,
            "discovered_subclusters": len(subclusters),
            "valid_subclusters": len(valid),
            "noise_files": sorted((set(discovery.get("noise") or []) | (cluster_files - covered)) & cluster_files),
            "subclusters": sub_results,
        }
    except Exception as exc:
        result = {"baseline": "signal", "cluster_id": cluster_id, **status_payload("failed", repr(exc))}
    write_json(cluster_out_dir / "status.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Run signal discovery then extraction.")
    parser.add_argument("--manifest", type=Path, default=ROOT / "demo" / "datasets" / "codecontest" / "cluster_manifest.json")
    parser.add_argument("--dataset-dir", type=Path, default=ROOT / "demo" / "datasets" / "codecontest")
    parser.add_argument("--results-dir", type=Path, default=ROOT / "demo" / "results" / "codecontest")
    parser.add_argument("--cluster-id", action="append")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--timeout-sec", type=float, default=5.0)
    parser.add_argument("--api-timeout-sec", type=float, default=None)
    parser.add_argument("--max-output-tokens", type=int, default=None)
    parser.add_argument("--embedding-model", type=str, default="")
    parser.add_argument("--test-mode", choices=["stdio", "pytest"], default="stdio")
    parser.add_argument("--test-limit", type=int, default=0, help="Per-file test limit. Use 0 for all tests.")
    parser.add_argument("--compare-mode", choices=["expected", "original"], default="original")
    parser.add_argument("--normalize", choices=["strip", "whitespace"], default="whitespace")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--rerun-metrics", action="store_true")
    parser.add_argument("--skip-metrics", action="store_true")
    parser.add_argument("--discovery-only", action="store_true", help="Stop after discovery.json; skip extraction.")
    args = parser.parse_args()

    manifest = load_manifest(args.manifest)
    clusters = selected_clusters(manifest, args.cluster_id, args.limit)
    test_limit = None if args.test_limit == 0 else args.test_limit
    cfg = fusion.DEFAULT_CFG
    embedding_model = args.embedding_model or DEFAULT_EMBEDDING_MODEL
    gate_cfg = {"api_timeout_sec": args.api_timeout_sec, "max_output_tokens": args.max_output_tokens}

    def cluster_needs_api(cluster: dict[str, Any]) -> bool:
        if not args.resume or args.discovery_only:
            return not (args.discovery_only and load_json_if_exists(args.results_dir / "signal" / cluster["cluster_id"] / "discovery.json"))
        cluster_id = cluster["cluster_id"]
        cluster_out_dir = args.results_dir / "signal" / cluster_id
        cluster_status = load_json_if_exists(cluster_out_dir / "status.json") or {}
        discovery = load_json_if_exists(cluster_out_dir / "discovery.json")
        if not discovery:
            return True
        cluster_files = {item["file_id"] for item in cluster["files"]}
        subclusters = [
            sanitize_subcluster(item, cluster_files, index)
            for index, item in enumerate(discovery.get("clusters", []))
        ]
        valid = [item for item in subclusters if len(item["members"]) >= 2]
        return any(
            (load_json_if_exists(cluster_out_dir / subcluster["cluster_id"] / "status.json") or {}).get("status") != "ok"
            for subcluster in valid
        )

    needs_api = any(cluster_needs_api(cluster) for cluster in clusters)
    try:
        client = DeepSeekClient(timeout_sec=args.api_timeout_sec, max_tokens=args.max_output_tokens) if needs_api else None
    except LLMConfigurationError as exc:
        for cluster in clusters:
            out_dir = args.results_dir / "signal" / cluster["cluster_id"]
            write_json(out_dir / "status.json", {"baseline": "signal", "cluster_id": cluster["cluster_id"], **status_payload("not_run", str(exc))})
        raise SystemExit(str(exc))

    results = []
    total = len(clusters)
    for index, cluster in enumerate(clusters, 1):
        progress("signal clusters", index - 1, total)
        results.append(run_cluster(
            client,
            args.dataset_dir,
            args.results_dir,
            cluster,
            args.timeout_sec,
            test_limit,
            args.compare_mode,
            args.normalize,
            args.test_mode,
            args.resume,
            args.rerun_metrics,
            args.skip_metrics,
            args.discovery_only,
            cfg,
            embedding_model,
            gate_cfg,
        ))
        progress("signal clusters", index, total)
    write_json(args.results_dir / "signal" / "summary.json", results)


if __name__ == "__main__":
    main()
