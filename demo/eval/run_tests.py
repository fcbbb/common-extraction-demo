from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = ROOT / "demo" / "dataset" / "cluster_manifest.json"
TEST_GROUPS = ("public", "private", "generated")


def progress(label: str, current: int, total: int) -> None:
    if total <= 0:
        return
    width = 24
    filled = int(width * current / total)
    bar = "#" * filled + "-" * (width - filled)
    print(f"\r{label} [{bar}] {current}/{total}", end="" if current < total else "\n", file=sys.stderr, flush=True)


def normalize_output(text: str, mode: str) -> str:
    if mode == "strip":
        return text.strip()
    if mode == "whitespace":
        return " ".join(text.strip().split())
    raise ValueError(f"Unknown normalization mode: {mode}")


def original_test_passed(test: dict[str, Any], run: dict[str, Any], normalize: str) -> bool:
    """Return whether an original stdio solution passed one manifest test."""
    return (
        run["returncode"] == 0
        and not run["timed_out"]
        and normalize_output(run["stdout"], normalize) == normalize_output(test["expected"], normalize)
    )


def run_python_file(path: Path, stdin: str, timeout_sec: float) -> dict[str, Any]:
    # ``cwd`` is intentionally the script directory so local imports keep
    # working.  Resolve first: a dataset supplied as a relative CLI path would
    # otherwise be interpreted relative to that new cwd a second time.
    path = path.resolve()
    proc = subprocess.run(
        [sys.executable, str(path)],
        input=stdin,
        text=True,
        capture_output=True,
        timeout=timeout_sec,
        cwd=str(path.parent),
        check=False,
    )
    return {
        "returncode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "timed_out": False,
    }


def safe_run(path: Path, stdin: str, timeout_sec: float) -> dict[str, Any]:
    try:
        return run_python_file(path, stdin, timeout_sec)
    except subprocess.TimeoutExpired as exc:
        return {
            "returncode": None,
            "stdout": exc.stdout or "",
            "stderr": exc.stderr or "",
            "timed_out": True,
        }


def resolve_workers(workers: int | None) -> int:
    if workers is None:
        workers = int(os.getenv("TEST_WORKERS", "0"))
    if workers <= 0:
        workers = min(os.cpu_count() or 1, 16)
    return workers


def run_tasks_parallel(
    tasks: list[tuple[str, str, Path, dict[str, Any]]],
    workers: int,
    timeout_sec: float,
    label: str,
) -> list[tuple[str, str, dict[str, Any], dict[str, Any]]]:
    """tasks: (kind, file_id, path, test). Returns (kind, file_id, test, run) in task order."""
    total = len(tasks)
    results: list[tuple[str, str, dict[str, Any], dict[str, Any]] | None] = [None] * total
    done = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(safe_run, path, test["stdin"], timeout_sec): index
            for index, (_, _, path, test) in enumerate(tasks)
        }
        for future in concurrent.futures.as_completed(futures):
            index = futures[future]
            kind, file_id, _, test = tasks[index]
            results[index] = (kind, file_id, test, future.result())
            done += 1
            progress(label, done, total)
    return [item for item in results if item is not None]


def iter_tests(file_entry: dict[str, Any], limit: int | None = None):
    seen = 0
    for group in TEST_GROUPS:
        tests = file_entry.get("tests", {}).get(group) or {}
        inputs = tests.get("input") or []
        outputs = tests.get("output") or []
        for test_index, (stdin, expected) in enumerate(zip(inputs, outputs)):
            if limit is not None and seen >= limit:
                return
            seen += 1
            yield {
                "group": group,
                "test_index": test_index,
                "stdin": stdin,
                "expected": expected,
            }


