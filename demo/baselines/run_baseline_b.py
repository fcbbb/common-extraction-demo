from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable

from demo.baselines.json_utils import (
    extract_json_object,
    require_discovery_schema,
    require_library_schema,
    require_refactor_batch_schema,
)
from demo.baselines.llm_client import DeepSeekClient, LLMConfigurationError
from demo.baselines.runner_utils import (
    PROMPT_DIR,
    behavior_tests_ok,
    build_user_prompt,
    compile_result,
    load_json_if_exists,
    load_manifest,
    materialize_edit_intents,
    prompt_files_for_cluster,
    read_text,
    run_metrics,
    save_call_log,
    selected_clusters,
    status,
    status_payload,
    validate_common_usage,
    write_extraction_result,
    write_json,
    validate_unit_scoped_edits,
)


ROOT = Path(__file__).resolve().parents[2]


def progress(label: str, current: int, total: int) -> None:
    if total <= 0:
        return
    width = 24
    filled = int(width * current / total)
    bar = "#" * filled + "-" * (width - filled)
    print(f"\r{label} [{bar}] {current}/{total}", end="" if current < total else "\n", file=sys.stderr, flush=True)


def call_discovery(client: DeepSeekClient, cluster: dict[str, Any], dataset_dir: Path, out_dir: Path) -> dict[str, Any]:
    cluster_id = cluster["cluster_id"]
    system_path = PROMPT_DIR / "baseline_b_discover_system.txt"
    user_path = PROMPT_DIR / "baseline_b_discover_user_template.txt"
    system_prompt = read_text(system_path)
    payload = {"cluster_id": cluster_id, "files": prompt_files_for_cluster(dataset_dir, cluster)}
    user_prompt = build_user_prompt(user_path, payload)
    progress(f"baseline_b cluster {cluster_id} discovery", 0, 1)
    response = client.chat(system_prompt, user_prompt)
    progress(f"baseline_b cluster {cluster_id} discovery", 1, 1)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "discovery_raw_response.txt").write_text(response.content, encoding="utf-8")
    write_json(out_dir / "discovery_raw_api_response.json", response.raw)
    write_json(
        out_dir / "discovery_call_log.json",
        {
            "provider": "deepseek",
            "model": response.model,
            "temperature": 0,
            "cluster_id": cluster_id,
            "baseline": "baseline_b_discover",
            "prompt_path": {"system": str(system_path), "user_template": str(user_path)},
            "latency_sec": response.latency_sec,
            "usage": response.usage,
        },
    )
    discovery = extract_json_object(response.content)
    require_discovery_schema(discovery)
    write_json(out_dir / "discovery.json", discovery)
    return discovery


def sanitize_subcluster(item: dict[str, Any], cluster_files: set[str], fallback_index: int) -> dict[str, Any]:
    sub_id = item.get("cluster_id") or f"sub_{fallback_index}"
    members = [member for member in item.get("members", []) if isinstance(member, str) and member in cluster_files]
    sanitized = {
        "cluster_id": str(sub_id),
        "members": sorted(dict.fromkeys(members)),
        "shared_concept": item.get("shared_concept", ""),
        "shared_interface": item.get("shared_interface", []),
        "key_variations": item.get("key_variations", []),
    }
    if "units" in item and isinstance(item["units"], dict):
        sanitized["units"] = {
            file_id: sorted({unit_id for unit_id in unit_ids if isinstance(unit_id, str)})
            for file_id, unit_ids in item["units"].items()
            if file_id in sanitized["members"] and isinstance(unit_ids, list)
        }
    if "screening" in item and isinstance(item["screening"], dict):
        sanitized["screening"] = item["screening"]
    return sanitized


def _prompt_path(prompt_set: dict[str, Any] | None, key: str, default: Path) -> Path:
    value = (prompt_set or {}).get(key, default)
    return Path(value)


