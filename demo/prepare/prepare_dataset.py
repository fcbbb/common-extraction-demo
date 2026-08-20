from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE = ROOT / "Librarian" / "data" / "LLM_description_clusters" / "new"
DEFAULT_OUT = ROOT / "demo" / "datasets" / "codecontest"
TEST_FIELDS = ("public_tests", "private_tests", "generated_tests")
PROMPT_EXCLUDED_FIELDS = (
    "name",
    "description",
    "short_description",
    "difficulty",
    "public_tests",
    "private_tests",
    "generated_tests",
)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON in {path}:{line_no}: {exc}") from exc
    return rows


def count_tests(record: dict[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for field in TEST_FIELDS:
        tests = record.get(field) or {}
        inputs = tests.get("input") or []
        outputs = tests.get("output") or []
        counts[field.replace("_tests", "")] = min(len(inputs), len(outputs))
    counts["total"] = sum(counts.values())
    return counts


def prepare_dataset(source_dir: Path, out_dir: Path) -> dict[str, Any]:
    cluster_files = sorted(source_dir.glob("*.jsonl"), key=lambda p: int(p.stem))
    if not cluster_files:
        raise FileNotFoundError(f"No jsonl cluster files found under {source_dir}")

    clusters_dir = out_dir / "clusters"
    clusters_dir.mkdir(parents=True, exist_ok=True)

    manifest: dict[str, Any] = {
        "source_dir": str(source_dir.relative_to(ROOT) if source_dir.is_relative_to(ROOT) else source_dir),
        "prompt_input_fields": ["file_id", "source_code"],
        "prompt_excluded_fields": list(PROMPT_EXCLUDED_FIELDS),
        "clusters": [],
    }

    inventory_lines = [
        "# Data Inventory",
        "",
        f"Source: `{manifest['source_dir']}`",
        "",
        "Prompt-visible fields: `file_id`, `source_code` only.",
        "",
        "Prompt-excluded fields: " + ", ".join(f"`{name}`" for name in PROMPT_EXCLUDED_FIELDS) + ".",
        "",
        "| cluster | records | fields | public tests | private tests | generated tests | total tests |",
        "|---:|---:|---|---:|---:|---:|---:|",
    ]

    for cluster_path in cluster_files:
        cluster_id = cluster_path.stem
        rows = read_jsonl(cluster_path)
        original_dir = clusters_dir / cluster_id / "original"
        original_dir.mkdir(parents=True, exist_ok=True)

        files = []
        total_counts = {"public": 0, "private": 0, "generated": 0, "total": 0}
        fields_seen: set[str] = set()
        for row_index, row in enumerate(rows):
            fields_seen.update(row.keys())
            file_id = f"file_{row_index:03d}.py"
            solution = row.get("solution")
            if not isinstance(solution, str):
                raise ValueError(f"{cluster_path}:{row_index} has no string solution field")
            (original_dir / file_id).write_text(solution, encoding="utf-8")

            test_counts = count_tests(row)
            for key, value in test_counts.items():
                total_counts[key] += value
            files.append(
                {
                    "file_id": file_id,
                    "row_index": row_index,
                    "name": row.get("name"),
                    "difficulty": row.get("difficulty"),
                    "tests": {
                        "public": row.get("public_tests") or {"input": [], "output": []},
                        "private": row.get("private_tests") or {"input": [], "output": []},
                        "generated": row.get("generated_tests") or {"input": [], "output": []},
                    },
                    "test_counts": test_counts,
                }
            )

        rel_source = str(cluster_path.relative_to(ROOT) if cluster_path.is_relative_to(ROOT) else cluster_path)
        manifest["clusters"].append(
            {
                "cluster_id": cluster_id,
                "source_jsonl": rel_source,
                "record_count": len(rows),
                "fields": sorted(fields_seen),
                "files": files,
                "test_counts": total_counts,
            }
        )
        inventory_lines.append(
            f"| {cluster_id} | {len(rows)} | {', '.join(f'`{x}`' for x in sorted(fields_seen))} | "
            f"{total_counts['public']} | {total_counts['private']} | {total_counts['generated']} | {total_counts['total']} |"
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "cluster_manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    (out_dir / "DATA_INVENTORY.md").write_text("\n".join(inventory_lines) + "\n", encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Unpack CodeContests JSONL clusters for baseline prompts.")
    parser.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    manifest = prepare_dataset(args.source_dir, args.out_dir)
    print(f"Wrote {len(manifest['clusters'])} clusters to {args.out_dir}")


if __name__ == "__main__":
    main()
