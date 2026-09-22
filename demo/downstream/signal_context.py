"""Build task-local Signal guidance without modifying a downstream C0 tree.

This is deliberately a side path for ``demo.downstream``. The existing Signal
pipeline remains unchanged; this module reuses its discovery and common
extraction machinery, then renders the result as read-only agent context.
The pack's common.py is reference material for writing new code, not a
refactor to apply, so validation checks that it compiles and is grounded in
the member sources (no invented imports or names) instead of running the
pipeline's member-rewrite equivalence procedure.
"""

from __future__ import annotations

import ast
import copy
import re
import subprocess
from pathlib import Path
from typing import Any

from demo.baselines.json_utils import require_library_schema
from demo.baselines.llm_client import DeepSeekClient
from demo.baselines.run_baseline_b import call_and_parse_stage, sanitize_subcluster
from demo.baselines.runner_utils import (
    build_user_prompt,
    compile_result,
    load_json_if_exists,
    read_text,
    status,
    write_json,
)
from demo.discovery import fusion
from demo.discovery.discover_cluster import discover_cluster
from demo.discovery.semantic_signal import DEFAULT_MODEL

from .core import _ensure_repo, _tree_files, _verify_history, load_task


ROOT = Path(__file__).resolve().parents[2]
PROMPT_DIR = ROOT / "demo" / "downstream" / "prompts"
CONTEXT_SYSTEM = PROMPT_DIR / "signal_context_common_system.txt"
CONTEXT_USER = PROMPT_DIR / "signal_context_common_user_template.txt"
_UNSAFE_STEM_RE = re.compile(r"[^A-Za-z0-9_.-]")


def _safe_stem(raw: str, index: int, used: set[str]) -> str:
    """Filesystem-safe, collision-free id for an LLM-supplied subcluster id."""
    stem = _UNSAFE_STEM_RE.sub("_", str(raw)).strip("._") or f"sub_{index}"
    unique, suffix = stem, 2
    while unique in used:
        unique = f"{stem}_{suffix}"
        suffix += 1
    used.add(unique)
    return unique


def _skill_markdown(patterns: list[dict[str, Any]]) -> str:
    lines = [
        "# Implementation guidance from comparable services",
        "",
        "Guidance distilled by Signal from similar existing implementations in",
        "this repository. Treat it as background reading: confirm APIs against",
        "this repository's own code before relying on it.",
        "",
        "## Patterns",
        "",
    ]
    for item in patterns:
        lines.extend([
            f"### `{item['subcluster_id']}`",
            f"- Members: {', '.join(item['member_paths'])}",
            f"- Guidance: `{item['guidance_path']}`",
            f"- Common component: `{item['common_path']}`",
            f"- Source evidence: `{item['snippets_path']}`",
            "",
        ])
    return "\n".join(lines) + "\n"


def _validate_context_common(payload: dict[str, Any]) -> None:
    require_library_schema(payload)
    guidance = payload.get("agent_guidance")
    if not isinstance(guidance, dict):
        raise ValueError("context common output must include agent_guidance")
    for field in ("summary",):
        if not isinstance(guidance.get(field), str):
            raise ValueError(f"agent_guidance.{field} must be a string")
    for field in ("invariants", "variation_points", "pitfalls", "recommended_use"):
        values = guidance.get(field)
        if not isinstance(values, list) or not all(isinstance(item, str) for item in values):
            raise ValueError(f"agent_guidance.{field} must be a list of strings")


def _load_source_units(source: str, unit_ids: list[str]) -> list[dict[str, Any]]:
    from demo.discovery.units import parse_units

    _, units, error = parse_units(source)
    if error:
        return [{"unit_id": "module", "start": 1, "end": source.count("\n") + 1, "content": source}]
    by_id = {unit.unit_id: unit for unit in units}
    lines = source.splitlines(keepends=True)
    selected = []
    for unit_id in unit_ids or ["module"]:
        unit = by_id.get(unit_id)
        if unit is None:
            continue
        selected.append({
            "unit_id": unit_id,
            "start": unit.start,
            "end": unit.end,
            "content": "".join(lines[unit.start - 1:unit.end]),
        })
    return selected or [{"unit_id": "module", "start": 1, "end": len(lines), "content": source}]


