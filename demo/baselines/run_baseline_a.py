from __future__ import annotations

import argparse
import concurrent.futures
import sys
from pathlib import Path

from demo.baselines.llm_client import DeepSeekClient, LLMConfigurationError
from demo.baselines.runner_utils import (
    PROMPT_DIR,
    build_user_prompt,
    behavior_tests_ok,
    call_and_parse_extraction,
    compile_result,
    load_json_if_exists,
    load_manifest,
    materialize_edit_intents,
    prompt_files_for_cluster,
    read_text,
    run_metrics,
    selected_clusters,
    status,
    status_payload,
    validate_common_usage,
    write_extraction_result,
    write_json,
)


ROOT = Path(__file__).resolve().parents[2]


def progress(label: str, current: int, total: int) -> None:
    if total <= 0:
        return
    width = 24
    filled = int(width * current / total)
    bar = "#" * filled + "-" * (width - filled)
    print(f"\r{label} [{bar}] {current}/{total}", end="" if current < total else "\n", file=sys.stderr, flush=True)


def run_cluster(
    client: DeepSeekClient | None,
    dataset_dir: Path,
    results_dir: Path,
    cluster: dict,
    timeout_sec: float,
    test_limit: int | None,
    compare_mode: str,
    normalize: str,
    test_mode: str,
    resume: bool,
    rerun_metrics: bool,
    skip_metrics: bool,
) -> dict:
    cluster_id = cluster["cluster_id"]
    out_dir = results_dir / "baseline_a" / cluster_id
    previous = load_json_if_exists(out_dir / "status.json")
    if resume and previous and previous.get("status") == "ok":
        if not rerun_metrics or skip_metrics:
            print(f"baseline_a cluster {cluster_id}: resume hit, skipping extraction and metrics", file=sys.stderr, flush=True)
            return previous
        print(f"baseline_a cluster {cluster_id}: resume hit, rerunning metrics only", file=sys.stderr, flush=True)
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
            )
            if compile_info["ok"]
            else None
        ) if not skip_metrics else None
        result = {
            "baseline": "baseline_a",
            "cluster_id": cluster_id,
            "status": "ok" if compile_info["ok"] else "compile_failed",
            "resume": True,
            "rerun_metrics": True,
            "compile": compile_info,
            "metrics": metrics,
        }
        write_json(out_dir / "status.json", result)
        return result

    if client is None:
        raise LLMConfigurationError("DEEPSEEK_API_KEY is required for clusters without reusable ok artifacts.")

    payload = {"cluster_id": cluster_id, "files": prompt_files_for_cluster(dataset_dir, cluster)}
    system_path = PROMPT_DIR / "baseline_a_system.txt"
    user_path = PROMPT_DIR / "baseline_a_user_template.txt"
    system_prompt = read_text(system_path)
    user_prompt = build_user_prompt(user_path, payload)
    prompt_paths = {"system": str(system_path), "user_template": str(user_path)}
    original_files = {item["file_id"]: item["source_code"] for item in payload["files"]}

    try:
        extraction = call_and_parse_extraction(client, system_prompt, user_prompt, out_dir, "baseline_a", cluster_id, prompt_paths)
        try:
            extraction = materialize_edit_intents(extraction, original_files)
            common_usage = validate_common_usage(extraction, original_files)
        except ValueError as exc:
            status(f"baseline_a cluster {cluster_id}: host edit/AST validation failed; retrying: {exc}")
            repair = (
                "Previous response contained edit intents that the host could not apply or validate. "
                "Return corrected local original/replacement fragments; do not return line numbers, diffs, "
                "or complete member files.\n" + str(exc)
            )
            extraction = call_and_parse_extraction(
                client,
                system_prompt,
                user_prompt,
                out_dir,
                "baseline_a",
                cluster_id,
                prompt_paths,
                repair_context=repair,
                raw_prefix="repair_",
            )
            extraction = materialize_edit_intents(extraction, original_files)
            common_usage = validate_common_usage(extraction, original_files)
        write_extraction_result(out_dir, extraction)
        compile_info = compile_result(out_dir)
        if not compile_info["ok"]:
            status(f"baseline_a cluster {cluster_id}: host compilation failed; retrying: {compile_info}")
            repair = (
                "The host-generated member files failed parsing or compilation. Return corrected local edit intents; "
                "do not return line numbers, diffs, or complete member files.\n" + str(compile_info)
            )
            extraction = call_and_parse_extraction(
                client,
                system_prompt,
                user_prompt,
                out_dir,
                "baseline_a",
                cluster_id,
                prompt_paths,
                repair_context=repair,
                raw_prefix="compile_retry_",
            )
            extraction = materialize_edit_intents(extraction, original_files)
            common_usage = validate_common_usage(extraction, original_files)
            write_extraction_result(out_dir, extraction)
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
            )
            if compile_info["ok"] and not skip_metrics
            else None
        )
        tests_ok = behavior_tests_ok(metrics)
        final_status = (
            "compile_failed" if not compile_info["ok"]
            else "tests_failed" if not tests_ok
            else "ok"
        )
        result = {
            "baseline": "baseline_a",
            "cluster_id": cluster_id,
            "status": final_status,
            "compile": compile_info,
            "common_usage": common_usage,
            "tests_verified": metrics is not None,
            "metrics": metrics,
        }
    except Exception as exc:
        result = {"baseline": "baseline_a", "cluster_id": cluster_id, **status_payload("failed", repr(exc))}
    write_json(out_dir / "status.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Run baseline-a end-to-end extraction.")
    parser.add_argument("--manifest", type=Path, default=ROOT / "demo" / "datasets" / "codecontest" / "cluster_manifest.json")
    parser.add_argument("--dataset-dir", type=Path, default=ROOT / "demo" / "datasets" / "codecontest")
    parser.add_argument("--results-dir", type=Path, default=ROOT / "demo" / "results" / "codecontest")
    parser.add_argument("--cluster-id", action="append")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--timeout-sec", type=float, default=5.0)
    parser.add_argument("--api-timeout-sec", type=float, default=None, help="Optional API request timeout")
    parser.add_argument("--max-output-tokens", type=int, default=None, help="Optional client-side output token limit")
    parser.add_argument("--test-limit", type=int, default=0, help="Per-file test limit. Use 0 for all tests.")
    parser.add_argument("--compare-mode", choices=["expected", "original"], default="original")
    parser.add_argument("--normalize", choices=["strip", "whitespace"], default="whitespace")
    parser.add_argument("--test-mode", choices=["stdio", "pytest"], default="stdio", help="Use pytest for package-backed real-code datasets.")
    parser.add_argument("--resume", action="store_true", help="Skip clusters whose status.json is already ok.")
    parser.add_argument("--rerun-metrics", action="store_true", help="With --resume, recompute metrics for skipped ok clusters.")
    parser.add_argument("--skip-metrics", action="store_true", help="Only extract/refactor and compile; do not run tests or metrics.")
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Number of clusters to extract concurrently. Each cluster writes to an independent directory.",
    )
    args = parser.parse_args()

    manifest = load_manifest(args.manifest)
    clusters = selected_clusters(manifest, args.cluster_id, args.limit)
    test_limit = None if args.test_limit == 0 else args.test_limit
    needs_api = not args.resume or any(
        (load_json_if_exists(args.results_dir / "baseline_a" / cluster["cluster_id"] / "status.json") or {}).get("status") != "ok"
        for cluster in clusters
    )
    try:
        client = DeepSeekClient(timeout_sec=args.api_timeout_sec, max_tokens=args.max_output_tokens) if needs_api else None
    except LLMConfigurationError as exc:
        for cluster in clusters:
            out_dir = args.results_dir / "baseline_a" / cluster["cluster_id"]
            write_json(out_dir / "status.json", {"baseline": "baseline_a", "cluster_id": cluster["cluster_id"], **status_payload("not_run", str(exc))})
        raise SystemExit(str(exc))

    total = len(clusters)
    worker_count = max(1, min(args.workers, total or 1))

    def run_one(cluster: dict) -> dict:
        return run_cluster(
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
        )

    results_by_id: dict[str, dict] = {}
    if worker_count == 1:
        for index, cluster in enumerate(clusters, 1):
            progress("baseline_a clusters", index - 1, total)
            result = run_one(cluster)
            results_by_id[cluster["cluster_id"]] = result
            progress("baseline_a clusters", index, total)
    else:
        print(
            f"baseline_a: extracting {total} clusters with {worker_count} workers",
            file=sys.stderr,
            flush=True,
        )
        with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = {executor.submit(run_one, cluster): cluster for cluster in clusters}
            for completed, future in enumerate(concurrent.futures.as_completed(futures), 1):
                cluster = futures[future]
                try:
                    result = future.result()
                except Exception as exc:
                    result = {
                        "baseline": "baseline_a",
                        "cluster_id": cluster["cluster_id"],
                        **status_payload("failed", repr(exc)),
                    }
                    write_json(
                        args.results_dir / "baseline_a" / cluster["cluster_id"] / "status.json",
                        result,
                    )
                results_by_id[cluster["cluster_id"]] = result
                progress("baseline_a clusters", completed, total)
    results = [results_by_id[cluster["cluster_id"]] for cluster in clusters]
    write_json(args.results_dir / "baseline_a" / "summary.json", results)


if __name__ == "__main__":
    main()
