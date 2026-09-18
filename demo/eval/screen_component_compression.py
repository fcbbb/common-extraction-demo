"""Strict pre-screen for the real component benchmark.

This is intentionally optimistic: it assumes the gold component can be
extracted with a 12-token call-site wrapper and ignores imports/adapters.
Failing this screen means the dataset cannot plausibly reach the requested
10--25% compression after a real refactor.
"""

from __future__ import annotations

import argparse
import io
import json
import tokenize
from pathlib import Path


IGNORED = {
    tokenize.ENCODING,
    tokenize.NL,
    tokenize.NEWLINE,
    tokenize.INDENT,
    tokenize.DEDENT,
    tokenize.COMMENT,
    tokenize.ENDMARKER,
}


def executable_tokens(source: str) -> int:
    return sum(token.type not in IGNORED for token in tokenize.generate_tokens(io.StringIO(source).readline))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset-dir", type=Path, default=Path("demo/datasets/real_component_clusters"))
    parser.add_argument("--wrapper-tokens", type=int, default=12)
    args = parser.parse_args()
    dataset = args.dataset_dir
    manifest = json.loads((dataset / "cluster_manifest.json").read_text(encoding="utf-8"))
    oracle = json.loads((dataset / "gold_oracle.json").read_text(encoding="utf-8"))
    by_id = {item["cluster_id"]: item for item in oracle["components"]}
    rows = []
    for cluster in manifest["clusters"]:
        cid = cluster["cluster_id"]
        gold = by_id[cid]
        before = sum(
            executable_tokens((dataset / "clusters" / cid / "original" / entry["file_id"]).read_text(encoding="utf-8"))
            for entry in cluster["files"]
        )
        k = len(cluster["files"])
        # Keep all non-common code in place.  Only the repeated component is
        # deduplicated, so k copies become one shared definition plus one
        # small call-site wrapper per member file.
        optimistic_after = (
            before
            - k * gold["component_tokens"]
            + gold["component_tokens"]
            + k * args.wrapper_tokens
        )
        compression = (before - optimistic_after) / before if before else 0.0
        rows.append({
            "cluster_id": cid,
            "files": k,
            "tokens_before": before,
            "component_tokens": gold["component_tokens"],
            "optimistic_tokens_after": optimistic_after,
            "optimistic_compression": round(compression, 4),
            "status": "pass" if 0.10 <= compression <= 0.25 else "reject",
        })
    passed = sum(row["status"] == "pass" for row in rows)
    print(json.dumps({"clusters": len(rows), "passed": passed, "rows": rows}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
