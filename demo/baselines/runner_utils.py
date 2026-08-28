from __future__ import annotations

import ast
import json
import py_compile
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from demo.baselines.json_utils import extract_json_object, require_extraction_schema
from demo.baselines.llm_client import DeepSeekClient, LLMResponse
from demo.eval.measure_api_coverage import measure as measure_api_coverage
from demo.eval.measure_conflicts import measure as measure_conflicts
from demo.eval.measure_mdl import measure_mdl
from demo.eval.measure_tokens import measure as measure_tokens
from demo.eval.run_tests import test_refactored_cluster
from demo.eval.run_tests_pytest import test_refactored_cluster as test_refactored_cluster_pytest


ROOT = Path(__file__).resolve().parents[2]
PROMPT_DIR = ROOT / "demo" / "baselines" / "prompts"


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def load_json_if_exists(path: Path) -> Any | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def status(message: str) -> None:
    stamp = time.strftime("%H:%M:%S")
    print(f"[{stamp}] {message}", file=sys.stderr, flush=True)


def load_manifest(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def selected_clusters(manifest: dict[str, Any], cluster_ids: list[str] | None, limit: int | None) -> list[dict[str, Any]]:
    clusters = manifest["clusters"]
    if cluster_ids:
        wanted = set(cluster_ids)
        clusters = [cluster for cluster in clusters if cluster["cluster_id"] in wanted]
    if limit is not None:
        clusters = clusters[:limit]
    return clusters


def prompt_files_for_cluster(dataset_dir: Path, cluster: dict[str, Any], file_ids: list[str] | None = None) -> list[dict[str, str]]:
    cluster_id = cluster["cluster_id"]
    allowed = set(file_ids) if file_ids is not None else None
    files = []
    for item in cluster["files"]:
        file_id = item["file_id"]
        if allowed is not None and file_id not in allowed:
            continue
        source = (dataset_dir / "clusters" / cluster_id / "original" / file_id).read_text(encoding="utf-8")
        files.append({"file_id": file_id, "source_code": source})
    return files


def build_user_prompt(template_path: Path, payload: dict[str, Any]) -> str:
    return read_text(template_path).replace("{payload}", json.dumps(payload, indent=2, ensure_ascii=False))


def save_call_log(path: Path, response: LLMResponse, baseline: str, cluster_id: str, prompt_paths: dict[str, str]) -> None:
    write_json(
        path,
        {
            "provider": "deepseek",
            "model": response.model,
            "temperature": 0,
            "cluster_id": cluster_id,
            "baseline": baseline,
            "prompt_path": prompt_paths,
            "latency_sec": response.latency_sec,
            "usage": response.usage,
        },
    )


def call_and_parse_extraction(
    client: DeepSeekClient,
    system_prompt: str,
    user_prompt: str,
    out_dir: Path,
    baseline: str,
    cluster_id: str,
    prompt_paths: dict[str, str],
    repair_context: str | None = None,
) -> dict[str, Any]:
    prompt = user_prompt if repair_context is None else user_prompt + "\n\nRepair context:\n" + repair_context
    prompt_chars = len(system_prompt) + len(prompt)
    approx_tokens = prompt_chars // 4
    status(
        f"{baseline} cluster {cluster_id}: prompt size {prompt_chars} chars "
        f"(roughly {approx_tokens} tokens)"
    )
    errors = []
    for attempt in range(2):
        final_user_prompt = prompt
        if attempt == 1:
            final_user_prompt = (
                prompt
                + "\n\nYour previous response could not be parsed or did not match the required schema. "
                + "Return one complete valid JSON object only, with library.content, members.*.diff, "
                + "and members.*.edit_mapping. Do not return members.*.new_content."
                + f"\nParser error: {errors[-1]}"
            )
        status(f"{baseline} cluster {cluster_id}: calling DeepSeek API attempt {attempt + 1}/2")
        response = client.chat(system_prompt, final_user_prompt)
        status(f"{baseline} cluster {cluster_id}: API response received")
        out_dir.mkdir(parents=True, exist_ok=True)
        suffix = "" if attempt == 0 else f".retry{attempt}"
        (out_dir / f"raw_response{suffix}.txt").write_text(response.content, encoding="utf-8")
        write_json(out_dir / f"raw_api_response{suffix}.json", response.raw)
        save_call_log(out_dir / f"call_log_meta{suffix}.json", response, baseline, cluster_id, prompt_paths)
        try:
            payload = extract_json_object(response.content)
            require_extraction_schema(payload)
            return payload
        except Exception as exc:
            errors.append(repr(exc))
    raise ValueError("Extraction JSON parse/schema failed after one retry: " + " | ".join(errors))


_DIFF_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def _parse_unified_diff(diff: str) -> list[dict[str, Any]]:
    if not diff.strip():
        return []
    hunks: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in diff.splitlines():
        match = _DIFF_HUNK_RE.match(line)
        if match:
            if current is not None:
                hunks.append(current)
            current = {
                "old_start": int(match.group(1)),
                "old_count": int(match.group(2) or "1"),
                "new_start": int(match.group(3)),
                "new_count": int(match.group(4) or "1"),
                "lines": [],
            }
            continue
        if current is None:
            if line.startswith("--- ") or line.startswith("+++ ") or not line:
                continue
            raise ValueError(f"Unexpected unified diff line before first hunk: {line!r}")
        if line == r"\ No newline at end of file":
            continue
        if not line or line[0] not in " +-":
            raise ValueError(f"Invalid unified diff line: {line!r}")
        current["lines"].append((line[0], line[1:]))
    if current is not None:
        hunks.append(current)
    if not hunks:
        raise ValueError("Diff has no unified diff hunks")
    for index, hunk in enumerate(hunks, 1):
        old_count = sum(prefix in " -" for prefix, _ in hunk["lines"])
        new_count = sum(prefix in " +" for prefix, _ in hunk["lines"])
        if old_count != hunk["old_count"] or new_count != hunk["new_count"]:
            raise ValueError(
                f"Hunk {index} line count mismatch: expected -{hunk['old_count']} +{hunk['new_count']}, "
                f"got -{old_count} +{new_count}"
            )
        hunk["id"] = index
        hunk["has_deleted"] = any(prefix == "-" for prefix, _ in hunk["lines"])
        hunk["has_added"] = any(prefix == "+" for prefix, _ in hunk["lines"])
        hunk["added_lines"] = [text for prefix, text in hunk["lines"] if prefix == "+"]
    return hunks


def _apply_unified_diff(source: str, hunks: list[dict[str, Any]]) -> str:
    source_lines = source.splitlines()
    output: list[str] = []
    cursor = 0
    for hunk in hunks:
        start = 0 if hunk["old_start"] == 0 else hunk["old_start"] - 1
        if start < cursor or start > len(source_lines):
            raise ValueError(f"Hunk {hunk['id']} has an invalid or overlapping source range")
        output.extend(source_lines[cursor:start])
        position = start
        for prefix, text in hunk["lines"]:
            if prefix in " -":
                if position >= len(source_lines) or source_lines[position] != text:
                    raise ValueError(f"Hunk {hunk['id']} does not match the original source exactly")
                if prefix == " ":
                    output.append(source_lines[position])
                position += 1
            else:
                output.append(text)
        cursor = position
    output.extend(source_lines[cursor:])
    ending = "\n" if source.endswith("\n") else ""
    return "\n".join(output) + ending


def _common_api_names(content: str) -> set[str]:
    try:
        tree = ast.parse(content)
    except SyntaxError as exc:
        raise ValueError(f"common.py is not valid Python: {exc}") from exc
    return {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }


def materialize_surgical_extraction(
    payload: dict[str, Any],
    original_files: dict[str, str],
) -> dict[str, Any]:
    members = payload["members"]
    if set(members) != set(original_files):
        raise ValueError(
            f"Member file_ids mismatch: expected {sorted(original_files)}, got {sorted(members)}"
        )
    api_names = _common_api_names(payload["library"]["content"])
    materialized_members: dict[str, Any] = {}
    for file_id, source in original_files.items():
        member = members[file_id]
        hunks = _parse_unified_diff(member["diff"])
        mappings = member["edit_mapping"]
        by_id = {hunk["id"]: hunk for hunk in hunks}
        mapped_removed: set[int] = set()
        mapped_added: set[int] = set()
        for mapping in mappings:
            helper = mapping["helper"]
            if helper not in api_names:
                raise ValueError(f"{file_id} references helper not defined in common.py: {helper}")
            removed = set(mapping["removed_hunks"])
            added = set(mapping["added_hunks"])
            if not removed or not added:
                raise ValueError(f"{file_id} edit {mapping['edit_id']} must map deleted and added hunks")
            if not removed <= set(by_id) or not added <= set(by_id):
                raise ValueError(f"{file_id} edit {mapping['edit_id']} references an unknown hunk")
            helper_call = re.compile(rf"(?:\bcommon\s*\.\s*)?\b{re.escape(helper)}\s*\(")
            if not any(
                any(helper_call.search(line) for line in by_id[hunk_id]["added_lines"])
                for hunk_id in added
            ):
                raise ValueError(f"{file_id} edit {mapping['edit_id']} has no call to common.{helper}")
            if not all(by_id[hunk_id]["has_deleted"] for hunk_id in removed):
                raise ValueError(f"{file_id} edit {mapping['edit_id']} has no deleted implementation hunk")
            mapped_removed.update(removed)
            mapped_added.update(added)
        for hunk in hunks:
            added_lines = hunk["added_lines"]
            import_only = bool(added_lines) and all(
                line.strip().startswith("from common import ") or line.strip() == "import common"
                for line in added_lines
            )
            if hunk["has_deleted"] and hunk["id"] not in mapped_removed:
                raise ValueError(f"{file_id} deletion hunk {hunk['id']} is not mapped to a common call")
            if hunk["has_added"] and not import_only and hunk["id"] not in mapped_added:
                raise ValueError(f"{file_id} added-code hunk {hunk['id']} is not mapped to a common call")
            if import_only and any(
                not (line.strip().startswith("from common import ") or line.strip() == "import common")
                for line in added_lines
            ):
                raise ValueError(f"{file_id} has an invalid import-only hunk {hunk['id']}")
        generated = _apply_unified_diff(source, hunks)
        materialized_member = dict(member)
        materialized_member["new_content"] = generated
        materialized_members[file_id] = materialized_member
    materialized = dict(payload)
    materialized["members"] = materialized_members
    return materialized


def write_extraction_result(out_dir: Path, payload: dict[str, Any]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "common.py").write_text(payload["library"]["content"], encoding="utf-8")
    refactored_dir = out_dir / "refactored"
    shutil.rmtree(refactored_dir, ignore_errors=True)
    refactored_dir.mkdir(parents=True, exist_ok=True)
    for file_id, member in payload["members"].items():
        (refactored_dir / file_id).write_text(member["new_content"], encoding="utf-8")
    write_json(out_dir / "raw_output.json", payload)
    write_json(
        out_dir / "call_log.json",
        {
            "members": {
                file_id: {
                    "edit_mapping": member.get("edit_mapping", []),
                    "call_mapping": member.get("call_mapping", []),
                    "removed_duplicates": member.get("removed_duplicates", []),
                }
                for file_id, member in payload["members"].items()
            }
        },
    )


def compile_result(out_dir: Path) -> dict[str, Any]:
    paths = [out_dir / "common.py", *sorted((out_dir / "refactored").glob("file_*.py"))]
    results = []
    ok = True
    for path in paths:
        try:
            py_compile.compile(str(path), doraise=True)
            results.append({"path": str(path), "ok": True})
        except py_compile.PyCompileError as exc:
            ok = False
            results.append({"path": str(path), "ok": False, "error": str(exc)})
    result = {"ok": ok, "files": results}
    write_json(out_dir / "compile_result.json", result)
    return result


def run_metrics(
    cluster: dict[str, Any],
    dataset_dir: Path,
    out_dir: Path,
    timeout_sec: float,
    file_ids: list[str] | None = None,
    test_limit: int | None = None,
    compare_mode: str = "original",
    normalize: str = "whitespace",
    test_mode: str = "stdio",
    workers: int | None = None,
) -> dict[str, Any]:
    cluster_id = cluster["cluster_id"]
    metric_cluster = cluster
    if file_ids is not None:
        allowed = set(file_ids)
        metric_cluster = {**cluster, "files": [item for item in cluster["files"] if item["file_id"] in allowed]}
    original_dir = dataset_dir / "clusters" / cluster_id / "original"
    if test_mode == "pytest":
        tests = test_refactored_cluster_pytest(
            metric_cluster,
            dataset_dir,
            out_dir,
            timeout_sec,
            test_limit=test_limit,
            compare_mode=compare_mode,
            normalize=normalize,
            workers=workers,
        )
    elif test_mode == "stdio":
        tests = test_refactored_cluster(
            metric_cluster,
            dataset_dir,
            out_dir,
            timeout_sec,
            test_limit=test_limit,
            compare_mode=compare_mode,
            normalize=normalize,
            workers=workers,
        )
    else:
        raise ValueError(f"Unknown test_mode: {test_mode}")
    tokens = measure_tokens(original_dir, out_dir, file_ids=file_ids)
    api_coverage = measure_api_coverage(out_dir)
    conflicts = measure_conflicts(out_dir)
    mdl = measure_mdl(original_dir, out_dir, file_ids=file_ids)
    result = {
        "cluster_id": cluster_id,
        "metric_policy": {
            "test_limit_per_file": test_limit,
            "compare_mode": compare_mode,
            "normalization": normalize,
            "test_mode": test_mode,
            "note": "Default baseline policy: all tests, compare refactored output to original solution output with whitespace normalization.",
        },
        "tests": tests["summary"],
        "tokens": tokens,
        "api_coverage": api_coverage,
        "conflicts": conflicts,
        "mdl": mdl,
    }
    write_json(out_dir / "test_result.json", tests)
    write_json(out_dir / "token_metrics.json", tokens)
    write_json(out_dir / "api_coverage.json", api_coverage)
    write_json(out_dir / "conflict_metrics.json", conflicts)
    write_json(out_dir / "mdl_metrics.json", mdl)
    write_json(out_dir / "metrics.json", result)
    return result


def run_py_compile_command(out_dir: Path) -> dict[str, Any]:
    files = [str(out_dir / "common.py"), *[str(path) for path in sorted((out_dir / "refactored").glob("file_*.py"))]]
    proc = subprocess.run([sys.executable, "-m", "py_compile", *files], text=True, capture_output=True, check=False)
    return {"returncode": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}


def status_payload(status: str, reason: str, **extra: Any) -> dict[str, Any]:
    return {"status": status, "reason": reason, **extra}
