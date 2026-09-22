"""Run one downstream task end to end.

This is the first smoke-test entry point: materialize future artifacts,
prepare an isolated C0 snapshot, invoke an external coding agent, and write
run.json.
It intentionally runs one task at a time so failures are easy to inspect.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .core import materialize, prepare, run_agent_command


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
        help="Defaults to .cache/downstream-artifacts/<task-id>",
    )
    parser.add_argument(
        "--workspace-root",
        type=Path,
        default=None,
        help="Defaults to .cache/downstream-workspaces (prepare() adds <task-id>/<variant>)",
    )
    parser.add_argument(
        "--result-dir",
        type=Path,
        default=None,
        help="Defaults to .cache/downstream-runs/<task-id>/<variant>; "
             "pass a fresh value for repeat runs",
    )
    parser.add_argument(
        "--agent-command",
        default="mini -t {task_prompt} -y --exit-immediately -o {trajectory_file}",
        help="External agent command; supports task/workspace/result/trajectory placeholders",
    )
    parser.add_argument("--timeout-sec", type=float, default=1800)
    args = parser.parse_args()
    if args.artifact_root is None:
        args.artifact_root = ROOT / ".cache" / "downstream-artifacts" / args.task_id
    if args.workspace_root is None:
        args.workspace_root = ROOT / ".cache" / "downstream-workspaces"
    if args.result_dir is None:
        args.result_dir = ROOT / ".cache" / "downstream-runs" / args.task_id / args.variant

    for path in (args.artifact_root, args.result_dir):
        if path.exists():
            raise SystemExit(f"refusing to overwrite existing path: {path}")

    print(f"[1/4] materialize future tests: {args.artifact_root}", flush=True)
    materialize(args.task_id, args.repo_cache, args.artifact_root, args.manifest)

    print(f"[2/4] prepare {args.variant} C0 workspace", flush=True)
    workspace = prepare(
        task_id=args.task_id,
        variant=args.variant,
        repo_cache=args.repo_cache,
        workspace_root=args.workspace_root,
        manifest=args.manifest,
    )

    print("[3/4] run agent in the C0 workspace", flush=True)
    print(f"       command: {args.agent_command}", flush=True)
    print("[4/4] evaluate future and regression tests", flush=True)
    result = run_agent_command(
        task_id=args.task_id,
        variant=args.variant,
        workspace=workspace,
        agent_command=args.agent_command,
        result_dir=args.result_dir,
        artifact_root=args.artifact_root,
        manifest=args.manifest,
        timeout_sec=args.timeout_sec,
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
