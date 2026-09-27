"""Qualification, deployment, and AST receipts for reusable Signal components."""

from __future__ import annotations

import ast
import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, Mapping


QUALIFICATION_SCHEMA = "downstream-signal-component-qualification-v1"
RECEIPT_SCHEMA = "downstream-signal-reuse-receipts-v1"
QUALIFIER_VERSION = 2


def _safe_relative(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError(f"unsafe relative path: {value!r}")
    return path


def _defined_api(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.append(node.name)
        elif isinstance(node, ast.Assign):
            names.extend(target.id for target in node.targets if isinstance(target, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.append(node.target.id)
    return names


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_name = temporary.name
            temporary.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        Path(temporary_name).replace(path)
    finally:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)


def _load_pack(pack_root: Path, task: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
    manifest_path = pack_root / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"Signal pack manifest missing: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema") != "downstream-signal-context-v1":
        raise ValueError(f"unsupported Signal pack schema: {manifest.get('schema')!r}")
    if manifest.get("task_id") != task["task_id"]:
        raise ValueError("Signal pack task_id does not match the current task")
    source_commit = task["history"]["c0"]
    if manifest.get("source_commit") != source_commit:
        raise ValueError("Signal pack was distilled from a different C0 commit")
    patterns = manifest.get("patterns")
    if not isinstance(patterns, list) or not patterns:
        raise ValueError("Signal pack contains no component patterns")

    identities = []
    for pattern in patterns:
        common_rel = _safe_relative(pattern["common_path"])
        common_path = pack_root.joinpath(*common_rel.parts)
        if not common_path.is_file():
            raise FileNotFoundError(f"Signal component missing: {common_path}")
        if pattern.get("validation") != "ok":
            raise ValueError(
                f"pattern {pattern.get('subcluster_id')} did not pass extraction validation"
            )
        api = pattern.get("api")
        if not isinstance(api, list) or not api:
            api = _defined_api(common_path)
            pattern["api"] = api
        identities.append({
            "subcluster_id": pattern["subcluster_id"],
            "common_path": common_rel.as_posix(),
            "common_sha256": _sha256(common_path),
            "member_paths": list(pattern.get("member_paths", [])),
            "api": list(pattern.get("api", [])),
        })
    identity_payload = {
        "schema": QUALIFICATION_SCHEMA,
        "qualifier_version": QUALIFIER_VERSION,
        "task_id": task["task_id"],
        "source_commit": source_commit,
        "patterns": identities,
    }
    identity = hashlib.sha256(
        json.dumps(identity_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return manifest, identity


def qualify_signal_pack(
    *,
    task: Mapping[str, Any],
    pack_root: Path,
    workspace: Path,
    tasks_manifest: Path,
) -> dict[str, Any]:
    """Run producer-side import/placeability checks once per exact pack identity."""
    pack_root = pack_root.resolve()
    workspace = workspace.resolve()
    pack_manifest, identity = _load_pack(pack_root, task)
    qualification_path = pack_root / "qualification.json"
    if qualification_path.is_file():
        try:
            cached = json.loads(qualification_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            cached = None
        if (
            isinstance(cached, dict)
            and cached.get("schema") == QUALIFICATION_SCHEMA
            and cached.get("identity") == identity
            and cached.get("source_commit") == task["history"]["c0"]
        ):
            return cached

    from .check_placeability import _check_pattern

    results: list[dict[str, Any]] = []
    for pattern in pack_manifest["patterns"]:
        common_rel = _safe_relative(pattern["common_path"])
        component_path = pack_root.joinpath(*common_rel.parts)
        try:
            result = _check_pattern(
                task_id=str(task["task_id"]),
                pattern=pattern,
                pack_root=pack_root,
                workspace=workspace,
                run_id=workspace.name,
                run_tests=True,
                tasks_manifest=tasks_manifest,
            )
        except Exception as exc:  # Preserve a per-pattern rejection record.
            result = {
                "task_id": task["task_id"],
                "subcluster_id": pattern["subcluster_id"],
                "common_path": common_rel.as_posix(),
                "placeable": False,
                "failure_category": "qualification_error",
                "detail": f"{type(exc).__name__}: {exc}",
            }
        result["source_commit"] = task["history"]["c0"]
        result["component_sha256"] = _sha256(component_path)
        result["qualification_level"] = (
            "import_and_member_tests"
            if result.get("existing_tests", {}).get("status") == "passed"
            else "import_only_no_matching_tests"
            if result.get("import", {}).get("status") == "passed"
            and result.get("existing_tests", {}).get("status") == "not_found"
            else "failed"
        )
        results.append(result)

    passing = sum(bool(item.get("placeable")) for item in results)
    from datetime import datetime, timezone
    report = {
        "schema": QUALIFICATION_SCHEMA,
        "qualifier_version": QUALIFIER_VERSION,
        "identity": identity,
        "task_id": task["task_id"],
        "source_commit": task["history"]["c0"],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "scope": (
            "grounding_and_compile_from_pack_manifest; import into the member "
            "package; run matching existing member tests when discoverable"
        ),
        "summary": {
            "patterns": len(results),
            "placeable": passing,
            "rejected": len(results) - passing,
        },
        "results": results,
    }
    _write_json_atomic(qualification_path, report)
    return report


def _copy_selected_agent_context(
    *,
    pack_root: Path,
    workspace: Path,
    patterns: list[dict[str, Any]],
    deployment_by_id: dict[str, dict[str, Any]],
) -> Path:
    destination = workspace / ".downstream" / "signal-context"
    if destination.exists():
        raise FileExistsError(f"refusing to overwrite signal context: {destination}")
    (destination / "patterns").mkdir(parents=True)
    (destination / "snippets").mkdir(parents=True)

    lines = [
        "# Validated reusable Signal components",
        "",
        "The listed common modules passed producer-side grounding, import, and",
        "placeability checks. Matching existing member tests were run when found.",
        "Adapt the documented variation points to the requested task contract.",
        "All paths are relative to the repository root.",
        "",
        "## Components",
        "",
    ]
    for pattern in patterns:
        sub_id = str(pattern["subcluster_id"])
        common_rel = _safe_relative(pattern["common_path"])
        guidance_rel = _safe_relative(pattern["guidance_path"])
        snippets_rel = _safe_relative(pattern["snippets_path"])
        for rel in (common_rel, guidance_rel, snippets_rel):
            source = pack_root.joinpath(*rel.parts)
            if not source.is_file():
                raise FileNotFoundError(f"Signal pack file missing: {source}")
        shutil.copy2(
            pack_root.joinpath(*common_rel.parts),
            destination / "patterns" / common_rel.name,
        )
        shutil.copy2(
            pack_root.joinpath(*guidance_rel.parts),
            destination / "patterns" / guidance_rel.name,
        )
        shutil.copy2(
            pack_root.joinpath(*snippets_rel.parts),
            destination / "snippets" / snippets_rel.name,
        )
        deployment = deployment_by_id[sub_id]
        lines.extend([
            f"### {sub_id}",
            f"- Members: {', '.join(pattern.get('member_paths', []))}",
            f"- Installed module: {deployment['module']}",
            f"- Installed file: {deployment['destination']}",
            f"- Common API: {', '.join(pattern.get('api', []))}",
            f"- Adaptation guidance: .downstream/signal-context/patterns/{guidance_rel.name}",
            f"- Source evidence: .downstream/signal-context/snippets/{snippets_rel.name}",
            "",
        ])
    index = destination / "SKILL.md"
    index.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return index


def install_qualified_components(
    *,
    task: Mapping[str, Any],
    pack_root: Path,
    workspace: Path,
    tasks_manifest: Path,
) -> dict[str, Any]:
    """Qualify a pack, install passing components, and expose only those patterns."""
    pack_root = pack_root.resolve()
    workspace = workspace.resolve()
    pack_manifest, identity = _load_pack(pack_root, task)
    qualification = qualify_signal_pack(
        task=task,
        pack_root=pack_root,
        workspace=workspace.parent,
        tasks_manifest=tasks_manifest,
    )
    if qualification.get("identity") != identity:
        raise ValueError("component qualification identity does not match this pack")

    qualification_by_id = {
        str(item.get("subcluster_id")): item
        for item in qualification.get("results", [])
    }
    accepted: list[dict[str, Any]] = []
    for pattern in pack_manifest["patterns"]:
        sub_id = str(pattern["subcluster_id"])
        record = qualification_by_id.get(sub_id)
        common_rel = _safe_relative(pattern["common_path"])
        source = pack_root.joinpath(*common_rel.parts)
        if (
            record is None
            or record.get("common_path") != common_rel.as_posix()
            or record.get("component_sha256") != _sha256(source)
            or record.get("source_commit") != task["history"]["c0"]
        ):
            raise ValueError(f"missing or stale qualification for pattern {sub_id}")
        if record.get("placeable"):
            accepted.append(pattern)

    if not accepted:
        raise RuntimeError(
            f"no Signal components passed qualification for {task['task_id']}"
        )

    context_destination = workspace / ".downstream" / "signal-context"
    if context_destination.exists():
        raise FileExistsError(f"refusing to overwrite signal context: {context_destination}")
    deployed: list[dict[str, Any]] = []
    created_targets: list[Path] = []
    deployment_by_id: dict[str, dict[str, Any]] = {}
    try:
        for pattern in accepted:
            sub_id = str(pattern["subcluster_id"])
            record = qualification_by_id[sub_id]
            member_paths = [_safe_relative(path) for path in pattern.get("member_paths", [])]
            if not member_paths:
                raise ValueError(f"pattern {sub_id} has no member paths")
            expected_directory = member_paths[0].parent.as_posix()
            member_directory = record.get("member_directory")
            if member_directory != expected_directory:
                raise ValueError(
                    f"qualification target changed for pattern {sub_id}: "
                    f"{member_directory!r} != {expected_directory!r}"
                )
            target_dir = workspace.joinpath(*_safe_relative(member_directory).parts)
            target_dir_resolved = target_dir.resolve()
            if not target_dir_resolved.is_relative_to(workspace):
                raise ValueError(f"component target escapes workspace: {member_directory}")
            if not target_dir.is_dir():
                raise FileNotFoundError(f"component target directory missing: {target_dir}")
            common_rel = _safe_relative(pattern["common_path"])
            source = pack_root.joinpath(*common_rel.parts)
            target = target_dir / common_rel.name
            if target.exists():
                raise FileExistsError(f"component target already exists: {target}")
            module_name = record.get("import", {}).get("module")
            if not isinstance(module_name, str) or not module_name:
                raise ValueError(f"qualification has no import path for pattern {sub_id}")
            created_targets.append(target)
            shutil.copy2(source, target)
            deployment = {
                "subcluster_id": sub_id,
                "module": module_name,
                "source": common_rel.as_posix(),
                "destination": target.relative_to(workspace).as_posix(),
                "sha256": _sha256(target),
                "qualification_level": record.get("qualification_level"),
                "member_tests": record.get("existing_tests", {}),
            }
            deployed.append(deployment)
            deployment_by_id[sub_id] = deployment

        skill_file = _copy_selected_agent_context(
            pack_root=pack_root,
            workspace=workspace,
            patterns=accepted,
            deployment_by_id=deployment_by_id,
        )
    except Exception:
        for target in created_targets:
            target.unlink(missing_ok=True)
        shutil.rmtree(context_destination, ignore_errors=True)
        raise

    return {
        "context_root": str(skill_file.parent),
        "context_file": str(skill_file),
        "qualification_file": str(pack_root / "qualification.json"),
        "qualification_identity": identity,
        "components": deployed,
    }


def _dotted(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _dotted(node.value)
        return f"{prefix}.{node.attr}" if prefix else None
    return None


def _relative_import_module(node: ast.ImportFrom, source_path: str) -> str | None:
    if node.level == 0:
        return node.module or ""
    package_parts = list(PurePosixPath(source_path).parent.parts)
    if package_parts and package_parts[0] == "src":
        package_parts = package_parts[1:]
    climb = node.level - 1
    if climb > len(package_parts):
        return None
    if climb:
        package_parts = package_parts[:-climb]
    if node.module:
        package_parts.extend(node.module.split("."))
    return ".".join(package_parts)


def _scan_source_for_component(
    tree: ast.AST,
    module_name: str,
    source_path: str,
) -> dict[str, list[dict[str, Any]]]:
    imports: list[dict[str, Any]] = []
    inheritance: list[dict[str, Any]] = []
    calls: list[dict[str, Any]] = []
    symbol_uses: list[dict[str, Any]] = []
    module_aliases: dict[str, str] = {}
    symbol_aliases: dict[str, tuple[str, str]] = {}

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported = alias.name
                if imported == module_name:
                    imports.append({"line": node.lineno, "statement": imported})
                    if alias.asname:
                        module_aliases[alias.asname] = module_name
                elif module_name.startswith(imported + "."):
                    imports.append({"line": node.lineno, "statement": imported})
        elif isinstance(node, ast.ImportFrom):
            imported_module = _relative_import_module(node, source_path)
            if imported_module is None:
                continue
            for alias in node.names:
                combined = f"{imported_module}.{alias.name}" if imported_module else alias.name
                statement = (
                    f"from {'.' * node.level}{node.module or ''} import {alias.name}"
                )
                if imported_module == module_name:
                    imports.append({"line": node.lineno, "statement": statement})
                    symbol_aliases[alias.asname or alias.name] = (module_name, alias.name)
                elif combined == module_name:
                    imports.append({"line": node.lineno, "statement": statement})
                    module_aliases[alias.asname or alias.name] = module_name

    def resolve(node: ast.AST) -> tuple[str, str] | None:
        dotted = _dotted(node)
        if dotted:
            for alias, target in module_aliases.items():
                if dotted == alias:
                    return target, ""
                if dotted.startswith(alias + "."):
                    return target, dotted[len(alias) + 1:]
            if dotted == module_name:
                return module_name, ""
            if dotted.startswith(module_name + "."):
                return module_name, dotted[len(module_name) + 1:]
        if isinstance(node, ast.Name) and node.id in symbol_aliases:
            return symbol_aliases[node.id]
        return None

    call_functions = {id(node.func) for node in ast.walk(tree) if isinstance(node, ast.Call)}
    base_nodes = {
        id(base)
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef)
        for base in node.bases
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            resolved = resolve(node.func)
            if resolved:
                calls.append({
                    "line": node.lineno,
                    "symbol": resolved[1] or "<module>",
                    "source": source_path,
                })
        elif isinstance(node, ast.ClassDef):
            for base in node.bases:
                resolved = resolve(base)
                if resolved:
                    inheritance.append({
                        "line": node.lineno,
                        "class": node.name,
                        "base": resolved[1] or "<module>",
                        "source": source_path,
                    })

    for node in ast.walk(tree):
        if not isinstance(node, (ast.Name, ast.Attribute)):
            continue
        if id(node) in call_functions or id(node) in base_nodes:
            continue
        resolved = resolve(node)
        if resolved and resolved[1]:
            symbol_uses.append({
                "line": node.lineno,
                "symbol": resolved[1],
                "source": source_path,
            })

    return {
        "imports": imports,
        "inheritance": inheritance,
        "calls": calls,
        "symbol_uses": symbol_uses,
    }


def _changed_python_files(
    workspace: Path,
    base_commit: str,
    baseline_untracked: set[str],
    excluded_paths: set[str],
) -> list[Path]:
    changed: set[str] = set()
    diff = subprocess.run(
        ["git", "diff", "--name-only", base_commit, "--"],
        cwd=workspace,
        capture_output=True,
        text=True,
        check=False,
    )
    if diff.returncode == 0:
        changed.update(line for line in diff.stdout.splitlines() if line)
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard", "-z"],
        cwd=workspace,
        capture_output=True,
        text=True,
        check=False,
    )
    if untracked.returncode == 0:
        changed.update(
            path for path in untracked.stdout.split("\0")
            if path and path not in baseline_untracked
        )
    return sorted(
        workspace / relative
        for relative in changed
        if relative.endswith(".py")
        and relative not in excluded_paths
        and (workspace / relative).is_file()
    )


def collect_reuse_receipts(
    *,
    workspace: Path,
    base_commit: str,
    baseline_untracked: set[str],
    components: list[dict[str, Any]],
) -> dict[str, Any]:
    """Record component imports and uses from agent-authored Python changes."""
    workspace = workspace.resolve()
    excluded = {item["destination"] for item in components}
    changed_sources = _changed_python_files(
        workspace, base_commit, baseline_untracked, excluded
    )
    records = []
    for component in components:
        module_name = component["module"]
        target = workspace / component["destination"]
        record: dict[str, Any] = {
            "subcluster_id": component["subcluster_id"],
            "module": module_name,
            "module_file": component["destination"],
            "injected_sha256": component["sha256"],
            "modified_after_injection": False,
            "missing_after_agent": not target.is_file(),
            "imports": [],
            "inheritance": [],
            "calls": [],
            "symbol_uses": [],
            "parse_errors": [],
        }
        if target.is_file():
            record["modified_after_injection"] = _sha256(target) != component["sha256"]
        for source in changed_sources:
            relative = source.relative_to(workspace).as_posix()
            try:
                tree = ast.parse(source.read_text(encoding="utf-8"), filename=relative)
            except (OSError, UnicodeError, SyntaxError) as exc:
                record["parse_errors"].append({
                    "source": relative,
                    "error": f"{type(exc).__name__}: {exc}",
                })
                continue
            evidence = _scan_source_for_component(tree, module_name, relative)
            for key in ("imports", "inheritance", "calls", "symbol_uses"):
                record[key].extend(evidence[key])

        meaningful_use = bool(record["inheritance"] or record["calls"] or record["symbol_uses"])
        record["receipt_status"] = (
            "used" if meaningful_use else "import_only" if record["imports"] else "not_used"
        )
        record["receipt_present"] = bool(record["imports"])
        records.append(record)

    return {
        "schema": RECEIPT_SCHEMA,
        "components": records,
        "summary": {
            "components": len(records),
            "with_receipt": sum(bool(item["receipt_present"]) for item in records),
            "actually_used": sum(item["receipt_status"] == "used" for item in records),
            "import_only": sum(item["receipt_status"] == "import_only" for item in records),
        },
    }
