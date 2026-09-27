"""Check whether distilled common modules can be placed into C0 repositories.

The checker exports the tracked C0 snapshot from each postfix signal workspace
into a temporary directory. It never edits the reusable workspace, whose
working tree may contain the run's generated implementation.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SHARED_ROOT = ROOT / ".cache/downstream-signal-context-shared"
DEFAULT_WORKSPACE_ROOT = ROOT / ".cache/downstream-workspaces"
DEFAULT_RUNS_ROOT = ROOT / ".cache/downstream-runs-postfix"
DEFAULT_TASKS_MANIFEST = ROOT / "demo/downstream/tasks.json"
DEFAULT_REPORT = ROOT / "demo/reports/reuse-proof-stage0-20260926.json"


def _safe_repo_path(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"unsafe repository path: {value!r}")
    return path


def _latest_run_id(task_id: str, runs_root: Path) -> str:
    run_files = sorted((runs_root / task_id / "signal").glob("*/run.json"))
    if not run_files:
        raise FileNotFoundError(f"no postfix signal run found for {task_id}")
    return run_files[-1].parent.name


def _workspace_for(task_id: str, run_id: str, workspace_root: Path) -> Path:
    # The run output and prepared workspace use the same date/time prefix but
    # have independent collision-safe suffixes.
    prefix = "-".join(run_id.split("-")[:2])
    candidates = sorted(
        path
        for path in (workspace_root / task_id / "signal").glob(f"{prefix}*")
        if (path / "source/.git").is_dir()
    )
    if len(candidates) != 1:
        raise RuntimeError(
            f"expected one prepared workspace matching {prefix}, found "
            f"{[str(path) for path in candidates]}"
        )
    workspace = candidates[0]
    if not (workspace / ".venv/bin/python").is_file():
        raise FileNotFoundError(f"task virtualenv missing under {workspace}")
    return workspace


def _export_c0(source_workspace: Path, destination: Path) -> tuple[str, list[str]]:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=source_workspace,
        capture_output=True,
        text=True,
        check=True,
    )
    commit = result.stdout.strip()
    archive_path = destination.parent / "c0.tar"
    with archive_path.open("wb") as archive_file:
        subprocess.run(
            ["git", "archive", "--format=tar", "HEAD"],
            cwd=source_workspace,
            stdout=archive_file,
            check=True,
        )
    destination.mkdir(parents=True)
    with tarfile.open(archive_path, "r:") as archive:
        try:
            archive.extractall(destination, filter="data")
        except TypeError:  # Python versions without the PEP 706 backport.
            archive.extractall(destination)
    archive_path.unlink()

    # PEP 621/setuptools-scm may generate a version module while preparing
    # the cached environment. Reuse only that declared generated file, which
    # is absent from git archive but required for imports from the C0 package.
    generated_files: list[str] = []
    pyproject = destination / "pyproject.toml"
    if pyproject.is_file():
        config = pyproject.read_text(encoding="utf-8")
        for match in re.finditer(r"(?m)^\s*(?:[\w.-]+\.)?version-file\s*=\s*['\"]([^'\"]+)['\"]", config):
            relative = _safe_repo_path(match.group(1))
            target = destination / relative
            prepared_file = source_workspace / relative
            if not target.exists() and prepared_file.is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(prepared_file, target)
                generated_files.append(relative.as_posix())
    return commit, generated_files


def _import_root_and_name(source: Path, member_path: PurePosixPath, module_name: str) -> tuple[Path, str]:
    parts = list(member_path.parts)
    if parts and parts[0] == "src":
        import_root = source / "src"
        parts = parts[1:]
    else:
        import_root = source
    package_parts = parts[:-1]
    if not package_parts:
        raise ValueError(f"member has no package directory: {member_path}")
    return import_root, ".".join([*package_parts, Path(module_name).stem])


def _import_error_category(stderr: str) -> str:
    text = stderr.lower()
    if "attempted relative import" in text or "beyond top-level package" in text:
        return "relative_import_level_mismatch"
    if "syntaxerror" in text:
        return "component_syntax_error"
    if "nameerror" in text or "attributeerror" in text:
        return "component_runtime_error"
    if "modulenotfounderror" in text or "importerror" in text:
        return "absolute_import_module_unresolvable"
    return "module_import_failed"


def _test_candidates(source: Path, member_paths: list[PurePosixPath]) -> list[Path]:
    files = [
        path
        for path in source.rglob("*.py")
        if (path.name.startswith("test") or path.name.endswith("_test.py"))
        and not any(part in {".git", ".venv", "build", "dist"} for part in path.parts)
    ]
    selected: set[Path] = set()
    structural_dirs = {
        "backends", "drivers", "imputation", "loader", "models",
        "serializers", "services", "sources", "transmission", "writers",
    }

    def tokens(value: str) -> set[str]:
        return {token for token in re.split(r"[^a-z0-9]+", value.lower()) if token}

    for member in member_paths:
        stem = member.stem.lstrip("_").lower()
        parent_parts = [part.lower().lstrip("_") for part in member.parent.parts]
        parent_tokens = set().union(*(tokens(part) for part in parent_parts))
        contextual_tokens = tokens(member.parent.name)
        repository_parts = [
            part for part in parent_parts if part not in {"src", "lib", "python"}
        ]
        has_structural_parent = (
            len(repository_parts) > 1
            or bool(parent_tokens.intersection(structural_dirs))
        )
        for test_file in files:
            if stem not in test_file.name.lower():
                continue
            relative = test_file.relative_to(source)
            test_parent_tokens = set().union(*(tokens(part) for part in relative.parent.parts))
            test_name_tokens = tokens(test_file.stem)
            # Prefer tests in the same package/backend area. For flat test
            # layouts, exact module-name matches remain usable unless the
            # source sits under a structural area such as a backend collection.
            if contextual_tokens.intersection(test_parent_tokens | test_name_tokens):
                selected.add(test_file)
            elif not has_structural_parent:
                selected.add(test_file)
    return sorted(selected)


def _task_test_env(tasks_manifest: Path, task_id: str) -> dict[str, str]:
    if not tasks_manifest.is_file():
        return {}
    payload = json.loads(tasks_manifest.read_text(encoding="utf-8"))
    for task in payload.get("tasks", []):
        if task.get("task_id") == task_id:
            return {str(key): str(value) for key, value in task.get("test_env", {}).items()}
    return {}


def _run_tests(
    *,
    python: Path,
    source: Path,
    import_root: Path,
    test_files: list[Path],
    extra_env: dict[str, str],
) -> dict[str, Any]:
    if not test_files:
        return {"status": "not_found", "paths": []}
    env = os.environ.copy()
    env.update(extra_env)
    old_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = os.pathsep.join(
        [str(import_root), *([old_pythonpath] if old_pythonpath else [])]
    )
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    paths = [path.relative_to(source).as_posix() for path in test_files]
    result = subprocess.run(
        [str(python), "-m", "pytest", "-q", *paths],
        cwd=source,
        env=env,
        capture_output=True,
        text=True,
        timeout=900,
        check=False,
    )
    return {
        "status": "passed" if result.returncode == 0 else "failed",
        "paths": paths,
        "returncode": result.returncode,
        "stdout_tail": result.stdout[-3000:],
        "stderr_tail": result.stderr[-3000:],
    }


def _check_pattern(
    *,
    task_id: str,
    pattern: dict[str, Any],
    pack_root: Path,
    workspace: Path,
    run_id: str,
    run_tests: bool,
    tasks_manifest: Path,
) -> dict[str, Any]:
    common_rel = _safe_repo_path(pattern["common_path"])
    member_paths = [_safe_repo_path(path) for path in pattern["member_paths"]]
    member = member_paths[0]
    module_file = pack_root / common_rel
    if not module_file.is_file():
        raise FileNotFoundError(f"component file missing: {module_file}")

    with tempfile.TemporaryDirectory(prefix="reuse-proof-stage0-") as temp_dir:
        temp_root = Path(temp_dir)
        source = temp_root / "source"
        snapshot_commit, snapshot_support_files = _export_c0(workspace / "source", source)
        target_dir = source / member.parent
        if not target_dir.is_dir():
            return {
                "task_id": task_id,
                "subcluster_id": pattern["subcluster_id"],
                "common_path": common_rel.as_posix(),
                "member_paths": [path.as_posix() for path in member_paths],
                "member_directory": member.parent.as_posix(),
                "workspace_run_id": run_id,
                "snapshot_commit": snapshot_commit,
                "snapshot_support_files": snapshot_support_files,
                "placeable": False,
                "failure_category": "member_directory_missing",
            }
        target_file = target_dir / common_rel.name
        if target_file.exists():
            return {
                "task_id": task_id,
                "subcluster_id": pattern["subcluster_id"],
                "common_path": common_rel.as_posix(),
                "member_paths": [path.as_posix() for path in member_paths],
                "member_directory": member.parent.as_posix(),
                "workspace_run_id": run_id,
                "snapshot_commit": snapshot_commit,
                "snapshot_support_files": snapshot_support_files,
                "placeable": False,
                "failure_category": "component_filename_already_exists",
                "detail": target_file.relative_to(source).as_posix(),
            }
        target_file.write_bytes(module_file.read_bytes())
        import_root, import_name = _import_root_and_name(source, member, common_rel.name)
        env = os.environ.copy()
        old_pythonpath = env.get("PYTHONPATH")
        env["PYTHONPATH"] = os.pathsep.join(
            [str(import_root), *([old_pythonpath] if old_pythonpath else [])]
        )
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        import_result = subprocess.run(
            [str(workspace / ".venv/bin/python"), "-c", f"import {import_name}"],
            cwd=source,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        import_record: dict[str, Any] = {
            "status": "passed" if import_result.returncode == 0 else "failed",
            "module": import_name,
            "returncode": import_result.returncode,
        }
        if import_result.returncode != 0:
            import_record["error"] = (import_result.stderr or import_result.stdout)[-3000:]
            import_record["failure_category"] = _import_error_category(
                import_result.stderr or import_result.stdout
            )

        tests_record: dict[str, Any] = {"status": "not_run", "paths": []}
        if run_tests and import_result.returncode == 0:
            tests = _test_candidates(source, member_paths)
            tests_record = _run_tests(
                python=workspace / ".venv/bin/python",
                source=source,
                import_root=import_root,
                test_files=tests,
                extra_env=_task_test_env(tasks_manifest, task_id),
            )

        failure_category = None
        if import_result.returncode != 0:
            failure_category = import_record["failure_category"]
        elif tests_record["status"] == "failed":
            failure_category = "existing_test_failure"
        return {
            "task_id": task_id,
            "subcluster_id": pattern["subcluster_id"],
            "common_path": common_rel.as_posix(),
            "member_paths": [path.as_posix() for path in member_paths],
            "member_directory": member.parent.as_posix(),
            "workspace_run_id": run_id,
            "snapshot_commit": snapshot_commit,
            "snapshot_support_files": snapshot_support_files,
            "import": import_record,
            "existing_tests": tests_record,
            "placeable": import_result.returncode == 0 and tests_record["status"] != "failed",
            "failure_category": failure_category,
        }


def check(args: argparse.Namespace) -> dict[str, Any]:
    selected_tasks = set(args.tasks or [])
    results: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    manifests = sorted(args.shared_root.glob("*/manifest.json"))
    for manifest_path in manifests:
        task_id = manifest_path.parent.name
        if selected_tasks and task_id not in selected_tasks:
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            run_dir = args.runs_root / task_id / "signal"
            if run_dir.is_dir() and any(run_dir.glob("*/run.json")):
                run_id = _latest_run_id(task_id, args.runs_root)
                workspace = _workspace_for(task_id, run_id, args.workspace_root)
            else:
                candidates = sorted(
                    path for path in (args.workspace_root / task_id / "signal").glob("*")
                    if (path / "source/.git").is_dir() and (path / ".venv/bin/python").is_file()
                )
                if not candidates:
                    raise FileNotFoundError(f"no prepared C0 workspace with a task venv for {task_id}")
                workspace = candidates[-1]
                run_id = workspace.name
            for pattern in manifest.get("patterns", []):
                try:
                    results.append(
                        _check_pattern(
                            task_id=task_id,
                            pattern=pattern,
                            pack_root=manifest_path.parent,
                            workspace=workspace,
                            run_id=run_id,
                            run_tests=args.run_tests,
                            tasks_manifest=args.tasks_manifest,
                        )
                    )
                except Exception as exc:  # Keep a per-pattern audit trail.
                    errors.append(
                        {
                            "task_id": task_id,
                            "subcluster_id": str(pattern.get("subcluster_id", "unknown")),
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                    )
        except Exception as exc:
            errors.append({"task_id": task_id, "error": f"{type(exc).__name__}: {exc}"})

    pass_count = sum(result.get("placeable", False) for result in results)
    task_counts: dict[str, dict[str, int]] = {}
    for result in results:
        counts = task_counts.setdefault(result["task_id"], {"placeable": 0, "patterns": 0})
        counts["patterns"] += 1
        counts["placeable"] += int(result.get("placeable", False))
    return {
        "schema": "reuse-proof-stage0-placeability-v1",
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "test_mode": "member_related_tests" if args.run_tests else "import_only",
        "summary": {
            "tasks": len(task_counts),
            "patterns": len(results) + len(errors),
            "placeable_patterns": pass_count,
            "failed_patterns": len(results) - pass_count + len(errors),
            "errors": len(errors),
            "go_no_go": (
                "go"
                if task_counts
                and not errors
                and all(counts["placeable"] > 0 for counts in task_counts.values())
                else "no_go"
            ),
        },
        "task_summary": task_counts,
        "results": results,
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shared-root", type=Path, default=DEFAULT_SHARED_ROOT)
    parser.add_argument("--workspace-root", type=Path, default=DEFAULT_WORKSPACE_ROOT)
    parser.add_argument("--runs-root", type=Path, default=DEFAULT_RUNS_ROOT)
    parser.add_argument("--tasks-manifest", type=Path, default=DEFAULT_TASKS_MANIFEST)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--tasks", nargs="*", help="optional task ID filter")
    parser.add_argument("--run-tests", action="store_true", help="run tests whose names match member modules")
    args = parser.parse_args()

    payload = check(args)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(payload["summary"], ensure_ascii=False))
    print(f"report: {args.report}")
    return 0 if not payload["errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
