"""The complete, intentionally small runner for downstream tasks.

The runner owns only dataset plumbing: task validation, C0 workspaces, an
external coding-agent command, test execution, and ``run.json``. It does not
implement an LLM agent.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import selectors
import shlex
import shutil
import subprocess
import tarfile
import time
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = ROOT / "demo" / "downstream" / "tasks.json"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
TASK_STATUS = {"selected", "gates_passed", "ready"}
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
    stream: bool = False,
) -> CommandResult:
    argv = shlex.split(command) if isinstance(command, str) else list(command)
    if not argv:
        raise ValueError("command must not be empty")
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    started = time.monotonic()
    if not stream:
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
    else:
        process = subprocess.Popen(
            argv, cwd=str(cwd), env=merged_env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=1,
        )
        selector = selectors.DefaultSelector()
        assert process.stdout is not None and process.stderr is not None
        selector.register(process.stdout, selectors.EVENT_READ, "stdout")
        selector.register(process.stderr, selectors.EVENT_READ, "stderr")
        captured = {"stdout": [], "stderr": []}
        timed_out = False
        while selector.get_map():
            remaining = timeout_sec - (time.monotonic() - started)
            if remaining <= 0:
                timed_out = True
                process.kill()
                break
            for key, _ in selector.select(min(0.25, remaining)):
                line = key.fileobj.readline()
                if not line:
                    selector.unregister(key.fileobj)
                    continue
                captured[key.data].append(line)
                print(line, end="", flush=True)
        selector.close()
        if not timed_out:
            try:
                process.wait(timeout=max(1.0, timeout_sec))
            except subprocess.TimeoutExpired:
                # Both output streams closed but the process still runs.
                timed_out = True
        if timed_out:
            process.kill()
            process.wait()
        result = CommandResult(
            argv, str(cwd), None if timed_out else process.returncode, timed_out,
            time.monotonic() - started, "".join(captured["stdout"]), "".join(captured["stderr"]),
        )
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
        if task.get("status") not in TASK_STATUS:
            raise ValueError(
                f"tasks[{index}].status must be one of {sorted(TASK_STATUS)}")
        if not str(task.get("agent_task", "")).strip():
            raise ValueError(f"tasks[{index}].agent_task must be a non-empty string")
        test_env = task.get("test_env", {})
        if not isinstance(test_env, dict) or not all(
                isinstance(key, str) and isinstance(value, str)
                for key, value in test_env.items()):
            raise ValueError(
                f"tasks[{index}].test_env must map variable names to string values")
        for key in ("slug", "url", "license"):
            if not task["repository"].get(key):
                raise ValueError(f"tasks[{index}].repository.{key} is required")
        for key in ("c0", "future_start", "future_end"):
            value = task["history"].get(key, "")
            if not SHA_RE.fullmatch(value):
                raise ValueError(f"tasks[{index}].history.{key} must be a full SHA")
        for field in ("signal_history_paths", "future_test_paths", "test_support_paths",
                      "future_patch_audit_paths", "regression_test_paths",
                      "future_test_adapter", "regression_test_adapter"):
            values = task.get(field, [])
            if isinstance(values, str):
                values = [values]
            for value in values:
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
    # Export only C0 into a fresh repository. A regular Git worktree would
    # retain the cache repository's refs and objects, making future commits
    # discoverable by the Agent.
    worktree = (workspace_root / task_id / variant / "source").resolve()
    if worktree.exists():
        raise FileExistsError(f"refusing to overwrite snapshot: {worktree}")
    worktree.parent.mkdir(parents=True, exist_ok=True)
    archive = subprocess.run(
        ["git", "archive", "--format=tar", c0],
        cwd=repo,
        capture_output=True,
        check=False,
    )
    if archive.returncode != 0:
        raise RuntimeError(f"git archive failed: {archive.stderr.decode(errors='replace')[-2000:]}")
    worktree.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(archive.stdout), mode="r:") as tar:
        try:
            tar.extractall(worktree, filter="data")
        except TypeError:  # Python without the PEP 706 backport
            tar.extractall(worktree)

    for command in (
        ["git", "init"],
        ["git", "add", "-A", "-f", "."],
        [
            "git", "-c", "user.name=downstream-snapshot",
            "-c", "user.email=downstream-snapshot@localhost",
            "commit", "--no-gpg-sign", "-m", "C0 snapshot",
        ],
    ):
        result = run_command(command, worktree, 120)
        if result.returncode != 0:
            raise RuntimeError(f"snapshot command failed: {result.stderr[-2000:]}")
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


def inject_tests(artifact_root: Path, workspace: Path) -> dict[str, list[str]]:
    """Copy held-out tests into the workspace and report what was replaced."""
    payload = json.loads((artifact_root / "manifest.json").read_text())
    injected: list[str] = []
    overwritten: list[str] = []
    for entry in payload["entries"]:
        if entry["kind"] not in {"future_test", "test_support"}:
            continue
        relative = _safe_path(entry["path"])
        source = artifact_root / payload["tests_dir"] / relative
        target = workspace / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            overwritten.append(entry["path"])
        shutil.copy2(source, target)
        injected.append(entry["path"])
    return {"injected": injected, "overwritten": overwritten}


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
    return {"task_id": task_id, "variant": variant, "model": model,
            "workspace_kind": "c0_original", "status": "not_started",
            "tests": {"future_passed": 0, "future_total": 0, "regression_passed": 0, "regression_total": 0},
            "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0,
                      "api_calls": 0, "agent_turns": 0, "tool_calls": 0,
                      "cost_usd": 0.0, "agent_wall_time_sec": 0.0,
                      "evaluation_wall_time_sec": 0.0, "wall_time_sec": 0.0},
            "code": {"changed_files": 0, "patch_lines": 0}, "artifacts": {}, "error": None}


def _code_change_stats(workspace: Path, base_commit: str,
                       baseline_untracked: set[str] | None = None) -> dict[str, int]:
    """Return the code changes made since the initial workspace snapshot.

    ``git diff`` does not include untracked files, which are common for a
    coding agent (for example, a newly added driver).  Count those separately
    and do this before evaluator tests are injected into the workspace.
    """
    changed = set()
    patch_lines = 0

    names = _git(workspace, ["diff", "--name-only", base_commit, "--"])
    if names.returncode == 0:
        changed.update(line for line in names.stdout.splitlines() if line)

    numstat = _git(workspace, ["diff", "--numstat", base_commit, "--"])
    if numstat.returncode == 0:
        for line in numstat.stdout.splitlines():
            fields = line.split("\t", 2)
            if len(fields) < 2:
                continue
            additions, deletions = fields[:2]
            if additions.isdigit():
                patch_lines += int(additions)
            if deletions.isdigit():
                patch_lines += int(deletions)

    untracked = _git(workspace, ["ls-files", "--others", "--exclude-standard", "-z"])
    if untracked.returncode == 0:
        for relative in (item for item in untracked.stdout.split("\0") if item):
            if baseline_untracked and relative in baseline_untracked:
                continue
            changed.add(relative)
            path = workspace / relative
            try:
                data = path.read_bytes()
            except (OSError, IsADirectoryError):
                continue
            # Binary files have no meaningful line-based patch size.
            if b"\0" not in data and data:
                patch_lines += data.count(b"\n")
                if not data.endswith(b"\n"):
                    patch_lines += 1

    return {"changed_files": len(changed), "patch_lines": patch_lines}


def _trajectory_usage(path: Path) -> dict[str, Any]:
    """Extract usage counters from a mini-SWE-agent trajectory, if present."""
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    stats = payload.get("info", {}).get("model_stats", {})
    input_tokens = output_tokens = total_tokens = tool_calls = 0
    for message in payload.get("messages", []):
        extra = message.get("extra", {}) or {}
        tool_calls += len(extra.get("actions", []) or [])
        response = extra.get("response", {}) or {}
        usage = response.get("usage", {}) if isinstance(response, dict) else {}
        input_tokens += int(usage.get("prompt_tokens", usage.get("input_tokens", 0)) or 0)
        output_tokens += int(usage.get("completion_tokens", usage.get("output_tokens", 0)) or 0)
        total_tokens += int(usage.get("total_tokens", 0) or 0)
    if total_tokens == 0:
        total_tokens = input_tokens + output_tokens
    api_calls = int(stats.get("api_calls", 0) or 0)
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "api_calls": api_calls,
        "agent_turns": api_calls,
        "tool_calls": tool_calls,
        "cost_usd": float(stats.get("instance_cost", 0.0) or 0.0),
    }


def _command_failed(result: CommandResult) -> bool:
    return result.timed_out or result.returncode != 0


def _format_test_command(task: dict[str, Any], suite: str, workspace: Path,
                         result_dir: Path) -> str:
    """Expand runner-owned paths in a task's test command.

    A test adapter lives outside the agent workspace so it can provide a
    stable evaluator-facing contract without exposing the held-out test
    implementation to the coding agent.
    """
    command = task[f"{suite}_test_command"]
    replacements = {
        "{workspace}": str(workspace),
        "{result_dir}": str(result_dir),
    }
    adapter = task.get(f"{suite}_test_adapter")
    if adapter:
        replacements[f"{{{suite}_test_adapter}}"] = str((ROOT / _safe_path(adapter)).resolve())
    for placeholder, value in replacements.items():
        command = command.replace(placeholder, value)
    return command


def _run_task_setup(task: dict[str, Any], workspace: Path, result_dir: Path,
                    timeout_sec: float) -> tuple[dict[str, Any], str | None]:
    """Run task-declared environment setup before starting the agent.

    ``install_command`` and ``test_setup`` are part of the task contract. They
    must run in the isolated C0 checkout, otherwise a correct implementation
    can fail because test-only support files or optional dependencies are
    missing. Commands are argv-parsed by ``run_command``; no shell is used.
    """
    setup_dir = result_dir / "setup"
    setup_dir.mkdir(parents=True, exist_ok=True)
    records: dict[str, Any] = {"commands": []}

    commands: list[tuple[str, str]] = []
    install_command = task.get("install_command")
    if install_command:
        commands.append(("install", install_command))
    for index, command in enumerate(task.get("test_setup", [])):
        commands.append((f"test_setup_{index}", command))

    for name, command in commands:
        try:
            command_result = run_command(
                command,
                workspace,
                timeout_sec,
                artifact_dir=setup_dir / name,
                stream=True,
            )
        except OSError as exc:
            records["commands"].append({"name": name, "command": command,
                                         "error": repr(exc)})
            (setup_dir / "setup.json").write_text(
                json.dumps(records, indent=2) + "\n", encoding="utf-8"
            )
            return records, f"{name}_command_error"

        record = asdict(command_result)
        record["name"] = name
        records["commands"].append(record)
        if _command_failed(command_result):
            (setup_dir / "setup.json").write_text(
                json.dumps(records, indent=2) + "\n", encoding="utf-8"
            )
            return records, f"{name}_failed"

    (setup_dir / "setup.json").write_text(
        json.dumps(records, indent=2) + "\n", encoding="utf-8"
    )
    return records, None


def _copy_signal_context(source_root: Path, workspace: Path) -> Path:
    """Copy only the agent-facing Signal pack into the C0 workspace.

    The audit ``manifest.json`` (which carries source and future commit
    hashes) deliberately stays behind; ``SKILL.md`` already indexes every
    file the agent needs.
    """
    source_root = source_root.resolve()
    if not source_root.is_dir():
        raise FileNotFoundError(f"signal context directory not found: {source_root}")
    destination = workspace / ".downstream" / "signal-context"
    if destination.exists():
        raise FileExistsError(f"refusing to overwrite signal context: {destination}")
    required_files = ("SKILL.md",)
    required_dirs = ("patterns", "snippets")
    if any(not (source_root / name).is_file() for name in required_files):
        raise FileNotFoundError(f"incomplete signal context pack: {source_root}")
    if any(not (source_root / name).is_dir() for name in required_dirs):
        raise FileNotFoundError(f"incomplete signal context pack: {source_root}")
    destination.mkdir(parents=True, exist_ok=True)
    for name in required_files:
        shutil.copy2(source_root / name, destination / name)
    for name in required_dirs:
        shutil.copytree(source_root / name, destination / name)
    return destination


def _untracked_files(workspace: Path) -> set[str]:
    result = _git(workspace, ["ls-files", "--others", "--exclude-standard", "-z"])
    if result.returncode != 0:
        return set()
    return {item for item in result.stdout.split("\0") if item}


def run_agent_command(*, task_id: str, variant: str, workspace: Path,
                      agent_command: str | Sequence[str], result_dir: Path,
                      artifact_root: Path | None = None,
                      signal_context_dir: Path | None = None,
                      manifest: Path = DEFAULT_MANIFEST, timeout_sec: float = 1800,
                      model: str = "external-agent") -> dict[str, Any]:
    workspace = workspace.resolve()
    result_dir = result_dir.resolve()
    if artifact_root is not None:
        artifact_root = artifact_root.resolve()
    if signal_context_dir is not None:
        signal_context_dir = signal_context_dir.resolve()
    if variant == "signal" and signal_context_dir is None:
        raise ValueError("signal variant requires signal_context_dir")
    if variant == "direct" and signal_context_dir is not None:
        raise ValueError("direct variant must not receive signal_context_dir")
    task = load_task(task_id, manifest)
    result_dir.mkdir(parents=True, exist_ok=True)
    signal_context_file = None
    if signal_context_dir is not None:
        signal_context_file = _copy_signal_context(signal_context_dir, workspace) / "SKILL.md"
    task_prompt = task["agent_task"].strip()
    if signal_context_file is not None:
        task_prompt += (
            "\n\nImplementation guidance distilled from comparable services in this "
            f"repository is available at {signal_context_file.relative_to(workspace)}. "
            "Consult it while implementing."
        )
    task_file = result_dir / "TASK.md"
    task_file.write_text(task_prompt + "\n", encoding="utf-8")
    trajectory_file = result_dir / "agent" / "trajectory.json"
    trajectory_file.parent.mkdir(parents=True, exist_ok=True)
    raw = shlex.split(agent_command) if isinstance(agent_command, str) else list(agent_command)
    argv = [item.format(
        task_file=str(task_file), task_prompt=task_prompt,
        workspace=str(workspace), task_id=task_id,
        result_dir=str(result_dir), trajectory_file=str(trajectory_file),
        signal_context_dir=str(signal_context_file.parent) if signal_context_file else "",
        signal_context_file=str(signal_context_file) if signal_context_file else "",
    ) for item in raw]
    result = _empty_result(task_id, variant, model)
    started = time.monotonic()
    setup, setup_error = _run_task_setup(task, workspace, result_dir, timeout_sec)
    result["artifacts"]["setup"] = setup
    if setup_error is not None:
        result["status"] = "timeout" if setup_error.endswith("_failed") and any(
            item.get("timed_out") for item in setup.get("commands", [])
            if isinstance(item, dict)
        ) else "error"
        result["error"] = setup_error
        result["usage"]["wall_time_sec"] = time.monotonic() - started
        (result_dir / "run.json").write_text(json.dumps(result, indent=2) + "\n")
        return result
    # Establish the comparison point after setup, so dependency/test setup
    # changes are not attributed to the coding agent.
    base_result = _git(workspace, ["rev-parse", "HEAD"])
    base_commit = base_result.stdout.strip() if base_result.returncode == 0 else None
    baseline_untracked = _untracked_files(workspace)
    # Only the task file is exposed to the agent process; task/variant
    # identifiers would tell it which arm of the experiment it is in.
    env = {"DOWNSTREAM_TASK_FILE": str(task_file)}
    if signal_context_file is not None:
        env["DOWNSTREAM_SIGNAL_CONTEXT_DIR"] = str(signal_context_file.parent)
        env["DOWNSTREAM_SIGNAL_CONTEXT_FILE"] = str(signal_context_file)
        result["artifacts"]["signal_context"] = str(signal_context_file.parent)
        result["artifacts"]["signal_context_source"] = str(signal_context_dir)
    try:
        agent = run_command(
            argv, workspace, timeout_sec, env=env,
            artifact_dir=result_dir / "agent", stream=True,
        )
    except OSError as exc:
        result["status"] = "error"
        result["error"] = f"agent_command_not_started: {exc}"
        result["usage"]["wall_time_sec"] = time.monotonic() - started
        (result_dir / "run.json").write_text(json.dumps(result, indent=2) + "\n")
        return result
    result["artifacts"]["agent"] = asdict(agent)
    result["artifacts"]["trajectory"] = str(trajectory_file)
    if base_commit:
        result["code"] = _code_change_stats(workspace, base_commit, baseline_untracked)
    result["usage"].update(_trajectory_usage(trajectory_file))
    result["usage"]["agent_wall_time_sec"] = agent.wall_time_sec
    result["usage"]["wall_time_sec"] = time.monotonic() - started
    # Held-out evaluation: future tests become visible only after the agent
    # has finished, and both suites always run so a future-test failure still
    # records the regression numbers the TODO metrics require.
    first_error: str | None = None
    if agent.returncode not in (None, 0):
        first_error = f"agent_command_failed(exit={agent.returncode})"
    if artifact_root is not None:
        result["artifacts"]["injected_tests"] = inject_tests(artifact_root, workspace)
    evaluation_started = time.monotonic()
    test_env = {key: str(value) for key, value in task.get("test_env", {}).items()}
    test_env.setdefault("DOWNSTREAM_WORKSPACE", str(workspace))
    all_green = True
    suite_timed_out = False
    for suite in ("future", "regression"):
        test_argv = shlex.split(_format_test_command(task, suite, workspace, result_dir))
        junit = result_dir / suite / "junit.xml"
        if any(item.endswith("pytest") or item == "pytest" for item in test_argv):
            test_argv.append(f"--junitxml={junit}")
        print(f"\n[{suite}] {' '.join(test_argv)}", flush=True)
        test = run_command(
            test_argv, workspace, timeout_sec, env=test_env or None,
            artifact_dir=result_dir / suite, stream=True,
        )
        result["artifacts"][suite] = asdict(test)
        suite_green = not _command_failed(test)
        if junit.exists():
            report = _junit(junit)
            result["tests"][f"{suite}_passed"] = report["passed"]
            result["tests"][f"{suite}_total"] = report["total"]
            suite_green = suite_green and report["passed"] == report["total"]
        if not suite_green:
            all_green = False
            if test.timed_out:
                suite_timed_out = True
            if first_error is None:
                first_error = f"{suite}_tests_failed"
    result["usage"]["evaluation_wall_time_sec"] = time.monotonic() - evaluation_started
    # Status follows the TODO stop conditions: timeout outranks everything;
    # green tests complete the run even if the agent command itself exited
    # non-zero; otherwise agent errors outrank test failures.
    if agent.timed_out or suite_timed_out:
        result["status"] = "timeout"
    elif all_green:
        result["status"] = "passed"
    elif agent.returncode not in (None, 0):
        result["status"] = "error"
    else:
        result["status"] = "failed"
    result["error"] = first_error
    result["usage"]["wall_time_sec"] = time.monotonic() - started
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
