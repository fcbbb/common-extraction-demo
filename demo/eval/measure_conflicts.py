from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
from typing import Any

from demo.eval.measure_api_coverage import calls_in_tree, common_apis, parse_python


def load_mapping(result_dir: Path) -> dict[str, set[str]]:
    call_log_path = result_dir / "call_log.json"
    if not call_log_path.exists():
        return {}
    payload = json.loads(call_log_path.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and "members" in payload:
        members = payload["members"]
    else:
        members = payload
    raw_members = {}
    raw_output_path = result_dir / "raw_output.json"
    if raw_output_path.exists():
        raw_payload = json.loads(raw_output_path.read_text(encoding="utf-8"))
        raw_members = raw_payload.get("members") or {}
    mapping: dict[str, set[str]] = {}
    for file_id, member in (members or {}).items():
        helpers: set[str] = set()
        mapping_items = member.get("edit_mapping") or member.get("call_mapping") or []
        if not mapping_items:
            raw_member = raw_members.get(file_id) or {}
            mapping_items = raw_member.get("edit_mapping") or raw_member.get("call_mapping") or []
        for item in mapping_items:
            helper = item.get("helper")
            if isinstance(helper, str) and helper:
                helpers.add(helper)
        mapping[file_id] = helpers
    return mapping


def actual_calls(result_dir: Path, apis: set[str]) -> dict[str, set[str]]:
    calls: dict[str, set[str]] = {}
    for path in sorted((result_dir / "refactored").glob("file_*.py")):
        module = parse_python(path)
        calls[path.name] = (calls_in_tree(module) & apis) if module is not None else set()
    return calls


def measure(result_dir: Path) -> dict[str, Any]:
    apis = common_apis(result_dir / "common.py")
    claimed = load_mapping(result_dir)
    actual = actual_calls(result_dir, apis)
    files = sorted(set(claimed) | set(actual))

    conflicts = []
    mapping_items = 0
    actual_items = 0
    for file_id in files:
        claimed_helpers = claimed.get(file_id, set())
        actual_helpers = actual.get(file_id, set())
        mapping_items += len(claimed_helpers)
        actual_items += len(actual_helpers)
        for helper in sorted(claimed_helpers - apis):
            conflicts.append({"file_id": file_id, "type": "claimed_missing_api", "helper": helper})
        for helper in sorted((claimed_helpers & apis) - actual_helpers):
            conflicts.append({"file_id": file_id, "type": "claimed_but_not_called", "helper": helper})
        for helper in sorted(actual_helpers - claimed_helpers):
            conflicts.append({"file_id": file_id, "type": "called_but_not_mapped", "helper": helper})

    denominator = max(mapping_items, actual_items, 1)
    return {
        "mapping_items": mapping_items,
        "actual_helper_calls": actual_items,
        "conflict_items": len(conflicts),
        "conflict_rate": len(conflicts) / denominator,
        "conflicts": conflicts,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Measure LLM call_mapping conflicts.")
    parser.add_argument("--result-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    payload = json.dumps(measure(args.result_dir), indent=2, ensure_ascii=False)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(payload + "\n", encoding="utf-8")
    else:
        print(payload)


if __name__ == "__main__":
    main()