def call_and_parse_stage(
    client: DeepSeekClient,
    system_prompt: str,
    user_prompt: str,
    out_dir: Path,
    baseline: str,
    cluster_id: str,
    prompt_paths: dict[str, str],
    raw_stem: str,
    schema_hint: str,
    validate: Callable[[dict[str, Any]], None],
    repair_context: str | None = None,
) -> dict[str, Any]:
    prompt = user_prompt if repair_context is None else user_prompt + "\n\nRepair context:\n" + repair_context
    prompt_chars = len(system_prompt) + len(prompt)
    approx_tokens = prompt_chars // 4
    status(
        f"{baseline} cluster {cluster_id}: prompt size {prompt_chars} chars "
        f"(roughly {approx_tokens} tokens)"
    )
    errors = []
    for attempt in range(2):
        final_user_prompt = prompt
        if attempt == 1:
            final_user_prompt = (
                prompt
                + "\n\nYour previous response could not be parsed or did not match the required schema. "
                + f"Return one complete valid JSON object only, matching this schema: {schema_hint}."
                + f"\nParser error: {errors[-1]}"
            )
        status(f"{baseline} cluster {cluster_id}: calling DeepSeek API attempt {attempt + 1}/2")
        response = client.chat(system_prompt, final_user_prompt)
        status(f"{baseline} cluster {cluster_id}: API response received")
        out_dir.mkdir(parents=True, exist_ok=True)
        suffix = "" if attempt == 0 else f".retry{attempt}"
        (out_dir / f"{raw_stem}_raw_response{suffix}.txt").write_text(response.content, encoding="utf-8")
        write_json(out_dir / f"{raw_stem}_raw_api_response{suffix}.json", response.raw)
        save_call_log(out_dir / f"{raw_stem}_call_log_meta{suffix}.json", response, baseline, cluster_id, prompt_paths)
        try:
            payload = extract_json_object(response.content)
            validate(payload)
            return payload
        except Exception as exc:
            errors.append(repr(exc))
    raise ValueError(f"{baseline} JSON parse/schema failed after one retry: " + " | ".join(errors))


