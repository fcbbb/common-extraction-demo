"""Compare Signal discovery candidates with the independent gold oracle."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def score(gold: set[str], candidate: set[str]) -> tuple[float, float, float]:
    overlap = len(gold & candidate)
    recall = overlap / len(gold) if gold else 0.0
    precision = overlap / len(candidate) if candidate else 0.0
    f1 = 2 * recall * precision / (recall + precision) if recall + precision else 0.0
    return recall, precision, f1


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset-dir", type=Path, default=Path("demo/datasets/real_components"))
    parser.add_argument("--results-dir", type=Path, default=Path("demo/results/real_components"))
    parser.add_argument("--threshold", type=float, default=0.67)
    args = parser.parse_args()

    oracle = json.loads((args.dataset_dir / "gold_oracle.json").read_text(encoding="utf-8"))
    rows = []
    for gold in oracle["components"]:
        signal_cluster = gold.get("cluster_id", gold["project"])
        path = args.results_dir / "signal" / signal_cluster / "discovery_candidates.json"
        if not path.exists():
            rows.append({"id": gold["id"], "status": "missing_discovery"})
            continue
        doc = json.loads(path.read_text(encoding="utf-8"))
        best = {"recall": 0.0, "precision": 0.0, "f1": 0.0, "members": []}
        for candidate in doc.get("candidates", []):
            members = set(candidate.get("members", []))
            recall, precision, f1 = score(set(gold["members"]), members)
            if f1 > best["f1"]:
                best = {"recall": recall, "precision": precision, "f1": f1, "members": sorted(members)}
        rows.append({"id": gold["id"], "status": "recalled" if best["recall"] >= args.threshold else "missed", **best})

    recalled = sum(row.get("status") == "recalled" for row in rows)
    result = {"gold_components": len(rows), "recalled": recalled, "recall": recalled / len(rows) if rows else 0.0, "rows": rows}
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
