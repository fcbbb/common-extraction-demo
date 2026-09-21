"""The complete, intentionally small runner for downstream tasks.

The runner owns only dataset plumbing: task validation, C0 workspaces, an
external coding-agent command, test execution, and ``run.json``. It does not
implement an LLM agent.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import time
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = ROOT / "demo" / "downstream" / "tasks.json"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
REQUIRED_FIELDS = {
    "task_id", "repository", "history", "evolution_pattern", "agent_task",
    "future_test_paths", "regression_test_paths", "future_test_command",
    "regression_test_command",
}


@dataclass
class CommandResult:
    command: list[str]
    cwd: str
    returncode: int | None
    timed_out: bool
    wall_time_sec: float
    stdout: str
    stderr: str


def run_command(
    command: str | Sequence[str],
    cwd: Path,
    timeout_sec: float,
    *,
    env: Mapping[str, str] | None = None,
    artifact_dir: Path | None = None,
) -> CommandResult:
    argv = shlex.split(command) if isinstance(command, str) else list(command)
    if not argv:
        raise ValueError("command must not be empty")
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    started = time.monotonic()
    try:
        completed = subprocess.run(
            argv, cwd=str(cwd), env=merged_env, text=True,
            capture_output=True, timeout=timeout_sec, check=False,
        )
        result = CommandResult(argv, str(cwd), completed.returncode, False,
                               time.monotonic() - started, completed.stdout, completed.stderr)
    except subprocess.TimeoutExpired as exc:
        result = CommandResult(argv, str(cwd), None, True, time.monotonic() - started,
                               exc.stdout or "", exc.stderr or "")
    if artifact_dir:
        artifact_dir.mkdir(parents=True, exist_ok=True)
        (artifact_dir / "command.json").write_text(json.dumps(asdict(result), indent=2) + "\n")
        (artifact_dir / "stdout.txt").write_text(result.stdout)
        (artifact_dir / "stderr.txt").write_text(result.stderr)
    return result


def _safe_path(value: str) -> Path:
    path = Path(value)
    if not value or path.is_absolute() or ".." in path.parts:
        raise ValueError(f"unsafe relative path: {value!r}")
    return path


def validate_manifest(payload: dict[str, Any]) -> None:
    if payload.get("schema") != "downstream-history-tasks-v1":
        raise ValueError("unsupported manifest schema")
    tasks = payload.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise ValueError("tasks must be a non-empty list")
    seen: set[str] = set()
    for index, task in enumerate(tasks):
        missing = REQUIRED_FIELDS - set(task)
        if missing:
            raise ValueError(f"tasks[{index}] missing: {sorted(missing)}")
        task_id = task["task_id"]
        if not isinstance(task_id, str) or not task_id or task_id in seen:
            raise ValueError(f"invalid duplicate task_id: {task_id!r}")
        seen.add(task_id)
        for key in ("slug", "url", "license"):
            if not task["repository"].get(key):
                raise ValueError(f"tasks[{index}].repository.{key} is required")
        for key in ("c0", "future_start", "future_end"):
            value = task["history"].get(key, "")
            if not SHA_RE.fullmatch(value):
                raise ValueError(f"tasks[{index}].history.{key} must be a full SHA")
        for field in ("signal_history_paths", "future_test_paths", "test_support_paths",
                      "future_patch_audit_paths", "regression_test_paths"):
            for value in task.get(field, []):
                _safe_path(value)


def load_manifest(path: Path = DEFAULT_MANIFEST) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    validate_manifest(payload)
    return payload


def load_task(task_id: str, manifest: Path = DEFAULT_MANIFEST) -> dict[str, Any]:
    for task in load_manifest(manifest)["tasks"]:
        if task["task_id"] == task_id:
            return task
    raise KeyError(f"unknown task: {task_id}")


def _git(repo: Path, args: Sequence[str], timeout_sec: float = 120) -> CommandResult:
    return run_command(["git", *args], repo, timeout_sec)


def _ensure_repo(url: str, repo: Path) -> Path:
    if (repo / ".git").is_dir():
        return repo.resolve()
    repo.parent.mkdir(parents=True, exist_ok=True)
    result = run_command(["git", "clone", url, str(repo)], repo.parent, 900)
    if result.returncode != 0:
        raise RuntimeError(f"git clone failed: {result.stderr[-2000:]}")
    return repo.resolve()


def _verify_history(repo: Path, c0: str, future_end: str) -> None:
    for commit in (c0, future_end):
        if _git(repo, ["cat-file", "-e", f"{commit}^{{commit}}"]).returncode != 0:
            raise ValueError(f"commit unavailable: {commit}")
    if _git(repo, ["merge-base", "--is-ancestor", c0, future_end]).returncode != 0:
        raise ValueError(f"{c0} is not an ancestor of {future_end}")


def prepare(*, task_id: str, variant: str, repo_cache: Path, workspace_root: Path,
            manifest: Path = DEFAULT_MANIFEST) -> Path:
    if variant not in {"direct", "signal"}:
        raise ValueError(f"unsupported variant: {variant}")
    task = load_task(task_id, manifest)
    repo = _ensure_repo(task["repository"]["url"], repo_cache / task["repository"]["slug"])
    c0, future_end = task["history"]["c0"], task["history"]["future_end"]
    _verify_history(repo, c0, future_end)
    worktree = workspace_root / task_id / variant / "source"
    if worktree.exists():
        raise FileExistsError(f"refusing to overwrite worktree: {worktree}")
    worktree.parent.mkdir(parents=True, exist_ok=True)
    result = _git(repo, ["worktree", "add", "--detach", str(worktree), c0])
    if result.returncode != 0:
        raise RuntimeError(f"git worktree failed: {result.stderr[-2000:]}")
    (worktree / ".downstream-workspace.json").write_text(json.dumps({
        "task_id": task_id, "variant": variant, "source_commit": c0,
        "future_commit": future_end,
    }, indent=2) + "\n")
    return worktree.resolve()


def _tree_files(repo: Path, commit: str, requested: str) -> list[str]:
    result = _git(repo, ["ls-tree", "-r", "--name-only", commit, "--", requested])
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-2000:])
    files = [line for line in result.stdout.splitlines() if line]
    if files:
        return files
    if _git(repo, ["cat-file", "-e", f"{commit}:{requested}"]).returncode != 0:
        raise FileNotFoundError(requested)
    return [requested]


def materialize(task_id: str, repo_cache: Path, artifact_root: Path,
                manifest: Path = DEFAULT_MANIFEST) -> dict[str, Any]:
    """Export visible future tests and hidden reference code separately."""
    task = load_task(task_id, manifest)
    repo = _ensure_repo(task["repository"]["url"], repo_cache / task["repository"]["slug"])
    c0, future_end = task["history"]["c0"], task["history"]["future_end"]
    _verify_history(repo, c0, future_end)
    if artifact_root.exists():
        raise FileExistsError(f"refusing to overwrite: {artifact_root}")
    entries: list[dict[str, Any]] = []
    for kind, destination, paths in (
        ("future_test", artifact_root / "tests", task["future_test_paths"]),
        ("test_support", artifact_root / "tests", task.get("test_support_paths", [])),
        ("ground_truth", artifact_root / "ground_truth", task["future_patch_audit_paths"]),
    ):
        for requested in paths:
            for source in _tree_files(repo, future_end, requested):
                relative = _safe_path(source)
                data = subprocess.run(["git", "show", f"{future_end}:{source}"], cwd=repo,
                                      capture_output=True, check=True).stdout
                target = destination / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                entries.append({"kind": kind, "path": source,
                                "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)})
    artifact_root.mkdir(parents=True, exist_ok=True)
    payload = {"schema": "downstream-artifacts-v1", "task_id": task_id,
               "source_commit": c0, "future_commit": future_end,
               "tests_dir": "tests", "ground_truth_dir": "ground_truth", "entries": entries}
    (artifact_root / "manifest.json").write_text(json.dumps(payload, indent=2) + "\n")
    return payload


def inject_tests(artifact_root: Path, workspace: Path) -> list[str]:
    payload = json.loads((artifact_root / "manifest.json").read_text())
    injected = []
    for entry in payload["entries"]:
        if entry["kind"] not in {"future_test", "test_support"}:
            continue
        relative = _safe_path(entry["path"])
        source = artifact_root / payload["tests_dir"] / relative
        target = workspace / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        injected.append(entry["path"])
    return injected


def _junit(path: Path) -> dict[str, Any]:
    root = ET.parse(path).getroot()
    cases = []
    for case in root.iter("testcase"):
        outcome = "passed"
        if case.find("failure") is not None:
            outcome = "failed"
        elif case.find("error") is not None:
            outcome = "error"
        elif case.find("skipped") is not None:
            outcome = "skipped"
        cases.append({"name": f"{case.get('classname', '')}::{case.get('name', '')}", "outcome": outcome})
    total = len(cases) or int(root.get("tests", 0))
    failed = sum(c["outcome"] == "failed" for c in cases)
    errors = sum(c["outcome"] == "error" for c in cases)
    skipped = sum(c["outcome"] == "skipped" for c in cases)
    return {"total": total, "passed": total - failed - errors - skipped,
            "failed": failed, "errors": errors, "skipped": skipped, "cases": cases}


def _empty_result(task_id: str, variant: str, model: str) -> dict[str, Any]:
    return {"task_id": task_id, "variant": variant, "model": model, "status": "not_started",
            "tests": {"future_passed": 0, "future_total": 0, "regression_passed": 0, "regression_total": 0},
            "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0,
                      "api_calls": 0, "agent_turns": 0, "tool_calls": 0, "wall_time_sec": 0.0},
            "code": {"changed_files": 0, "patch_lines": 0}, "artifacts": {}, "error": None}


def run_agent_command(*, task_id: str, variant: str, workspace: Path,
                      agent_command: str | Sequence[str], result_dir: Path,
                      manifest: Path = DEFAULT_MANIFEST, timeout_sec: float = 1800,
                      model: str = "external-agent") -> dict[str, Any]:
    task = load_task(task_id, manifest)
    result_dir.mkdir(parents=True, exist_ok=True)
    task_file = result_dir / "TASK.md"
    task_file.write_text(task["agent_task"].strip() + "\n", encoding="utf-8")
    raw = shlex.split(agent_command) if isinstance(agent_command, str) else list(agent_command)
    argv = [item.format(task_file=str(task_file), task_prompt=task["agent_task"].strip(),
                        workspace=str(workspace), task_id=task_id) for item in raw]
    result = _empty_result(task_id, variant, model)
    started = time.monotonic()
    env = {"DOWNSTREAM_TASK_FILE": str(task_file), "DOWNSTREAM_TASK_ID": task_id,
           "DOWNSTREAM_VARIANT": variant}
    agent = run_command(argv, workspace, timeout_sec, env=env, artifact_dir=result_dir / "agent")
    result["artifacts"]["agent"] = asdict(agent)
    result["usage"]["wall_time_sec"] = time.monotonic() - started
    if agent.returncode != 0 or agent.timed_out:
        result["status"] = "timeout" if agent.timed_out else "error"
        result["error"] = "agent_command_failed"
    else:
        for suite in ("future", "regression"):
            test_argv = shlex.split(task[f"{suite}_test_command"])
            junit = result_dir / suite / "junit.xml"
            if any(item.endswith("pytest") or item == "pytest" for item in test_argv):
                test_argv.append(f"--junitxml={junit}")
            test = run_command(test_argv, workspace, timeout_sec, artifact_dir=result_dir / suite)
            result["artifacts"][suite] = asdict(test)
            if junit.exists():
                report = _junit(junit)
                result["tests"][f"{suite}_passed"] = report["passed"]
                result["tests"][f"{suite}_total"] = report["total"]
            if test.returncode != 0 or test.timed_out:
                result["status"] = "timeout" if test.timed_out else "failed"
                result["error"] = f"{suite}_tests_failed"
                break
        else:
            result["status"] = "passed"
    (result_dir / "run.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Minimal downstream task runner")
    parser.add_argument("command", choices=("validate",))
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    args = parser.parse_args()
    if args.command == "validate":
        payload = load_manifest(args.manifest)
        print(json.dumps({"tasks": len(payload["tasks"]), "status": "valid"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
