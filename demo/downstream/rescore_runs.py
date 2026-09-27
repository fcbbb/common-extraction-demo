"""Rescore downstream runs against frozen reference test IDs.

Runs produced before the reference scoring was integrated into the runner
report ``future_passed/future_total`` as whatever pytest collected inside the
agent's workspace, so the denominator drifts: agents can add their own test
files (inflating the total) or break imports so most reference tests never
collect (collapsing it to a handful of collection errors).  This module
re-derives the score from the run's ``junit.xml`` with a fixed denominator:
the set of test IDs the upstream future tests define (frozen per task in
``demo/datasets/swe_rebench_screen/references/<task>.xml``, generated once at
the gate-A future state; for tasks whose local rerun is network-bound,
extracted from a run whose junit shows clean full collection).  New runs
score this way inline in ``demo.downstream.core`` and need no rescore.

Scoring rule (fixed denominator): a reference test counts as passed iff its
ID appears as a passed testcase in the run's junit; missing, errored, failed,
and skipped reference tests all count as not passed.  Agent-added tests
outside the reference set are reported separately and never counted.

Writes ``rescored.json`` next to each ``run.json`` and prints a per-task
comparison table.  Read-only otherwise: ``run.json`` is never modified.

Usage::

    python -m demo.downstream.rescore_runs [--runs-root .cache/downstream-runs]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .core import junit_keys, load_reference

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RUNS_ROOT = ROOT / ".cache" / "downstream-runs"
DEFAULT_REFERENCES = ROOT / "demo" / "datasets" / "swe_rebench_screen" / "references"
DEFAULT_MANIFEST = ROOT / "demo" / "downstream" / "tasks.json"


def rescore_run(run_dir: Path, reference: set[str]) -> dict | None:
    junit = run_dir / "future" / "junit.xml"
    run_json = run_dir / "run.json"
    if not (junit.is_file() and run_json.is_file()):
        return None
    try:
        meta = json.loads(run_json.read_text())
    except json.JSONDecodeError:
        return None
    if meta.get("status") == "error":
        return None  # infra failures carry no agent signal
    outcomes = junit_keys(junit, "")  # run junits: strip via '.source.'
    passed = {k for k, v in outcomes.items() if v == "pass"}
    ref_passed = len(reference & passed)
    extra = sorted(k for k in outcomes if k not in reference)
    result = {
        "task_id": meta.get("task_id"),
        "variant": meta.get("variant"),
        "run_id": run_dir.name,
        "reference_total": len(reference),
        "reference_passed": ref_passed,
        "reference_pass_rate": round(ref_passed / max(1, len(reference)), 4),
        "runner_future_total": meta.get("tests", {}).get("future_total"),
        "non_reference_tests_collected": len(extra),
        "regression": meta.get("tests", {}).get("regression_passed"),
        "total_tokens": meta.get("usage", {}).get("total_tokens"),
        "agent_turns": meta.get("usage", {}).get("agent_turns"),
    }
    (run_dir / "rescored.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs-root", type=Path, default=DEFAULT_RUNS_ROOT)
    parser.add_argument("--references", type=Path, default=DEFAULT_REFERENCES)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text())
    # Reference junits are generated with cwd inside the history checkout and
    # carry classnames relative to the repo root: '.cache.history.<slug>.<relpath>'.
    repo_prefixes = {
        t["task_id"]: ".cache.history." + t["repository"]["slug"].replace("/", ".")
        for t in manifest["tasks"]
    }

    rows = []
    for task in manifest["tasks"]:
        task_id = task["task_id"]
        try:
            reference = load_reference(task_id, args.references, repo_prefixes[task_id])
        except FileNotFoundError as exc:
            print(f"skip {task_id}: {exc}")
            continue
        for run_dir in sorted((args.runs_root / task_id).glob("*/*/")):
            result = rescore_run(run_dir, reference)
            if result is not None:
                rows.append(result)

    # summary table
    print(f"{'task':38s} {'variant':7s} {'run':22s} {'ref passed':>11s} {'rate':>6s} {'extra':>5s} {'tokens':>10s}")
    for r in rows:
        print(f"{r['task_id']:38s} {r['variant']:7s} {r['run_id']:22s} "
              f"{r['reference_passed']:>5d}/{r['reference_total']:<5d} {r['reference_pass_rate']*100:>5.1f}% "
              f"{r['non_reference_tests_collected']:>5d} {r['total_tokens'] or 0:>10,}")

    # per-task aggregates
    print("\n=== per-task aggregate (fixed denominator) ===")
    stats: dict[tuple[str, str], list[float]] = {}
    toks: dict[tuple[str, str], list[int]] = {}
    for r in rows:
        key = (r["task_id"], r["variant"])
        stats.setdefault(key, []).append(r["reference_pass_rate"])
        toks.setdefault(key, []).append(r["total_tokens"] or 0)
    tasks = sorted({k[0] for k in stats})
    for t in tasks:
        for variant in ("direct", "signal"):
            key = (t, variant)
            if key not in stats:
                continue
            rates = stats[key]
            mean_rate = sum(rates) / len(rates)
            mean_tok = sum(toks[key]) / len(toks[key])
            print(f"{t:38s} {variant:7s} n={len(rates)}  mean {mean_rate*100:5.1f}%  "
                  f"[{' '.join(f'{x*100:.0f}%' for x in rates)}]  tok {mean_tok/1e6:.2f}M")


if __name__ == "__main__":
    main()
