"""Convert the complex Scrapy slice into the baseline pipeline layout.

Reads demo/datasets/complex/manifest.json (subsystems + per-file metadata) and the
byte-identical source files under source/, then writes:

- demo/datasets/complex/cluster_manifest.json   (one cluster, 24 member files)
- demo/datasets/complex/clusters/0/original/file_*.py
- a generated section appended to demo/datasets/complex/DATA_INVENTORY.md

Test files are matched to source files by name (test_<subsystem>_<stem>.py) with a
few explicit overrides; unmatched generic test files become cluster-level
shared_test_files. The pytest runner consumes this manifest.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATASET_DIR = ROOT / "demo" / "datasets" / "complex"
PROMPT_EXCLUDED_FIELDS = (
    "name",
    "description",
    "short_description",
    "difficulty",
    "public_tests",
    "private_tests",
    "generated_tests",
)

# subsystem name -> test file prefix (strip trailing "s")
SUB_PREFIX = {"downloadermiddlewares": "downloadermiddleware", "spidermiddlewares": "spidermiddleware", "pipelines": "pipeline"}

# extra test files that exercise a file beyond the name-matched one
EXTRA_TEST_MAP = {
    "downloadermiddlewares/redirect.py": ["test_downloadermiddleware_redirect_metarefresh.py"],
    "spidermiddlewares/start.py": ["test_spidermiddleware_process_start.py"],
}

# test files that do not belong to a single member file
SHARED_TEST_FILES = [
    "test_downloadermiddleware.py",
    "test_spidermiddleware.py",
    "test_spidermiddleware_output_chain.py",
    "test_pipelines.py",
]


def read_manifest(dataset_dir: Path) -> dict[str, Any]:
    path = dataset_dir / "manifest.json"
    if not path.exists():
        raise FileNotFoundError(f"No manifest.json under {dataset_dir}")
    return json.loads(path.read_text(encoding="utf-8"))


def count_test_functions(path: Path) -> int:
    import ast

    try:
        module = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError:
        return 0
    count = 0
    for node in ast.walk(module):
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
            count += 1
    return count


def match_test_files(source_name: str, rel_path: str, test_dir: Path) -> list[str]:
    stem = Path(source_name).stem
    if stem == "scrapy_middleware":
        matched = ["test_middleware.py"]
    else:
        subsystem = rel_path.split("/")[0]
        prefix = SUB_PREFIX[subsystem]
        matched = [f"test_{prefix}_{stem}.py"]
    matched += EXTRA_TEST_MAP.get(rel_path, [])
    return [name for name in matched if (test_dir / name).exists()]


def prepare_dataset(dataset_dir: Path) -> dict[str, Any]:
    manifest = read_manifest(dataset_dir)
    source_dir = dataset_dir / "source"
    test_dir = dataset_dir / "tests"
    out_dir = dataset_dir
    clusters_dir = out_dir / "clusters"
    original_dir = clusters_dir / "0" / "original"
    original_dir.mkdir(parents=True, exist_ok=True)

    files: list[dict[str, Any]] = []
    shared = []
    test_files_seen: set[str] = set()
    total_lines = 0
    file_index = 0
    for subsystem in manifest["subsystems"]:
        sub_path = dataset_dir / subsystem["path"]
        sub_rel = subsystem["path"].split("/", 1)[1] if subsystem["path"].startswith("source/") else subsystem["path"]
        single_file = sub_path.is_file()
        for item in subsystem["files"]:
            if item["name"] == "__init__.py":
                continue
            rel_path = item["name"] if single_file else f"{sub_rel}/{item['name']}"
            source = sub_path if single_file else sub_path / item["name"]
            lines = len(source.read_text(encoding="utf-8").splitlines())
            total_lines += lines
            file_id = f"file_{file_index:03d}.py"
            file_index += 1
            matched = match_test_files(item["name"], rel_path, test_dir)
            if not matched and rel_path not in EXTRA_TEST_MAP:
                print(f"WARNING: no test file matched for {rel_path}")
            test_files_seen.update(matched)
            (original_dir / file_id).write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
            files.append(
                {
                    "file_id": file_id,
                    "name": item["name"],
                    "rel_path": rel_path,
                    "subsystem": subsystem["name"],
                    "lines": lines,
                    "tests": {"pytest": matched},
                }
            )

    all_test_files = sorted(path.name for path in test_dir.glob("test_*.py"))
    for name in SHARED_TEST_FILES:
        if (test_dir / name).exists():
            shared.append(name)
            test_files_seen.add(name)
    unmatched = sorted(set(all_test_files) - test_files_seen)
    if unmatched:
        raise ValueError(f"Test files not mapped to any member or shared list: {unmatched}")

    cluster: dict[str, Any] = {
        "cluster_id": "0",
        "name": "scrapy_middleware_pipeline_slice",
        "record_count": len(files),
        "source_lines": total_lines,
        "subsystems": manifest["subsystems"],
        "test_dir": str(test_dir.relative_to(ROOT)),
        "shared_test_files": shared,
        "files": files,
        "test_counts": {
            "member_mapped_test_files": len(test_files_seen - set(shared)),
            "pytest_test_functions": sum(count_test_functions(test_dir / name) for name in all_test_files),
        },
    }
    payload: dict[str, Any] = {
        "source_dir": str(source_dir.relative_to(ROOT)),
        "repo": manifest.get("repo"),
        "repo_url": manifest.get("repo_url"),
        "slice_commit": manifest.get("source_commit"),
        "slice_commit_date": manifest.get("source_commit_date"),
        "prompt_input_fields": ["file_id", "source_code"],
        "prompt_excluded_fields": list(PROMPT_EXCLUDED_FIELDS),
        "test_mode": "pytest",
        "clusters": [cluster],
    }
    (out_dir / "cluster_manifest.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    inventory = Path(out_dir / "DATA_INVENTORY.md")
    generated_marker = "\n## Prepared manifest (generated)"
    if inventory.exists() and generated_marker in inventory.read_text(encoding="utf-8"):
        text = inventory.read_text(encoding="utf-8")
        inventory.write_text(text[: text.index(generated_marker)].rstrip() + "\n", encoding="utf-8")
    section = [
        "",
        "## Prepared manifest (generated)",
        "",
        f"- Manifest: `cluster_manifest.json` — one cluster `0`, {len(files)} member files, {total_lines} source lines.",
        f"- Originals: `clusters/0/original/file_*.py` (byte-identical copies of `source/`).",
        f"- Test dir: `{cluster['test_dir']}` — includes upstream test infra (`tests/utils`, `tests/mockserver`, `tests/spiders.py`, `tests/sample_data`, `tests/keys`) fetched at commit {manifest.get('source_commit', '?')}.",
        f"- Shared test files (cluster level): {', '.join(f'`{x}`' for x in shared)}.",
        "",
        "| file_id | source | rel_path | pytest files |",
        "|---|---:|---|---|",
    ]
    section.extend(f"| {f['file_id']} | {f['name']} | `{f['rel_path']}` | {', '.join(f['tests']['pytest']) or '-'} |" for f in files)
    if inventory.exists():
        inventory.write_text(inventory.read_text(encoding="utf-8") + "\n" + "\n".join(section) + "\n", encoding="utf-8")
    else:
        inventory.write_text("\n".join(section) + "\n", encoding="utf-8")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare dataset_complex for baseline pipeline.")
    parser.add_argument("--dataset-dir", type=Path, default=DEFAULT_DATASET_DIR)
    args = parser.parse_args()
    payload = prepare_dataset(args.dataset_dir)
    cluster = payload["clusters"][0]
    print(f"Wrote {cluster['record_count']} member files in cluster 0 to {args.dataset_dir}")


if __name__ == "__main__":
    main()
