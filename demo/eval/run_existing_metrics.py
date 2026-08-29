from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from demo.baselines.runner_utils import (
    behavior_tests_ok,
    compile_result,
    load_json_if_exists,
    load_manifest,
    run_metrics,
    selected_clusters,
    write_json,
)
from demo.baselines.run_baseline_b import sanitize_subcluster


ROOT = Path(__file__).resolve().parents[2]


def update_status_with_metrics(status_path: Path, compile_info: dict[str, Any], metrics: dict[str, Any] | None) -> dict[str, Any]:
    status = load_json_if_exists(status_path) or {}
    status["status"] = (
        "compile_failed" if not compile_info.get("ok")
        else "tests_failed" if not behavior_tests_ok(metrics)
        else "ok"
    )
    status["compile"] = compile_info
    status["metrics"] = metrics
    write_json(status_path, status)
    return status


def run_baseline_a_metrics(
    clusters: list[dict[str, Any]],
    dataset_dir: Path,
    results_dir: Path,
    timeout_sec: float,
    test_limit: int | None,
    compare_mode: str,
    normalize: str,
    test_mode: str,
    workers: int | None = None,
) -> list[dict[str, Any]]:
    results = []
    for cluster in clusters:
        cluster_id = cluster["cluster_id"]
        out_dir = results_dir / "baseline_a" / cluster_id
        status_path = out_dir / "status.json"
        if not (out_dir / "common.py").exists() or not (out_dir / "refactored").exists():
            result = {
                "baseline": "baseline_a",
                "cluster_id": cluster_id,
                "status": "missing_extraction",
                "reason": f"No existing extraction under {out_dir}",
            }
            write_json(status_path, result)
            results.append(result)
            continue
        compile_info = compile_result(out_dir)
        metrics = (
            run_metrics(
                cluster,
                dataset_dir,
                out_dir,
                timeout_sec,
                test_limit=test_limit,
                compare_mode=compare_mode,
                normalize=normalize,
                test_mode=test_mode,
                workers=workers,
            )
            if compile_info["ok"]
            else None
        )
        results.append(update_status_with_metrics(status_path, compile_info, metrics))
    write_json(results_dir / "baseline_a" / "summary.json", results)
    return results


def run_baseline_b_metrics(
    clusters: list[dict[str, Any]],
    dataset_dir: Path,
    results_dir: Path,
    timeout_sec: float,
    test_limit: int | None,
    compare_mode: str,
    normalize: str,
    test_mode: str,
    workers: int | None = None,
) -> list[dict[str, Any]]:
    cluster_results = []
    for cluster in clusters:
        cluster_id = cluster["cluster_id"]
        cluster_out_dir = results_dir / "baseline_b" / cluster_id
        discovery = load_json_if_exists(cluster_out_dir / "discovery.json")
        if not discovery:
            result = {
                "baseline": "baseline_b",
                "cluster_id": cluster_id,
                "status": "missing_discovery",
                "reason": f"No existing discovery under {cluster_out_dir}",
            }
            write_json(cluster_out_dir / "status.json", result)
            cluster_results.append(result)
            continue

        cluster_files = {item["file_id"] for item in cluster["files"]}
        subclusters = [
            sanitize_subcluster(item, cluster_files, index)
            for index, item in enumerate(discovery.get("clusters", []))
        ]
        valid = [item for item in subclusters if len(item["members"]) >= 2]
        sub_results = []
        for subcluster in valid:
            sub_id = subcluster["cluster_id"]
            out_dir = cluster_out_dir / sub_id
            status_path = out_dir / "status.json"
            if not (out_dir / "common.py").exists() or not (out_dir / "refactored").exists():
                sub_result = {
                    "baseline": "baseline_b",
                    "cluster_id": cluster_id,
                    "subcluster_id": sub_id,
                    "members": subcluster["members"],
                    "status": "missing_extraction",
                    "reason": f"No existing extraction under {out_dir}",
                }
                write_json(status_path, sub_result)
                sub_results.append(sub_result)
                continue
            compile_info = compile_result(out_dir)
            metrics = (
                run_metrics(
                    cluster,
                    dataset_dir,
                    out_dir,
                    timeout_sec,
                    file_ids=subcluster["members"],
                    test_limit=test_limit,
                    compare_mode=compare_mode,
                    normalize=normalize,
                    test_mode=test_mode,
                    workers=workers,
                )
                if compile_info["ok"]
                else None
            )
            sub_results.append(update_status_with_metrics(status_path, compile_info, metrics))

        covered = set().union(*(set(item["members"]) for item in valid)) if valid else set()
        result = {
            "baseline": "baseline_b",
            "cluster_id": cluster_id,
            "status": "ok",
            "discovered_subclusters": len(subclusters),
            "valid_subclusters": len(valid),
            "noise_files": sorted((set(discovery.get("noise") or []) | (cluster_files - covered)) & cluster_files),
            "subclusters": sub_results,
        }
        write_json(cluster_out_dir / "status.json", result)
        cluster_results.append(result)
    write_json(results_dir / "baseline_b" / "summary.json", cluster_results)
    return cluster_results


def main() -> None:
    parser = argparse.ArgumentParser(description="Run tests and metrics on existing baseline extraction artifacts.")
    parser.add_argument("--manifest", type=Path, default=ROOT / "demo" / "datasets" / "codecontest" / "cluster_manifest.json")
    parser.add_argument("--dataset-dir", type=Path, default=ROOT / "demo" / "datasets" / "codecontest")
    parser.add_argument("--results-dir", type=Path, default=ROOT / "demo" / "results" / "codecontest")
    parser.add_argument("--baseline", choices=["a", "b", "both"], default="a")
    parser.add_argument("--cluster-id", action="append")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--timeout-sec", type=float, default=5.0)
    parser.add_argument("--test-limit", type=int, default=0, help="Per-file test limit. Use 0 for all tests.")
    parser.add_argument("--compare-mode", choices=["expected", "original"], default="original")
    parser.add_argument("--normalize", choices=["strip", "whitespace"], default="whitespace")
    parser.add_argument("--test-mode", choices=["stdio", "pytest"], default="stdio", help="pytest for the dataset_complex Scrapy slice.")
    parser.add_argument("--workers", type=int, default=0, help="Parallel test workers. 0 = auto (min(cpu_count, 16)). Override with TEST_WORKERS env.")
    args = parser.parse_args()

    manifest = load_manifest(args.manifest)
    clusters = selected_clusters(manifest, args.cluster_id, args.limit)
    test_limit = None if args.test_limit == 0 else args.test_limit
    workers = args.workers if args.workers > 0 else None
    if args.baseline in {"a", "both"}:
        run_baseline_a_metrics(clusters, args.dataset_dir, args.results_dir, args.timeout_sec, test_limit, args.compare_mode, args.normalize, args.test_mode, workers)
    if args.baseline in {"b", "both"}:
        run_baseline_b_metrics(clusters, args.dataset_dir, args.results_dir, args.timeout_sec, test_limit, args.compare_mode, args.normalize, args.test_mode, workers)


if __name__ == "__main__":
    main()