def call_common(
    client: DeepSeekClient,
    cluster: dict[str, Any],
    dataset_dir: Path,
    out_dir: Path,
    subcluster: dict[str, Any],
    raw_prefix: str = "",
    repair_context: str | None = None,
    prompt_set: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cluster_id = cluster["cluster_id"]
    system_path = _prompt_path(prompt_set, "common_system", PROMPT_DIR / "baseline_b_common_system.txt")
    user_path = _prompt_path(prompt_set, "common_user", PROMPT_DIR / "baseline_b_common_user_template.txt")
    payload = {
        "cluster_id": cluster_id,
        "subcluster": subcluster,
        "files": prompt_files_for_cluster(dataset_dir, cluster, subcluster["members"]),
    }
    system_prompt = read_text(system_path)
    user_prompt = build_user_prompt(user_path, payload)
    return call_and_parse_stage(
        client,
        system_prompt,
        user_prompt,
        out_dir,
        "baseline_b_common",
        cluster_id,
        {"system": str(system_path), "user_template": str(user_path)},
        f"{raw_prefix}common",
        '{"library":{"path":"common.py","content":"..."},"rationale":"..."}',
        require_library_schema,
        repair_context=repair_context,
    )


def call_member_refactors(
    client: DeepSeekClient,
    cluster: dict[str, Any],
    dataset_dir: Path,
    out_dir: Path,
    subcluster: dict[str, Any],
    library: dict[str, Any],
    raw_prefix: str = "",
    repair_context: str | None = None,
    prompt_set: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One API call refactoring every member file of the subcluster."""
    cluster_id = cluster["cluster_id"]
    system_path = _prompt_path(prompt_set, "refactor_system", PROMPT_DIR / "baseline_b_refactor_all_system.txt")
    user_path = _prompt_path(prompt_set, "refactor_user", PROMPT_DIR / "baseline_b_refactor_all_user_template.txt")
    payload = {
        "cluster_id": cluster_id,
        "subcluster": subcluster,
        "library": library,
        "files": prompt_files_for_cluster(dataset_dir, cluster, subcluster["members"]),
    }
    system_prompt = read_text(system_path)
    user_prompt = build_user_prompt(user_path, payload)
    return call_and_parse_stage(
        client,
        system_prompt,
        user_prompt,
        out_dir,
        "baseline_b_refactor",
        cluster_id,
        {"system": str(system_path), "user_template": str(user_path)},
        f"{raw_prefix}refactor_all",
        '{"refactors":[{"file_id":"file_000.py","edits":[],"rationale":"..."}]}',
        lambda item: require_refactor_batch_schema(item, set(subcluster["members"])),
        repair_context=repair_context,
    )


def generate_member_refactor_extraction(
    client: DeepSeekClient,
    cluster: dict[str, Any],
    dataset_dir: Path,
    out_dir: Path,
    subcluster: dict[str, Any],
    library: dict[str, Any],
    raw_prefix: str = "",
    repair_context: str | None = None,
    prompt_set: dict[str, Any] | None = None,
) -> dict[str, Any]:
    refactor_payload = call_member_refactors(
        client,
        cluster,
        dataset_dir,
        out_dir,
        subcluster,
        library,
        raw_prefix,
        repair_context,
        prompt_set,
    )
    members = {
        item["file_id"]: {
            "edits": item["edits"],
            "rationale": item.get("rationale", ""),
        }
        for item in refactor_payload["refactors"]
    }
    original_files = {
        item["file_id"]: item["source_code"]
        for item in prompt_files_for_cluster(dataset_dir, cluster, subcluster["members"])
    }
    extraction = materialize_edit_intents(
        {"library": library, "members": members, "rationale": ""},
        original_files,
    )
    if (prompt_set or {}).get("unit_scoped"):
        validate_unit_scoped_edits(extraction, original_files, subcluster)
    validate_common_usage(extraction, original_files)
    return extraction


def run_subcluster(
    client: DeepSeekClient | None,
    cluster: dict[str, Any],
    dataset_dir: Path,
    cluster_out_dir: Path,
    subcluster: dict[str, Any],
    timeout_sec: float,
    test_limit: int | None,
    compare_mode: str,
    normalize: str,
    test_mode: str,
    resume: bool,
    rerun_metrics: bool,
    skip_metrics: bool,
    prompt_set: dict[str, Any] | None = None,
    force_extraction: bool = False,
) -> dict[str, Any]:
    cluster_id = cluster["cluster_id"]
    sub_id = subcluster["cluster_id"]
    out_dir = cluster_out_dir / sub_id
    previous = load_json_if_exists(out_dir / "status.json")
    if resume and not force_extraction and previous and previous.get("status") == "ok":
        if not rerun_metrics or skip_metrics:
            print(
                f"baseline_b cluster {cluster_id}/{sub_id}: resume hit, skipping extraction and metrics",
                file=sys.stderr,
                flush=True,
            )
            return previous
        print(
            f"baseline_b cluster {cluster_id}/{sub_id}: resume hit, rerunning metrics only",
            file=sys.stderr,
            flush=True,
        )
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
            )
            if compile_info["ok"]
            else None
        ) if not skip_metrics else None
        return {
            "baseline": "baseline_b",
            "cluster_id": cluster_id,
            "subcluster_id": sub_id,
            "members": subcluster["members"],
            "status": "ok" if compile_info["ok"] else "compile_failed",
            "resume": True,
            "rerun_metrics": True,
            "compile": compile_info,
            "metrics": metrics,
        }

    if client is None:
        raise LLMConfigurationError("DEEPSEEK_API_KEY is required for subclusters without reusable ok artifacts.")

    # Generate common.py exactly once. All member-refactor retries below reuse this
    # library and call only the member-refactor stage.
    common_path = out_dir / "common.py"
    if resume and not force_extraction and common_path.exists():
        status(f"baseline_b cluster {cluster_id}/{sub_id}: reusing common.py")
        common_payload = {
            "library": {"path": "common.py", "content": common_path.read_text(encoding="utf-8")},
            "rationale": "reused fixed common.py from the previous member-refactor attempt",
        }
    else:
        status(f"baseline_b cluster {cluster_id}/{sub_id}: common extraction start")
        common_payload = call_common(client, cluster, dataset_dir, out_dir, subcluster, prompt_set=prompt_set)
        status(f"baseline_b cluster {cluster_id}/{sub_id}: common extraction done")
    library = common_payload["library"]
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "common.py").write_text(library["content"], encoding="utf-8")
    try:
        status(f"baseline_b cluster {cluster_id}/{sub_id}: member refactor start")
        extraction = generate_member_refactor_extraction(
            client, cluster, dataset_dir, out_dir, subcluster, library, prompt_set=prompt_set
        )
        status(f"baseline_b cluster {cluster_id}/{sub_id}: member refactor done")
    except ValueError as exc:
        status(f"baseline_b cluster {cluster_id}/{sub_id}: member refactor retry after validation error")
        repair = (
            "The fixed common.py has been generated. Your member edit intents could not be applied or did not pass "
            "host validation. Return exactly one member entry per file with ordered exact original/replacement "
            "fragments. Do not regenerate or modify common.py; do not return line numbers, diffs, or complete files. "
            "Validation error: "
            + repr(exc)
        )
        extraction = generate_member_refactor_extraction(
            client,
            cluster,
            dataset_dir,
            out_dir,
            subcluster,
            library,
            raw_prefix="refactor_retry_",
            repair_context=repair,
            prompt_set=prompt_set,
        )
        status(f"baseline_b cluster {cluster_id}/{sub_id}: member refactor retry done")
    extraction["rationale"] = common_payload.get("rationale", "")
    write_extraction_result(out_dir, extraction)
    status(f"baseline_b cluster {cluster_id}/{sub_id}: compile start")
    compile_info = compile_result(out_dir)
    status(
        f"baseline_b cluster {cluster_id}/{sub_id}: compile "
        f"{'passed' if compile_info['ok'] else 'failed'}"
    )
    if not compile_info["ok"]:
        status(f"baseline_b cluster {cluster_id}/{sub_id}: compile repair start")
        repair = (
            "The fixed common.py must remain unchanged. The host-generated member files failed parsing or compilation. "
            "Return corrected member edit intents only; do not regenerate common.py, return line numbers, diffs, or "
            "complete files. "
            + json.dumps(compile_info, ensure_ascii=False)
        )
        extraction = generate_member_refactor_extraction(
            client,
            cluster,
            dataset_dir,
            out_dir,
            subcluster,
            library,
            raw_prefix="compile_retry_",
            repair_context=repair,
            prompt_set=prompt_set,
        )
        extraction["rationale"] = common_payload.get("rationale", "")
        write_extraction_result(out_dir, extraction)
        compile_info = compile_result(out_dir)
        status(
            f"baseline_b cluster {cluster_id}/{sub_id}: compile repair "
            f"{'passed' if compile_info['ok'] else 'failed'}"
        )
    if compile_info["ok"] and not skip_metrics:
        status(f"baseline_b cluster {cluster_id}/{sub_id}: pytest/metrics start")
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
        )
        if compile_info["ok"] and not skip_metrics
        else None
    )
    if metrics is not None:
        status(f"baseline_b cluster {cluster_id}/{sub_id}: pytest/metrics done")
    if metrics is not None and not behavior_tests_ok(metrics):
        status(f"baseline_b cluster {cluster_id}/{sub_id}: test repair start")
        repair = (
            "The fixed common.py must remain unchanged. Host behavior tests did not all pass for the generated member "
            "files. Return corrected member edit intents only; do not regenerate common.py, return line numbers, diffs, "
            "or complete files. Test result: "
            + json.dumps(metrics["tests"], ensure_ascii=False)
        )
        extraction = generate_member_refactor_extraction(
            client,
            cluster,
            dataset_dir,
            out_dir,
            subcluster,
            library,
            raw_prefix="test_retry_",
            repair_context=repair,
            prompt_set=prompt_set,
        )
        extraction["rationale"] = common_payload.get("rationale", "")
        write_extraction_result(out_dir, extraction)
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
            )
            if compile_info["ok"]
            else None
        )
        status(
            f"baseline_b cluster {cluster_id}/{sub_id}: test repair "
            f"{'passed' if metrics is not None and behavior_tests_ok(metrics) else 'still failing'}"
        )
    tests_ok = behavior_tests_ok(metrics)
    return {
        "baseline": "baseline_b",
        "cluster_id": cluster_id,
        "subcluster_id": sub_id,
        "members": subcluster["members"],
        "status": "compile_failed" if not compile_info["ok"] else "tests_failed" if not tests_ok else "ok",
        "compile": compile_info,
        "common_usage": extraction.get("common_usage"),
        "tests_verified": metrics is not None,
        "metrics": metrics,
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
    rerun_metrics: bool,
    skip_metrics: bool,
) -> dict[str, Any]:
    cluster_id = cluster["cluster_id"]
    cluster_out_dir = results_dir / "baseline_b" / cluster_id
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
                f"baseline_b cluster {cluster_id}: resume hit, skipping cluster",
                file=sys.stderr,
                flush=True,
            )
            return previous_cluster
        # Discovery is an independent completed artifact.  Reuse it even when
        # the previous run stopped partway through subclusters and therefore
        # never wrote an overall status=ok for the cluster.
        if resume and previous_discovery:
            print(f"baseline_b cluster {cluster_id}: resume hit, reusing discovery", file=sys.stderr, flush=True)
            discovery = previous_discovery
        else:
            if client is None:
                raise LLMConfigurationError("DEEPSEEK_API_KEY is required for clusters without reusable discovery artifacts.")
            discovery = call_discovery(client, cluster, dataset_dir, cluster_out_dir)
        cluster_files = {item["file_id"] for item in cluster["files"]}
        subclusters = [
            sanitize_subcluster(item, cluster_files, index)
            for index, item in enumerate(discovery.get("clusters", []))
        ]
        valid = [item for item in subclusters if len(item["members"]) >= 2]
        sub_results = []
        total_subclusters = len(valid)
        for sub_index, subcluster in enumerate(valid, 1):
            progress(f"baseline_b cluster {cluster_id} subclusters", sub_index - 1, total_subclusters)
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
                    "baseline": "baseline_b",
                    "cluster_id": cluster_id,
                    "subcluster_id": subcluster["cluster_id"],
                    "members": subcluster["members"],
                    **status_payload("failed", repr(exc)),
                }
            write_json(cluster_out_dir / subcluster["cluster_id"] / "status.json", sub_result)
            sub_results.append(sub_result)
            progress(f"baseline_b cluster {cluster_id} subclusters", sub_index, total_subclusters)
        covered = set().union(*(set(item["members"]) for item in valid)) if valid else set()
        cluster_status = "ok" if all(item.get("status") == "ok" for item in sub_results) else "failed"
        result = {
            "baseline": "baseline_b",
            "cluster_id": cluster_id,
            "status": cluster_status,
            "discovered_subclusters": len(subclusters),
            "valid_subclusters": len(valid),
            "noise_files": sorted((set(discovery.get("noise") or []) | (cluster_files - covered)) & cluster_files),
            "subclusters": sub_results,
        }
    except Exception as exc:
        result = {"baseline": "baseline_b", "cluster_id": cluster_id, **status_payload("failed", repr(exc))}
    write_json(cluster_out_dir / "status.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Run baseline-b discover then extract.")
    parser.add_argument("--manifest", type=Path, default=ROOT / "demo" / "datasets" / "codecontest" / "cluster_manifest.json")
    parser.add_argument("--dataset-dir", type=Path, default=ROOT / "demo" / "datasets" / "codecontest")
    parser.add_argument("--results-dir", type=Path, default=ROOT / "demo" / "results" / "codecontest")
    parser.add_argument("--cluster-id", action="append")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--timeout-sec", type=float, default=5.0)
    parser.add_argument("--api-timeout-sec", type=float, default=None, help="Optional API request timeout")
    parser.add_argument("--max-output-tokens", type=int, default=None, help="Optional client-side output token limit")
    parser.add_argument("--test-mode", choices=["stdio", "pytest"], default="stdio", help="Use pytest for package-backed real-code datasets.")
    parser.add_argument("--test-limit", type=int, default=0, help="Per-file test limit. Use 0 for all tests.")
    parser.add_argument("--compare-mode", choices=["expected", "original"], default="original")
    parser.add_argument("--normalize", choices=["strip", "whitespace"], default="whitespace")
    parser.add_argument("--resume", action="store_true", help="Reuse ok discovery/subcluster artifacts instead of calling DeepSeek again.")
    parser.add_argument("--rerun-metrics", action="store_true", help="With --resume, recompute metrics for skipped ok subclusters.")
    parser.add_argument("--skip-metrics", action="store_true", help="Only extract/refactor and compile; do not run tests or metrics.")
    args = parser.parse_args()

    manifest = load_manifest(args.manifest)
    clusters = selected_clusters(manifest, args.cluster_id, args.limit)
    test_limit = None if args.test_limit == 0 else args.test_limit
    def cluster_needs_api(cluster: dict[str, Any]) -> bool:
        if not args.resume:
            return True
        cluster_id = cluster["cluster_id"]
        cluster_out_dir = args.results_dir / "baseline_b" / cluster_id
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
            out_dir = args.results_dir / "baseline_b" / cluster["cluster_id"]
            write_json(out_dir / "status.json", {"baseline": "baseline_b", "cluster_id": cluster["cluster_id"], **status_payload("not_run", str(exc))})
        raise SystemExit(str(exc))

    results = []
    total = len(clusters)
    for index, cluster in enumerate(clusters, 1):
        progress("baseline_b clusters", index - 1, total)
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
        ))
        progress("baseline_b clusters", index, total)
    write_json(args.results_dir / "baseline_b" / "summary.json", results)


if __name__ == "__main__":
    main()
