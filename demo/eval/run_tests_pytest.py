"""Pytest-based test runner for the dataset_complex Scrapy slice.

The slice's tests are upstream pytest unit tests importing `scrapy.*` and
`tests.*`, so the stdin/stdout model of run_tests.py does not apply. Instead:

- a temp dir gets a full `scrapy` package (symlink-copied from the installed
  site-packages, with the slice's original or refactored files overlaid at
  their real package paths),
- `common.py` is placed at the temp root so refactored `import common` works,
- the dataset's `tests/` directory (including upstream test infra) is copied,
- pytest runs each test file once for the original layout and once for the
  refactored layout, and outcomes are compared per test case.

Result shapes mirror run_tests.py so the metrics/report pipeline is unchanged.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = ROOT / "demo" / "dataset_complex" / "cluster_manifest.json"
# --reactor=asyncio mirrors upstream scrapy's [tool.pytest.ini_options].addopts
PYTEST_ARGS = ["-q", "--no-header", "-p", "no:cacheprovider", "--reactor=asyncio"]


def progress(label: str, current: int, total: int) -> None:
    if total <= 0:
        return
    width = 24
    filled = int(width * current / total)
    bar = "#" * filled + "-" * (width - filled)
    print(f"\r{label} [{bar}] {current}/{total}", end="" if current < total else "\n", file=sys.stderr, flush=True)


def load_manifest(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def find_cluster(manifest: dict[str, Any], cluster_id: str) -> dict[str, Any]:
    for cluster in manifest["clusters"]:
        if cluster["cluster_id"] == cluster_id:
            return cluster
    raise KeyError(f"Unknown cluster_id: {cluster_id}")


def discover_scrapy_pkg() -> Path:
    proc = subprocess.run(
        [sys.executable, "-c", "import scrapy, os; print(os.path.dirname(scrapy.__file__))"],
        text=True, capture_output=True, check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            "scrapy not importable in the current interpreter. "
            "Run: pip install 'scrapy @ git+https://github.com/scrapy/scrapy@<slice_commit>'\n" + proc.stderr
        )
    return Path(proc.stdout.strip())


def build_overlay(tmp: Path, cluster: dict[str, Any], dataset_dir: Path, result_dir: Path | None) -> None:
    """Materialize tmp/scrapy (installed package + slice overlay) and tmp/tests."""
    scrapy_pkg = discover_scrapy_pkg()
    overlay_targets: dict[str, Path] = {}
    for entry in cluster["files"]:
        source = (
            result_dir / "refactored" / entry["file_id"]
            if result_dir is not None
            else dataset_dir / "clusters" / cluster["cluster_id"] / "original" / entry["file_id"]
        )
        if not source.exists():
            raise FileNotFoundError(f"Missing source file {source}")
        overlay_targets[entry["rel_path"]] = source

    dest_pkg = tmp / "scrapy"
    dest_pkg.mkdir(parents=True)
    for root, dirs, files in os.walk(scrapy_pkg):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        rel_root = Path(root).relative_to(scrapy_pkg)
        (dest_pkg / rel_root).mkdir(parents=True, exist_ok=True)
        for name in files:
            rel = rel_root / name
            if str(rel) in overlay_targets:
                shutil.copy2(overlay_targets[str(rel)], dest_pkg / rel)
            else:
                os.symlink(Path(root) / name, dest_pkg / rel)

    if result_dir is not None and (result_dir / "common.py").exists():
        shutil.copy2(result_dir / "common.py", tmp / "common.py")

    test_dir = ROOT / cluster["test_dir"]
    shutil.copytree(test_dir, tmp / "tests", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))

    conftest = Path(__file__).resolve().parent / "pytest_conftest.py"
    shutil.copy2(conftest, tmp / "conftest.py")


def parse_junit(xml_path: Path) -> tuple[list[dict[str, Any]], dict[str, int]]:
    root = ET.parse(xml_path).getroot()
    cases = []
    counts = {"total": 0, "passed": 0, "failed": 0, "errors": 0, "skipped": 0}
    for suite in root.iter("testsuite"):
        counts["total"] += int(suite.get("tests", 0))
        counts["failed"] += int(suite.get("failures", 0))
        counts["errors"] += int(suite.get("errors", 0))
        counts["skipped"] += int(suite.get("skipped", 0))
    for case in root.iter("testcase"):
        outcome = "passed"
        for tag in ("failure", "error"):
            if case.find(tag) is not None:
                outcome = tag
        if case.find("skipped") is not None:
            outcome = "skipped"
        cases.append({"name": f"{case.get('classname')}::{case.get('name')}", "outcome": outcome})
    counts["passed"] = counts["total"] - counts["failed"] - counts["errors"] - counts["skipped"]
    return cases, counts


def run_pytest_file(tmp: Path, test_file: str, timeout_sec: float) -> dict[str, Any]:
    junit = tmp / "junit.xml"
    if junit.exists():
        junit.unlink()
    env = {**os.environ, "PYTHONPATH": str(tmp)}
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", *PYTEST_ARGS, f"--junitxml={junit}", f"tests/{test_file}"],
            cwd=tmp, env=env, text=True, capture_output=True, timeout=timeout_sec, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return {"timed_out": True, "cases": [], "counts": {"total": 0, "passed": 0, "failed": 0, "errors": 0, "skipped": 0}}
    if not junit.exists():
        return {
            "timed_out": False,
            "collection_failed": True,
            "returncode": proc.returncode,
            "stderr_tail": proc.stderr[-4000:],
            "cases": [],
            "counts": {"total": 0, "passed": 0, "failed": 0, "errors": 0, "skipped": 0},
        }
    cases, counts = parse_junit(junit)
    return {"timed_out": False, "cases": cases, "counts": counts}


def resolve_workers(workers: int | None) -> int:
    if workers is None:
        workers = int(os.getenv("TEST_WORKERS", "0"))
    if workers <= 0:
        workers = min(os.cpu_count() or 1, 16)
    return workers


def run_pytest_files_parallel(
    cluster: dict[str, Any],
    dataset_dir: Path,
    result_dir: Path | None,
    names: list[str],
    timeout_sec: float,
    label: str,
    workers: int | None,
) -> dict[str, dict[str, Any]]:
    """Run each pytest test file in its own overlay dir, in parallel.

    Per-file overlay dirs isolate concurrent pytest runs (junit output and any
    test-side state); overlay build is cheap (symlinks into site-packages).
    """
    workers = resolve_workers(workers)
    runs: dict[str, dict[str, Any]] = {}
    total = len(names)

    def run_one(name: str) -> dict[str, Any]:
        with tempfile.TemporaryDirectory(prefix="pytest_eval_") as tmp:
            build_overlay(Path(tmp), cluster, dataset_dir, result_dir)
            return run_pytest_file(Path(tmp), name, timeout_sec)

    done = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(run_one, name): name for name in names}
        for future in concurrent.futures.as_completed(futures):
            name = futures[future]
            try:
                runs[name] = future.result()
            except Exception as exc:
                runs[name] = {
                    "timed_out": False,
                    "collection_failed": True,
                    "stderr_tail": f"worker error: {exc!r}",
                    "cases": [],
                    "counts": {"total": 0, "passed": 0, "failed": 0, "errors": 0, "skipped": 0},
                }
            done += 1
            progress(label, done, total)
    return runs


def match_cases(original: dict[str, Any], refactored: dict[str, Any]) -> int:
    if original.get("timed_out") or original.get("collection_failed") or refactored.get("timed_out") or refactored.get("collection_failed"):
        return 0
    orig = {case["name"]: case["outcome"] for case in original["cases"]}
    matched = 0
    for case in refactored["cases"]:
        if case["outcome"] == orig.get(case["name"]):
            matched += 1
    return matched


def suite_ok(result: dict[str, Any]) -> bool:
    return not result.get("timed_out") and not result.get("collection_failed") and result["counts"]["failed"] == 0 and result["counts"]["errors"] == 0


def iter_file_tests(cluster: dict[str, Any]) -> list[tuple[str, str]]:
    """(file_id, test_file) pairs in manifest order, deduplicated per test file."""
    seen: set[str] = set()
    pairs = []
    for entry in cluster["files"]:
        for name in entry.get("tests", {}).get("pytest", []):
            if name not in seen:
                seen.add(name)
            pairs.append((entry["file_id"], name))
    return pairs


def run_pytest_both(
    cluster: dict[str, Any],
    dataset_dir: Path,
    result_dir: Path | None,
    timeout_sec: float,
    workers: int | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    cluster_id = cluster["cluster_id"]
    pairs = iter_file_tests(cluster)
    shared = cluster.get("shared_test_files", [])
    all_test_files = sorted({name for _, name in pairs} | set(shared))
    original_runs = run_pytest_files_parallel(
        cluster, dataset_dir, None, all_test_files, timeout_sec,
        f"pytest original cluster {cluster_id}", workers,
    )
    refactored_runs = run_pytest_files_parallel(
        cluster, dataset_dir, result_dir, all_test_files, timeout_sec,
        f"pytest refactored cluster {cluster_id}", workers,
    )
    return original_runs, refactored_runs


def test_refactored_cluster(
    cluster: dict[str, Any],
    dataset_dir: Path,
    result_dir: Path,
    timeout_sec: float,
    test_limit: int | None = None,
    compare_mode: str = "original",
    normalize: str = "whitespace",
    workers: int | None = None,
) -> dict[str, Any]:
    del test_limit, normalize  # no stdin cases in pytest mode
    cluster_id = cluster["cluster_id"]
    original_runs, refactored_runs = run_pytest_both(cluster, dataset_dir, result_dir, timeout_sec, workers)

    totals = {"tests": 0, "matched_original": 0, "files": 0, "files_matched_original": 0}
    file_results = []
    pairs = iter_file_tests(cluster)
    per_file_tests: dict[str, list[dict[str, Any]]] = {}
    for _, name in pairs:
        original = original_runs[name]
        refactored = refactored_runs[name]
        if compare_mode == "expected":
            matched_count = sum(1 for case in refactored["cases"] if case["outcome"] == "passed")
            matched = suite_ok(refactored)
        elif compare_mode == "original":
            matched_count = match_cases(original, refactored)
            matched = (original["counts"]["total"] == refactored["counts"]["total"] and matched_count == refactored["counts"]["total"])
        else:
            raise ValueError(f"Unknown compare mode: {compare_mode}")
        entry = {
            "test_file": name,
            "group": "pytest",
            "test_index": 0,
            "original": {"counts": original["counts"], "timed_out": original.get("timed_out", False)},
            "refactored": {"counts": refactored["counts"], "timed_out": refactored.get("timed_out", False)},
            "matched_original": matched,
        }
        totals["tests"] += refactored["counts"]["total"]
        totals["matched_original"] += matched_count
        for file_id, test_name in pairs:
            if test_name == name:
                per_file_tests.setdefault(file_id, []).append(entry)

    for file_id, tests in per_file_tests.items():
        file_ok = all(item["matched_original"] for item in tests)
        totals["files"] += 1
        totals["files_matched_original"] += int(file_ok)
        file_results.append({"file_id": file_id, "status": "ok" if file_ok else "failed", "tests": tests})

    shared_results = []
    for name in cluster.get("shared_test_files", []):
        original = original_runs[name]
        refactored = refactored_runs[name]
        if compare_mode == "expected":
            matched = suite_ok(refactored)
        else:
            matched = (original["counts"]["total"] == refactored["counts"]["total"] and match_cases(original, refactored) == refactored["counts"]["total"])
        shared_results.append({"test_file": name, "matched_original": matched, "original": original["counts"], "refactored": refactored["counts"]})

    return {
        "cluster_id": cluster_id,
        "scope": "refactored",
        "result_dir": str(result_dir),
        "summary": {
            **totals,
            "test_pass_rate": totals["matched_original"] / totals["tests"] if totals["tests"] else None,
            "file_pass_rate": totals["files_matched_original"] / totals["files"] if totals["files"] else None,
        },
        "files": file_results,
        "shared": shared_results,
    }


def test_original_cluster(
    cluster: dict[str, Any],
    dataset_dir: Path,
    timeout_sec: float,
    test_limit: int | None = None,
    normalize: str = "strip",
    workers: int | None = None,
) -> dict[str, Any]:
    del test_limit, normalize
    cluster_id = cluster["cluster_id"]
    pairs = iter_file_tests(cluster)
    shared = cluster.get("shared_test_files", [])
    all_test_files = sorted({name for _, name in pairs} | set(shared))
    runs = run_pytest_files_parallel(
        cluster, dataset_dir, None, all_test_files, timeout_sec,
        f"pytest original cluster {cluster_id}", workers,
    )

    totals = {"tests": 0, "expected_passed": 0, "files": 0, "files_expected_passed": 0}
    per_file_tests: dict[str, list[dict[str, Any]]] = {}
    for _, name in pairs:
        run = runs[name]
        totals["tests"] += run["counts"]["total"]
        totals["expected_passed"] += run["counts"]["passed"]
        for file_id, test_name in pairs:
            if test_name == name:
                per_file_tests.setdefault(file_id, []).append({"test_file": name, "run": run["counts"]})
    file_results = []
    for file_id, tests in per_file_tests.items():
        ok = all(item["run"]["failed"] == 0 and item["run"]["errors"] == 0 for item in tests)
        totals["files"] += 1
        totals["files_expected_passed"] += int(ok)
        file_results.append({"file_id": file_id, "all_expected_ok": ok, "tests": tests})
    return {
        "cluster_id": cluster_id,
        "scope": "original",
        "summary": {
            **totals,
            "test_pass_rate_vs_expected": totals["expected_passed"] / totals["tests"] if totals["tests"] else None,
            "file_pass_rate_vs_expected": totals["files_expected_passed"] / totals["files"] if totals["files"] else None,
        },
        "files": file_results,
        "shared": [{"test_file": name, "run": runs[name]["counts"]} for name in shared],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run pytest suites for the dataset_complex Scrapy slice.")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--dataset-dir", type=Path, default=ROOT / "demo" / "dataset_complex")
    parser.add_argument("--cluster-id", required=True)
    parser.add_argument("--result-dir", type=Path, help="Baseline cluster result directory (refactored mode).")
    parser.add_argument("--timeout-sec", type=float, default=300.0)
    parser.add_argument("--compare-mode", choices=["expected", "original"], default="original")
    parser.add_argument("--workers", type=int, default=0, help="Parallel pytest workers. 0 = auto (min(cpu_count, 16)). Override with TEST_WORKERS env.")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    manifest = load_manifest(args.manifest)
    cluster = find_cluster(manifest, args.cluster_id)
    if args.result_dir:
        result = test_refactored_cluster(cluster, args.dataset_dir, args.result_dir, args.timeout_sec, compare_mode=args.compare_mode, workers=args.workers)
    else:
        result = test_original_cluster(cluster, args.dataset_dir, args.timeout_sec, workers=args.workers)
    payload = json.dumps(result, indent=2, ensure_ascii=False)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(payload + "\n", encoding="utf-8")
    else:
        print(payload)


if __name__ == "__main__":
    main()
