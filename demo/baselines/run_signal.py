"""Signal-method runner: discovery (signals + gate) then extraction via run_subcluster.

Mirrors run_baseline_b.py; the only differences are the method directory ("signal")
and that discovery.json is produced by demo.discovery (tool signals + LLM gate)
instead of one LLM call.  Extraction reuses run_baseline_b.run_subcluster untouched,
so subcluster artifacts have the same layout and the same eval can scan both methods.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import copy
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
    audit_signal_edit_scopes,
    write_json,
)
from demo.discovery.discover_cluster import discover_cluster

ROOT = Path(__file__).resolve().parents[2]
SIGNAL_PROMPT_SET = {
    "common_system": ROOT / "demo" / "baselines" / "prompts" / "signal_common_system.txt",
    "common_user": ROOT / "demo" / "baselines" / "prompts" / "signal_common_user_template.txt",
    "refactor_system": ROOT / "demo" / "baselines" / "prompts" / "signal_refactor_all_system.txt",
    "refactor_user": ROOT / "demo" / "baselines" / "prompts" / "signal_refactor_all_user_template.txt",
    "unit_scoped": True,
}


def _run_subcluster_job(payload: dict[str, Any]) -> dict[str, Any]:
    """Run one independent extraction in a child process.

    Subcluster output is isolated under ``signal/<cluster>/<subcluster>``. The
    client is created inside the child so urllib state and API credentials are
    not shared between workers.
    """
    client = None
    if payload["needs_api"]:
        client = DeepSeekClient(
            timeout_sec=payload["api_timeout_sec"],
            max_tokens=payload["max_output_tokens"],
        )
    subcluster = payload["subcluster"]
    try:
        return run_subcluster(
            client,
            payload["cluster"],
            payload["dataset_dir"],
            payload["cluster_out_dir"],
            subcluster,
            payload["timeout_sec"],
            payload["test_limit"],
            payload["compare_mode"],
            payload["normalize"],
            payload["test_mode"],
            payload["resume"],
            payload["rerun_metrics"],
            payload["skip_metrics"],
            SIGNAL_PROMPT_SET,
            payload["force_extraction"],
        )
    except Exception as exc:
        return {
            "baseline": "signal",
            "cluster_id": payload["cluster"]["cluster_id"],
            "subcluster_id": subcluster["cluster_id"],
            "members": subcluster["members"],
            **status_payload("failed", repr(exc)),
        }


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
    rerun_gate: bool,
    rerun_metrics: bool,
    skip_metrics: bool,
    force_extraction: bool,
    discovery_only: bool,
    skip_semantic: bool,
    cfg: dict[str, Any],
    embedding_model: str,
    gate_cfg: dict[str, Any],
    min_actual_savings: int,
    workers: int,
) -> dict[str, Any]:
    cluster_id = cluster["cluster_id"]
    cluster_out_dir = results_dir / "signal" / cluster_id
    try:
        previous_cluster = load_json_if_exists(cluster_out_dir / "status.json")
        previous_discovery = load_json_if_exists(cluster_out_dir / "discovery.json")
        resume_cluster_complete = False
        if resume and previous_cluster and previous_discovery:
            cluster_files = {item["file_id"] for item in cluster["files"]}
            previous_subclusters = [
                sanitize_subcluster(item, cluster_files, index)
                for index, item in enumerate(previous_discovery.get("clusters", []))
            ]
            valid_previous = [item for item in previous_subclusters if len(item["members"]) >= 2]
            resume_cluster_complete = all(
                (load_json_if_exists(cluster_out_dir / item["cluster_id"] / "status.json") or {}).get("status") == "ok"
                and (cluster_out_dir / item["cluster_id"] / "common.py").exists()
                and (cluster_out_dir / item["cluster_id"] / "refactored").is_dir()
                for item in valid_previous
            )
        if (
            resume
            and previous_cluster
            and previous_cluster.get("status") == "ok"
            and previous_discovery
            and resume_cluster_complete
            and not rerun_gate
            and not force_extraction
            and (not rerun_metrics or skip_metrics)
        ):
            print(
                f"signal cluster {cluster_id}: resume hit, skipping cluster",
                file=sys.stderr,
                flush=True,
            )
            return previous_cluster
        if resume and previous_discovery and not rerun_gate:
            print(f"signal cluster {cluster_id}: resume hit, reusing discovery", file=sys.stderr, flush=True)
            discovery = previous_discovery
        else:
            discovery_status = discover_cluster(
                cluster,
                dataset_dir,
                results_dir,
                cfg,
                skip_semantic=skip_semantic,
                skip_gate=False,
                embedding_model=embedding_model,
                force_semantic=False,
                resume=resume,
                gate_cfg=gate_cfg,
                rerun_gate=rerun_gate,
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
        total_subclusters = len(valid)
        job_common = {
            "cluster": cluster,
            "dataset_dir": dataset_dir,
            "cluster_out_dir": cluster_out_dir,
            "timeout_sec": timeout_sec,
            "test_limit": test_limit,
            "compare_mode": compare_mode,
            "normalize": normalize,
            "test_mode": test_mode,
            "resume": resume,
            "rerun_metrics": rerun_metrics,
            "skip_metrics": skip_metrics,
            "force_extraction": force_extraction,
            "needs_api": client is not None,
            "api_timeout_sec": gate_cfg.get("api_timeout_sec"),
            "max_output_tokens": gate_cfg.get("max_output_tokens"),
        }
        results_by_id: dict[str, dict[str, Any]] = {}
        worker_count = max(1, min(int(workers), total_subclusters or 1))
        jobs = []
        for sub_index, subcluster in enumerate(valid, 1):
            job = dict(job_common)
            job["subcluster"] = subcluster
            job["sub_index"] = sub_index
            jobs.append(job)
        if worker_count == 1:
            for sub_index, job in enumerate(jobs, 1):
                progress(f"signal cluster {cluster_id} subclusters", sub_index - 1, total_subclusters)
                sub_result = _run_subcluster_job(job)
                results_by_id[sub_result["subcluster_id"]] = sub_result
                write_json(cluster_out_dir / sub_result["subcluster_id"] / "status.json", sub_result)
                progress(f"signal cluster {cluster_id} subclusters", sub_index, total_subclusters)
        else:
            print(
                f"signal cluster {cluster_id}: extracting {total_subclusters} subclusters with {worker_count} workers",
                file=sys.stderr,
                flush=True,
            )
            with concurrent.futures.ProcessPoolExecutor(max_workers=worker_count) as executor:
                futures = [executor.submit(_run_subcluster_job, job) for job in jobs]
                for completed, future in enumerate(concurrent.futures.as_completed(futures), 1):
                    sub_result = future.result()
                    results_by_id[sub_result["subcluster_id"]] = sub_result
                    write_json(cluster_out_dir / sub_result["subcluster_id"] / "status.json", sub_result)
                    progress(f"signal cluster {cluster_id} subclusters", completed - 1, total_subclusters)
                    progress(f"signal cluster {cluster_id} subclusters", completed, total_subclusters)
        sub_results = [results_by_id[item["cluster_id"]] for item in valid]
        result_by_sub_id = {item.get("subcluster_id"): item for item in sub_results}
        extraction_dropped: list[dict[str, Any]] = []
        final_valid = []
        for subcluster in valid:
            sub_id = subcluster["cluster_id"]
            sub_result = result_by_sub_id.get(sub_id) or {}
            if sub_result.get("status") != "ok":
                extraction_dropped.append({
                    "cluster_id": sub_id,
                    "reason": "extraction_failed",
                    "detail": sub_result.get("reason", "subcluster did not finish successfully"),
                })
                continue
            # Actual token savings remain a reported metric, not a second
            # candidate filter.  The user's requested decision point is the
            # LLM gate; negative savings should remain visible in the report.
            final_valid.append(subcluster)

        # If independently generated subclusters target the same source span,
        # retain the earlier one and exclude the later one from the final set.
        while True:
            scope_audit = audit_signal_edit_scopes(dataset_dir, cluster, cluster_out_dir, final_valid)
            if scope_audit["ok"] or not scope_audit.get("conflicts"):
                break
            drop_ids = {item["right"]["subcluster_id"] for item in scope_audit["conflicts"]}
            if not drop_ids:
                break
            final_valid = [item for item in final_valid if item["cluster_id"] not in drop_ids]
            extraction_dropped.extend(
                {"cluster_id": sub_id, "reason": "scope_overlap_with_earlier_subcluster"}
                for sub_id in sorted(drop_ids)
            )
        extraction_result_screen = {
            "before_count": len(valid),
            "after_count": len(final_valid),
            "economic_filter_enabled": False,
            "dropped": extraction_dropped,
            "note": "Actual token savings are reported per subcluster; they do not filter LLM-accepted candidates.",
        }
        write_json(cluster_out_dir / "extraction_result_screen.json", extraction_result_screen)
        write_json(cluster_out_dir / "scope_audit.json", scope_audit)
        covered = set().union(*(set(item["members"]) for item in final_valid)) if final_valid else set()
        if not scope_audit["ok"]:
            cluster_status = "scope_conflict"
        elif extraction_dropped:
            cluster_status = "partial_failed" if final_valid else "failed"
        else:
            cluster_status = "ok"
        result = {
            "baseline": "signal",
            "cluster_id": cluster_id,
            "status": cluster_status,
            "discovered_subclusters": len(subclusters),
            "valid_subclusters": len(final_valid),
            "extraction_candidates": len(valid),
            "noise_files": sorted((set(discovery.get("noise") or []) | (cluster_files - covered)) & cluster_files),
            "subclusters": sub_results,
            "scope_audit": scope_audit,
            "extraction_result_screen": extraction_result_screen,
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
    parser.add_argument(
        "--screen-min-shared-tokens",
        type=int,
        default=18,
        help="Minimum concrete shared tokens required by the extraction-worthiness screen.",
    )
    parser.add_argument(
        "--screen-min-estimated-savings",
        type=int,
        default=8,
        help="Minimum estimated token savings required after wrapper overhead.",
    )
    parser.add_argument(
        "--screen-wrapper-tokens",
        type=int,
        default=8,
        help="Per-member import/call-site token budget used by the screen.",
    )
    parser.add_argument(
        "--screen-min-actual-savings",
        type=int,
        default=0,
        help="Deprecated compatibility option; actual token savings are reported and do not filter LLM-accepted candidates.",
    )
    parser.add_argument("--embedding-model", type=str, default="")
    parser.add_argument(
        "--semantic-top-frac",
        type=float,
        default=None,
        help=(
            "Override the fraction of highest-cosine semantic edges accepted in addition "
            "to --min-cosine. For example, 0.005 keeps the top 0.5%%."
        ),
    )
    parser.add_argument("--test-mode", choices=["stdio", "pytest"], default="stdio")
    parser.add_argument("--test-limit", type=int, default=0, help="Per-file test limit. Use 0 for all tests.")
    parser.add_argument("--compare-mode", choices=["expected", "original"], default="original")
    parser.add_argument("--normalize", choices=["strip", "whitespace"], default="whitespace")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--rerun-gate", action="store_true", help="Reuse mechanical candidates but rerun the LLM gate.")
    parser.add_argument(
        "--force-extraction",
        action="store_true",
        help="With --resume, reuse discovery but regenerate common.py and member edits for every accepted subcluster.",
    )
    parser.add_argument("--rerun-metrics", action="store_true")
    parser.add_argument("--skip-metrics", action="store_true")
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Number of parallel extraction worker processes per cluster.",
    )
    parser.add_argument(
        "--gate-workers",
        type=int,
        default=1,
        help="Number of parallel LLM gate calls per cluster.",
    )
    parser.add_argument("--discovery-only", action="store_true", help="Stop after discovery.json; skip extraction.")
    parser.add_argument("--skip-semantic", action="store_true", help="Use clone + AST discovery without the embedding worker.")
    parser.add_argument(
        "--unit-preference",
        choices=["leaf", "coarse"],
        default="coarse",
        help="Candidate scope policy: leaf keeps methods/functions; coarse prefers class envelopes over methods.",
    )
    args = parser.parse_args()

    manifest = load_manifest(args.manifest)
    clusters = selected_clusters(manifest, args.cluster_id, args.limit)
    test_limit = None if args.test_limit == 0 else args.test_limit
    cfg = copy.deepcopy(fusion.DEFAULT_CFG)
    cfg["unit_preference"] = args.unit_preference
    if args.semantic_top_frac is not None:
        if not 0.0 <= args.semantic_top_frac <= 1.0:
            parser.error("--semantic-top-frac must be between 0 and 1")
        cfg["semantic"]["or_top_frac"] = args.semantic_top_frac
    embedding_model = args.embedding_model or DEFAULT_EMBEDDING_MODEL
    gate_cfg = {
        "api_timeout_sec": args.api_timeout_sec,
        "max_output_tokens": args.max_output_tokens,
        "min_shared_tokens": args.screen_min_shared_tokens,
        "min_estimated_savings": args.screen_min_estimated_savings,
        "wrapper_tokens_per_file": args.screen_wrapper_tokens,
        "gate_workers": max(1, args.gate_workers),
    }

    def cluster_needs_api(cluster: dict[str, Any]) -> bool:
        if args.rerun_gate or args.force_extraction:
            return True
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
            args.rerun_gate,
            args.rerun_metrics,
            args.skip_metrics,
            args.force_extraction,
            args.discovery_only,
            args.skip_semantic,
            cfg,
            embedding_model,
            gate_cfg,
            args.screen_min_actual_savings,
            args.workers,
        ))
        progress("signal clusters", index, total)
    write_json(args.results_dir / "signal" / "summary.json", results)


if __name__ == "__main__":
    main()
