"""Compare full-pool executable-token compression for Baseline A and Signal.

Unlike per-subcluster reports, this charges untouched Signal files at their
original size.  It therefore prevents Signal from winning merely by refusing
to process difficult files.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .measure_tokens import count_tokens, strip_non_code


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def source_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def token_count(paths: list[Path]) -> int:
    return count_tokens(strip_non_code("\n\n".join(source_text(p) for p in paths)))[0]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dataset-dir",
        type=Path,
        default=Path("demo/datasets/libcloud_loadbalancer_real"),
    )
    parser.add_argument(
        "--results-dir",
        type=Path,
        default=Path("demo/results/libcloud_loadbalancer_real"),
    )
    parser.add_argument("--out", type=Path, help="Optional JSON output path.")
    args = parser.parse_args()

    manifest = load(args.dataset_dir / "cluster_manifest.json")
    entries = manifest["clusters"][0]["files"]
    original_dir = args.dataset_dir / "clusters/0/original"
    original = {entry["file_id"]: original_dir / entry["file_id"] for entry in entries}
    before = token_count(list(original.values()))

    a_dir = args.results_dir / "baseline_a/0"
    a_after = token_count([a_dir / "common.py", *sorted((a_dir / "refactored").glob("file_*.py"))])

    signal_dir = args.results_dir / "signal/0"
    discovery = load(signal_dir / "discovery.json")
    covered: set[str] = set()
    signal_paths: list[Path] = []
    subclusters = []
    for index, cluster in enumerate(discovery.get("clusters", [])):
        members = [item for item in cluster.get("members", []) if item in original]
        if len(members) < 2:
            continue
        if covered.intersection(members):
            raise SystemExit(f"overlapping Signal subclusters at sub_{index}: {members}")
        covered.update(members)
        sub_id = str(cluster.get("cluster_id", f"sub_{index}"))
        sub_dir = signal_dir / sub_id
        signal_paths.extend([sub_dir / "common.py", *(sub_dir / "refactored" / item for item in members)])
        subclusters.append({"id": sub_id, "members": members})
    signal_paths.extend(original[file_id] for file_id in sorted(set(original) - covered))
    signal_after = token_count(signal_paths)

    def row(name: str, after: int) -> dict[str, object]:
        return {
            "method": name,
            "tokens_before": before,
            "tokens_after": after,
            "compression": round(1 - after / before, 4) if before else None,
        }

    payload = {
        "dataset": manifest.get("repo"),
        "file_count": len(original),
        "signal_covered_files": len(covered),
        "signal_subclusters": subclusters,
        "rows": [row("baseline_a", a_after), row("signal", signal_after)],
        "decision": "signal_not_worse" if signal_after <= a_after else "baseline_a_better",
        "test_gate": "Require all changed subclusters to compile and pass the same original-passing pytest cases; unchanged noise is retained byte-for-byte.",
    }
    rendered = json.dumps(payload, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)


if __name__ == "__main__":
    main()
