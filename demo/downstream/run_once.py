"""Run one downstream task end to end.

This is the first smoke-test entry point: materialize future artifacts,
prepare an isolated C0 snapshot, invoke an external coding agent, and write
run.json.
It intentionally runs one task at a time so failures are easy to inspect.
"""

from __future__ import annotations

import argparse
import json
import shutil
import time
from datetime import datetime
from pathlib import Path

from .core import DEFAULT_REFERENCES, materialize, prepare, run_agent_command
from .signal_context import materialize_signal_context
from demo.discovery.semantic_signal import DEFAULT_MODEL


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = ROOT / "demo" / "downstream" / "tasks.json"


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one downstream task end to end")
    parser.add_argument("--task-id", default="moto_service_cleanrooms")
    parser.add_argument("--variant", choices=("direct", "signal"), default="direct")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--repo-cache", type=Path, default=ROOT / ".cache" / "history")
    parser.add_argument(
        "--artifact-root",
        type=Path,
        default=None,
        help="Defaults to .cache/downstream-artifacts/<task-id>/<variant>/<run-id>",
    )
    parser.add_argument(
        "--workspace-root",
        type=Path,
        default=None,
        help="Defaults to .cache/downstream-workspaces/<task-id>/<variant>/<run-id>",
    )
    parser.add_argument(
        "--result-dir",
        type=Path,
        default=None,
        help="Defaults to .cache/downstream-runs/<task-id>/<variant>/<run-id>",
    )
    parser.add_argument(
        "--signal-context-root",
        type=Path,
        default=None,
        help="Defaults to .cache/downstream-signal-context/<task-id>/<variant>/<run-id>",
    )
    parser.add_argument("--signal-api-timeout-sec", type=float, default=None)
    parser.add_argument("--signal-max-output-tokens", type=int, default=None)
    parser.add_argument("--signal-embedding-model", default=DEFAULT_MODEL)
    parser.add_argument("--signal-skip-semantic", action="store_true")
    parser.add_argument("--signal-gate-workers", type=int, default=1)
    parser.add_argument(
        "--signal-retries", type=int, default=3,
        help="Attempts for Signal context generation when distillation fails validation",
    )
    parser.add_argument(
        "--signal-delivery-mode", choices=("reference", "reuse"), default="reference",
        help="reference provides read-only guidance; reuse qualifies and installs common modules",
    )
    parser.add_argument(
        "--agent-command",
        default="mini -t {task_prompt} -y --exit-immediately -o {trajectory_file}",
        help="External agent command; supports task/workspace/result/trajectory placeholders",
    )
    parser.add_argument("--timeout-sec", type=float, default=3600)
    parser.add_argument(
        "--references", type=Path, default=DEFAULT_REFERENCES,
        help="Frozen per-task reference test IDs for fixed-denominator future "
             "scoring; tasks without a reference file fall back to collected counts",
    )
    args = parser.parse_args()
    if args.variant == "direct" and args.signal_delivery_mode != "reference":
        parser.error("--signal-delivery-mode reuse requires --variant signal")
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    cache_root = ROOT / ".cache"
    workspace_root_was_default = args.workspace_root is None
    if args.artifact_root is None:
        args.artifact_root = cache_root / "downstream-artifacts" / args.task_id / args.variant / run_id
    if args.workspace_root is None:
        args.workspace_root = cache_root / "downstream-workspaces"
    if args.result_dir is None:
        args.result_dir = cache_root / "downstream-runs" / args.task_id / args.variant / run_id
    if args.signal_context_root is None:
        args.signal_context_root = cache_root / "downstream-signal-context" / args.task_id / args.variant / run_id

    # Default signal flow: one distilled context pack per task, shared by every
    # run (distillation is a one-off cost in the A/B design and the pack must be
    # identical across repeats; generation retries keep the .partial resume
    # state). An explicit --signal-context-root keeps the old per-run behavior.
    signal_context_root_was_default = args.signal_context_root == (
        cache_root / "downstream-signal-context" / args.task_id / args.variant / run_id
    )
    shared_signal_root = (
        cache_root / "downstream-signal-context-shared" / args.task_id
        if signal_context_root_was_default else None
    )

    paths = [args.artifact_root, args.result_dir]
    if args.variant == "signal" and shared_signal_root is None:
        paths.append(args.signal_context_root)
    for path in paths:
        if path.exists():
            raise SystemExit(f"refusing to overwrite existing path: {path}")

    workspace_run_id = run_id if workspace_root_was_default else None

    # Every default output path contains this run_id. Explicit path arguments
    # remain available for callers that need to resume or integrate elsewhere.
    print(f"run id: {run_id}", flush=True)
    print(f"[1/5] materialize future tests: {args.artifact_root}", flush=True)
    try:
        materialize(args.task_id, args.repo_cache, args.artifact_root, args.manifest)

        signal_context = None
        signal_context_started = time.monotonic()
        if args.variant == "signal":
            context_target = shared_signal_root or args.signal_context_root
            if shared_signal_root is not None and (shared_signal_root / "SKILL.md").is_file():
                print(f"[2/5] reuse shared Signal context: {shared_signal_root}", flush=True)
                signal_context = shared_signal_root
            else:
                print(f"[2/5] materialize Signal context: {context_target}", flush=True)
                attempts = max(1, args.signal_retries)
                for attempt in range(1, attempts + 1):
                    try:
                        signal_context = materialize_signal_context(
                            args.task_id,
                            args.repo_cache,
                            context_target,
                            args.manifest,
                            api_timeout_sec=args.signal_api_timeout_sec,
                            max_output_tokens=args.signal_max_output_tokens,
                            embedding_model=args.signal_embedding_model,
                            skip_semantic=args.signal_skip_semantic,
                            gate_workers=args.signal_gate_workers,
                        )
                        break
                    except Exception:
                        if attempt == attempts:
                            raise
                        print(
                            f"signal context attempt {attempt}/{attempts} failed; "
                            "retrying (partial state is reused)",
                            flush=True,
                        )
        signal_context_generation_wall_time_sec = (
            time.monotonic() - signal_context_started if signal_context is not None else 0.0
        )
    except Exception:
        # artifact_root did not exist when this command started and is cheap
        # to regenerate, so remove it to keep retry a single command. The
        # Signal work directory (<signal-context-root>.partial) is kept for
        # resume by materialize_signal_context.
        shutil.rmtree(args.artifact_root, ignore_errors=True)
        raise

    print(f"[3/5] prepare {args.variant} C0 workspace", flush=True)
    workspace = prepare(
        task_id=args.task_id,
        variant=args.variant,
        repo_cache=args.repo_cache,
        workspace_root=args.workspace_root,
        manifest=args.manifest,
        run_id=workspace_run_id,
    )

    print("[4/5] run agent in the C0 workspace", flush=True)
    print(f"       command: {args.agent_command}", flush=True)
    print("[5/5] evaluate future and regression tests", flush=True)
    result = run_agent_command(
        task_id=args.task_id,
        variant=args.variant,
        workspace=workspace,
        agent_command=args.agent_command,
        result_dir=args.result_dir,
        artifact_root=args.artifact_root,
        signal_context_dir=signal_context,
        signal_context_generation_wall_time_sec=signal_context_generation_wall_time_sec,
        signal_delivery_mode=args.signal_delivery_mode,
        manifest=args.manifest,
        timeout_sec=args.timeout_sec,
        references_root=args.references,
    )
    print(json.dumps({
        "status": result["status"],
        "tests": result["tests"],
        "usage": result["usage"],
        "run_json": str(args.result_dir / "run.json"),
    }, ensure_ascii=False, indent=2))
    if result["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