def _write_snippets(
    context_root: Path,
    subcluster: dict[str, Any],
    file_by_id: dict[str, dict[str, Any]],
    source_by_id: dict[str, str],
) -> Path:
    path = context_root / "snippets" / f"{subcluster['cluster_id']}.md"
    lines = [f"# Source evidence: {subcluster['cluster_id']}", ""]
    for file_id in subcluster["members"]:
        entry = file_by_id[file_id]
        lines.extend([f"## `{entry['rel_path']}`", ""])
        unit_ids = subcluster.get("units", {}).get(file_id, [])
        for unit in _load_source_units(source_by_id[file_id], unit_ids):
            lines.extend([
                f"### `{unit['unit_id']}` (lines {unit['start']}-{unit['end']})",
                "",
                "```python",
                unit["content"].rstrip("\n"),
                "```",
                "",
            ])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _call_context_common(
    client: DeepSeekClient,
    cluster: dict[str, Any],
    dataset_dir: Path,
    out_dir: Path,
    subcluster: dict[str, Any],
) -> dict[str, Any]:
    payload = {
        "cluster_id": cluster["cluster_id"],
        "subcluster": subcluster,
        "files": [
            {
                "file_id": item["file_id"],
                "rel_path": item["rel_path"],
                "source_code": (dataset_dir / "clusters" / cluster["cluster_id"] / "original" / item["file_id"]).read_text(encoding="utf-8"),
            }
            for item in cluster["files"]
            if item["file_id"] in set(subcluster["members"])
        ],
    }
    system_prompt = read_text(CONTEXT_SYSTEM)
    user_prompt = build_user_prompt(CONTEXT_USER, payload)
    return call_and_parse_stage(
        client,
        system_prompt,
        user_prompt,
        out_dir,
        "downstream_signal_context",
        cluster["cluster_id"],
        {"system": str(CONTEXT_SYSTEM), "user_template": str(CONTEXT_USER)},
        f"{subcluster['cluster_id']}_common",
        '{"library":{"path":"common.py","content":"..."},"rationale":"...","agent_guidance":{}}',
        _validate_context_common,
    )