def load_manifest(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def find_cluster(manifest: dict[str, Any], cluster_id: str) -> dict[str, Any]:
    for cluster in manifest["clusters"]:
        if cluster["cluster_id"] == cluster_id:
            return cluster
    raise KeyError(f"Unknown cluster_id: {cluster_id}")


def test_original_cluster(
    cluster: dict[str, Any],
    dataset_dir: Path,
    timeout_sec: float,
    test_limit: int | None = None,
    normalize: str = "strip",
    workers: int | None = None,
) -> dict[str, Any]:
    cluster_id = cluster["cluster_id"]
    original_dir = dataset_dir / "clusters" / cluster_id / "original"
    workers = resolve_workers(workers)

    tasks = []
    for file_entry in cluster["files"]:
        file_id = file_entry["file_id"]
        path = original_dir / file_id
        for test in iter_tests(file_entry, test_limit):
            tasks.append(("original", file_id, path, test))
    runs = run_tasks_parallel(tasks, workers, timeout_sec, f"original cluster {cluster_id}")

    by_file: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = {}
    for _, file_id, test, run in runs:
        by_file.setdefault(file_id, []).append((test, run))

    file_results = []
    totals = {"tests": 0, "expected_passed": 0, "files": 0, "files_expected_passed": 0}
    for file_entry in cluster["files"]:
        file_id = file_entry["file_id"]
        tests = []
        file_expected_ok = True
        for test, result in by_file.get(file_id, []):
            expected_ok = original_test_passed(test, result, normalize)
            tests.append({**test, "run": result, "expected_ok": expected_ok})
            totals["tests"] += 1
            totals["expected_passed"] += int(expected_ok)
            file_expected_ok = file_expected_ok and expected_ok
        totals["files"] += 1
        totals["files_expected_passed"] += int(file_expected_ok)
        file_results.append({"file_id": file_id, "all_expected_ok": file_expected_ok, "tests": tests})

    return {
        "cluster_id": cluster_id,
        "scope": "original",
        "summary": {
            **totals,
            "test_pass_rate_vs_expected": totals["expected_passed"] / totals["tests"] if totals["tests"] else None,
            "file_pass_rate_vs_expected": totals["files_expected_passed"] / totals["files"] if totals["files"] else None,
        },
        "files": file_results,
    }


def copy_common_for_run(result_dir: Path, tmp_dir: Path) -> None:
    common_path = result_dir / "common.py"
    if common_path.exists():
        (tmp_dir / "common.py").write_text(common_path.read_text(encoding="utf-8"), encoding="utf-8")


def test_refactored_cluster(
    cluster: dict[str, Any],
    dataset_dir: Path,
    result_dir: Path,
    timeout_sec: float,
    test_limit: int | None = None,
    compare_mode: str = "expected",
    normalize: str = "strip",
    workers: int | None = None,
) -> dict[str, Any]:
    cluster_id = cluster["cluster_id"]
    original_dir = dataset_dir / "clusters" / cluster_id / "original"
    refactored_dir = result_dir / "refactored"
    workers = resolve_workers(workers)

    # Establish the before-rewrite baseline first.  Refactored code is evaluated
    # only on tests that the original solution actually passes; otherwise the
    # denominator would include known-bad tests and make the reported rate
    # measure the dataset defects rather than the rewrite.
    original_tasks = []
    refactored_paths: dict[str, Path] = {}
    for file_entry in cluster["files"]:
        file_id = file_entry["file_id"]
        path = original_dir / file_id
        refactored_path = refactored_dir / file_id
        if refactored_path.exists():
            refactored_paths[file_id] = refactored_path
        for test in iter_tests(file_entry, test_limit):
            original_tasks.append(("original", file_id, path, test))
    original_runs = run_tasks_parallel(original_tasks, workers, timeout_sec, f"original cluster {cluster_id}")

    original_by_file: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = {}
    for _, file_id, test, run in original_runs:
        original_by_file.setdefault(file_id, []).append((test, run))

    eligible_by_file: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = {}
    for file_id, file_runs in original_by_file.items():
        eligible_by_file[file_id] = [
            (test, run) for test, run in file_runs if original_test_passed(test, run, normalize)
        ]

    tasks = []
    tmp_dirs: list[tempfile.TemporaryDirectory] = []
    missing: list[str] = []
    try:
        for file_entry in cluster["files"]:
            file_id = file_entry["file_id"]
            refactored_path = refactored_paths.get(file_id)
            if refactored_path is None:
                missing.append(file_id)
                continue
            tmp = tempfile.TemporaryDirectory(prefix="baseline_eval_")
            tmp_dirs.append(tmp)
            tmp_dir = Path(tmp.name)
            copy_common_for_run(result_dir, tmp_dir)
            runnable = tmp_dir / file_id
            runnable.write_text(refactored_path.read_text(encoding="utf-8"))
            for test, _ in eligible_by_file.get(file_id, []):
                tasks.append(("refactored", file_id, runnable, test))
        runs = run_tasks_parallel(tasks, workers, timeout_sec, f"refactored cluster {cluster_id}")
    finally:
        for tmp in tmp_dirs:
            tmp.cleanup()

    refactored_by_file: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = {}
    for _, file_id, test, run in runs:
        refactored_by_file.setdefault(file_id, []).append((test, run))

    file_results = []
    totals = {
        "tests": 0,
        "matched_original": 0,
        "files": 0,
        "files_matched_original": 0,
        "candidate_tests": sum(len(list(iter_tests(file_entry, test_limit))) for file_entry in cluster["files"]),
        "baseline_passed_tests": sum(len(tests) for tests in eligible_by_file.values()),
        "candidate_files": sum(bool(list(iter_tests(file_entry, test_limit))) for file_entry in cluster["files"]),
        "baseline_passed_files": sum(bool(tests) for tests in eligible_by_file.values()),
    }
    for file_entry in cluster["files"]:
        file_id = file_entry["file_id"]
        eligible = eligible_by_file.get(file_id, [])
        if not eligible:
            file_results.append({"file_id": file_id, "status": "not_evaluated", "tests": []})
            continue
        totals["files"] += 1
        if file_id in missing:
            totals["files_matched_original"] += 0
            file_results.append(
                {
                    "file_id": file_id,
                    "status": "missing_refactored",
                    "tests": [
                        {
                            "group": test["group"],
                            "test_index": test["test_index"],
                            "original": original_result,
                            "refactored": None,
                            "matched_original": False,
                        }
                        for test, original_result in eligible
                    ],
                }
            )
            continue

        ref_runs = refactored_by_file.get(file_id, [])
        ref_by_key = {(test["group"], test["test_index"]): run for test, run in ref_runs}
        tests = []
        file_ok = True
        for test, original_result in eligible:
            refactored_result = ref_by_key.get((test["group"], test["test_index"]))
            if compare_mode == "expected":
                matched = (
                    refactored_result is not None
                    and refactored_result["returncode"] == 0
                    and not refactored_result["timed_out"]
                    and refactored_result["stderr"] == ""
                    and normalize_output(refactored_result["stdout"], normalize)
                    == normalize_output(test["expected"], normalize)
                )
            elif compare_mode == "original":
                matched = (
                    refactored_result is not None
                    and original_result["returncode"] == refactored_result["returncode"] == 0
                    and not original_result["timed_out"]
                    and not refactored_result["timed_out"]
                    and normalize_output(original_result["stdout"], normalize)
                    == normalize_output(refactored_result["stdout"], normalize)
                )
            else:
                raise ValueError(f"Unknown compare mode: {compare_mode}")
            tests.append(
                {
                    "group": test["group"],
                    "test_index": test["test_index"],
                    "original": original_result,
                    "refactored": refactored_result,
                    "matched_original": matched,
                }
            )
            totals["tests"] += 1
            totals["matched_original"] += int(matched)
            file_ok = file_ok and matched
        totals["files_matched_original"] += int(file_ok)
        file_results.append({"file_id": file_id, "status": "ok" if file_ok else "failed", "tests": tests})

    return {
        "cluster_id": cluster_id,
        "scope": "refactored",
        "result_dir": str(result_dir),
        "test_policy": {
            "baseline_filter": "original_passed_only",
            "description": "Only tests passed by the original solution are evaluated for the refactored solution.",
        },
        "summary": {
            **totals,
            "test_pass_rate": totals["matched_original"] / totals["tests"] if totals["tests"] else None,
            "file_pass_rate": totals["files_matched_original"] / totals["files"] if totals["files"] else None,
        },
        "files": file_results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run original or refactored CodeContests tests.")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--dataset-dir", type=Path, default=ROOT / "demo" / "dataset")
    parser.add_argument("--cluster-id", required=True)
    parser.add_argument("--result-dir", type=Path, help="Baseline cluster or subcluster result directory.")
    parser.add_argument("--timeout-sec", type=float, default=5.0)
    parser.add_argument("--test-limit", type=int, default=0, help="Per-file test limit. Use 0 for all tests.")
    parser.add_argument("--compare-mode", choices=["expected", "original"], default="original")
    parser.add_argument("--normalize", choices=["strip", "whitespace"], default="whitespace")
    parser.add_argument("--workers", type=int, default=0, help="Parallel test workers. 0 = auto (min(cpu_count, 16)). Override with TEST_WORKERS env.")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    manifest = load_manifest(args.manifest)
    cluster = find_cluster(manifest, args.cluster_id)
    test_limit = None if args.test_limit == 0 else args.test_limit
    if args.result_dir:
        result = test_refactored_cluster(
            cluster,
            args.dataset_dir,
            args.result_dir,
            args.timeout_sec,
            test_limit=test_limit,
            compare_mode=args.compare_mode,
            normalize=args.normalize,
            workers=args.workers,
        )
    else:
        result = test_original_cluster(
            cluster,
            args.dataset_dir,
            args.timeout_sec,
            test_limit=test_limit,
            normalize=args.normalize,
            workers=args.workers,
        )

    payload = json.dumps(result, indent=2, ensure_ascii=False)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(payload + "\n", encoding="utf-8")
    else:
        print(payload)


if __name__ == "__main__":
    main()