def _member_evidence(source: str) -> tuple[set[str], set[str]]:
    """Return (absolute modules imported, identifiers used) for one member."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        # Unparseable member: fall back to raw tokens so grounding never
        # fails merely because one member could not be parsed.
        return set(), set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", source))
    modules: set[str] = set()
    identifiers: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)
                identifiers.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.level == 0:
                modules.add(node.module)
            identifiers.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Name):
            identifiers.add(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            identifiers.add(node.name)
    return modules, identifiers


def _validate_common_reference(
    common_payload: dict[str, Any],
    subcluster: dict[str, Any],
    source_by_id: dict[str, str],
    out_dir: Path,
) -> dict[str, Any]:
    """Check that the reference common.py compiles and stays grounded.

    The pack's common.py is distilled reference material, so the failure
    mode that would actually mislead the downstream agent is hallucinated
    APIs, and the member-rewrite equivalence procedure of the Signal
    pipeline does not catch that (py_compile resolves no names).  Instead:
    every module common.py imports must be imported by a member source, and
    every name it imports from a module must appear in the member sources;
    a common.py that defines no top-level API is a failed distillation.
    Whether the pattern ultimately helps the agent is measured by the
    downstream A/B run, not here.
    """
    content = common_payload["library"]["content"]
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "common.py").write_text(content, encoding="utf-8")
    compile_info = compile_result(out_dir)
    if not compile_info["ok"]:
        return {"subcluster_id": subcluster["cluster_id"], "status": "compile_failed",
                "reason": "common.py does not compile", "compile": compile_info}

    tree = ast.parse(content)
    defined: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defined.append(node.name)
        elif isinstance(node, ast.Assign):
            defined.extend(target.id for target in node.targets if isinstance(target, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            defined.append(node.target.id)
    if not defined:
        return {"subcluster_id": subcluster["cluster_id"], "status": "empty",
                "reason": "common.py defines no top-level API", "compile": compile_info,
                "defined_names": defined}

    member_modules: set[str] = set()
    member_identifiers: set[str] = set()
    for file_id in subcluster["members"]:
        modules, identifiers = _member_evidence(source_by_id[file_id])
        member_modules.update(modules)
        member_identifiers.update(identifiers)

    imported_modules: list[str] = []
    imported_names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module == "__future__":
                continue
            if node.module and node.level == 0:
                imported_modules.append(node.module)
            imported_names.extend(alias.name for alias in node.names)
    invented_modules = sorted(set(imported_modules) - member_modules)
    invented_names = sorted(set(imported_names) - member_identifiers)
    if invented_modules or invented_names:
        detail = ", ".join(invented_modules + invented_names)
        return {"subcluster_id": subcluster["cluster_id"], "status": "ungrounded",
                "reason": f"imports not present in member sources: {detail}",
                "compile": compile_info, "defined_names": defined,
                "invented_imports": invented_modules + invented_names}
    return {"subcluster_id": subcluster["cluster_id"], "status": "ok",
            "compile": compile_info, "defined_names": defined}


def materialize_signal_context(
    task_id: str,
    repo_cache: Path,
    context_root: Path,
    manifest: Path,
    *,
    api_timeout_sec: float | None = None,
    max_output_tokens: int | None = None,
    embedding_model: str = DEFAULT_MODEL,
    skip_semantic: bool = False,
    gate_workers: int = 1,
) -> Path:
    """Run Signal on C0 history paths and write an agent-readable context pack.

    All work happens in ``<context_root>.partial`` and is renamed into place
    only on success, so a failed attempt leaves resumable state instead of a
    half-written pack: discovery reuses its own JSON artifacts, each
    subcluster reuses its cached common payload, and validated subclusters
    reuse their validation result. Failed subclusters are retried on resume,
    matching ``run_signal``. Delete the ``.partial`` directory to start over.
    Each pattern's common.py is screened by ``_validate_common_reference``
    (compile plus import/name grounding against the member sources); the
    screen removes hallucinated reference code, it does not certify that the
    pattern helps the downstream agent.
    """
    task = load_task(task_id, manifest)
    context_root = context_root.resolve()
    if context_root.exists():
        raise FileExistsError(f"refusing to overwrite signal context: {context_root}")

    repo = _ensure_repo(task["repository"]["url"], repo_cache / task["repository"]["slug"])
    c0, future_end = task["history"]["c0"], task["history"]["future_end"]
    _verify_history(repo, c0, future_end)

    # Collect sources before creating any directory, so config errors leave
    # no residue to clean up.
    file_entries: list[dict[str, Any]] = []
    source_by_id: dict[str, str] = {}
    raw_by_id: dict[str, bytes] = {}
    seen_paths: set[str] = set()
    for requested in task.get("signal_history_paths", []):
        for source_path in _tree_files(repo, c0, requested):
            if source_path in seen_paths:
                continue
            seen_paths.add(source_path)
            file_id = f"file_{len(file_entries):03d}.py"
            data = subprocess.run(
                ["git", "show", f"{c0}:{source_path}"],
                cwd=repo,
                capture_output=True,
                check=True,
            ).stdout
            raw_by_id[file_id] = data
            source_by_id[file_id] = data.decode("utf-8")
            file_entries.append({
                "file_id": file_id,
                "name": Path(source_path).name,
                "rel_path": source_path,
                "subsystem": task["evolution_pattern"],
                "lines": len(source_by_id[file_id].splitlines()),
            })

    if len(file_entries) < 2:
        raise ValueError(f"Signal context requires at least two C0 source files for {task_id}")

    cluster = {
        "cluster_id": "0",
        "name": f"downstream_{task_id}_c0_history",
        "record_count": len(file_entries),
        "source_lines": sum(item["lines"] for item in file_entries),
        "files": file_entries,
    }

    work_root = context_root.parent / f"{context_root.name}.partial"
    if work_root.exists():
        status(f"signal context: resuming from {work_root}")
    else:
        work_root.mkdir(parents=True)
    dataset_dir = work_root / "_signal_dataset"
    results_dir = work_root / "_signal_results"
    source_dir = dataset_dir / "clusters" / "0" / "original"
    source_dir.mkdir(parents=True, exist_ok=True)
    for entry in file_entries:
        (source_dir / entry["file_id"]).write_bytes(raw_by_id[entry["file_id"]])
    write_json(dataset_dir / "cluster_manifest.json", {
        "schema": "downstream-signal-input-v1",
        "task_id": task_id,
        "source_commit": c0,
        "clusters": [cluster],
    })

    cfg = copy.deepcopy(fusion.DEFAULT_CFG)
    cfg["unit_preference"] = "coarse"
    gate_cfg = {
        "api_timeout_sec": api_timeout_sec,
        "max_output_tokens": max_output_tokens,
        "gate_workers": max(1, gate_workers),
    }
    signal_status = discover_cluster(
        cluster,
        dataset_dir,
        results_dir,
        cfg,
        skip_semantic=skip_semantic,
        skip_gate=False,
        embedding_model=embedding_model,
        force_semantic=False,
        resume=True,
        gate_cfg=gate_cfg,
        rerun_gate=False,
    )
    discovery = load_json_if_exists(results_dir / "signal" / "0" / "discovery.json")
    if discovery is None:
        raise RuntimeError(f"Signal discovery did not produce discovery.json: {signal_status}")

    (work_root / "patterns").mkdir(parents=True, exist_ok=True)
    (work_root / "snippets").mkdir(parents=True, exist_ok=True)
    patterns: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    file_by_id = {item["file_id"]: item for item in file_entries}
    used_ids: set[str] = set()
    subclusters = []
    for index, item in enumerate(discovery.get("clusters", [])):
        sanitized = sanitize_subcluster(item, set(file_by_id), index)
        sanitized["cluster_id"] = _safe_stem(sanitized["cluster_id"], index, used_ids)
        subclusters.append(sanitized)

    client: DeepSeekClient | None = None
    for subcluster in [item for item in subclusters if len(item["members"]) >= 2]:
        sub_id = subcluster["cluster_id"]
        status(f"signal context: subcluster {sub_id} start")
        out_dir = results_dir / "signal" / "0" / sub_id
        common_payload = load_json_if_exists(out_dir / "context_common_payload.json")
        if common_payload is not None:
            _validate_context_common(common_payload)
        validation = load_json_if_exists(out_dir / "validation.json")
        if validation is not None and validation.get("status") != "ok":
            validation = None  # failed subclusters are retried on resume
        if common_payload is None:
            if client is None:
                client = DeepSeekClient(timeout_sec=api_timeout_sec, max_tokens=max_output_tokens)
            common_payload = _call_context_common(client, cluster, dataset_dir, out_dir, subcluster)
            write_json(out_dir / "context_common_payload.json", common_payload)
        if validation is None:
            validation = _validate_common_reference(common_payload, subcluster, source_by_id, out_dir)
            write_json(out_dir / "validation.json", validation)
        if validation.get("status") != "ok":
            dropped.append({"subcluster_id": sub_id, "reason": validation.get("status"),
                            "detail": validation.get("reason")})
            status(f"signal context: subcluster {sub_id} dropped ({validation.get('reason') or validation.get('status')})")
            continue

        common_path = work_root / "patterns" / f"{sub_id}_common.py"
        common_path.write_text(common_payload["library"]["content"], encoding="utf-8")
        snippets_path = _write_snippets(work_root, subcluster, file_by_id, source_by_id)
        guidance_path = work_root / "patterns" / f"{sub_id}.md"
        guidance = common_payload["agent_guidance"]
        guidance_path.write_text(
            "# Implementation pattern\n\n"
            "## Extraction rationale\n\n"
            f"{common_payload.get('rationale', '')}\n\n"
            f"## Summary\n\n{guidance['summary']}\n\n"
            "## Invariants\n\n" + "\n".join(f"- {item}" for item in guidance["invariants"]) + "\n\n"
            "## Variation points\n\n" + "\n".join(f"- {item}" for item in guidance["variation_points"]) + "\n\n"
            "## Pitfalls\n\n" + "\n".join(f"- {item}" for item in guidance["pitfalls"]) + "\n\n"
            "## Recommended use\n\n" + "\n".join(f"- {item}" for item in guidance["recommended_use"]) + "\n",
            encoding="utf-8",
        )
        patterns.append({
            "subcluster_id": sub_id,
            "members": subcluster["members"],
            "member_paths": [file_by_id[m]["rel_path"] for m in subcluster["members"]],
            "common_path": str(common_path.relative_to(work_root)),
            "guidance_path": str(guidance_path.relative_to(work_root)),
            "snippets_path": str(snippets_path.relative_to(work_root)),
            "rationale": common_payload.get("rationale", ""),
            "validation": "ok",
        })
        status(f"signal context: subcluster {sub_id} validated")

    if not patterns:
        detail = "; ".join(f"{item['subcluster_id']}: {item['reason']}" for item in dropped)
        raise ValueError(
            f"Signal context for {task_id} produced no validated pattern"
            + (f" ({detail})" if detail else " (no subcluster with at least two members passed the gate)")
        )

    (work_root / "SKILL.md").write_text(_skill_markdown(patterns), encoding="utf-8")

    write_json(work_root / "manifest.json", {
        "schema": "downstream-signal-context-v1",
        "task_id": task_id,
        "source_commit": c0,
        "future_commit": future_end,
        "source_paths": [item["rel_path"] for item in file_entries],
        "patterns": patterns,
        "dropped": dropped,
        "signal_status": signal_status,
        "note": "Audit record; manifest.json is not copied into the agent workspace.",
    })
    work_root.rename(context_root)
    return context_root
