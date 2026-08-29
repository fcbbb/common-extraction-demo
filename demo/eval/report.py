from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]


def load_json(path: Path) -> Any | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def fmt_pct(value: Any) -> str:
    if value is None:
        return "not_available"
    if isinstance(value, (int, float)):
        return f"{value * 100:.1f}%"
    return str(value)


def fmt_num(value: Any) -> str:
    if value is None:
        return "not_available"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def baseline_a_rows(results_dir: Path) -> list[dict[str, Any]]:
    rows = []
    for status_path in sorted((results_dir / "baseline_a").glob("*/status.json")):
        status = load_json(status_path) or {}
        metrics = status.get("metrics") or load_json(status_path.parent / "metrics.json") or {}
        rows.append(flat_row("baseline_a", status_path.parent.name, None, status, metrics))
    return rows


def baseline_b_rows(results_dir: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    main_rows = []
    discovery_rows = []
    for cluster_status_path in sorted((results_dir / "baseline_b").glob("*/status.json")):
        status = load_json(cluster_status_path) or {}
        cluster_id = cluster_status_path.parent.name
        discovery_rows.append(
            {
                "cluster": cluster_id,
                "discovered": status.get("discovered_subclusters"),
                "valid": status.get("valid_subclusters"),
                "noise": len(status.get("noise_files") or []),
                "notes": status.get("status", "missing"),
            }
        )
        for sub_status_path in sorted(cluster_status_path.parent.glob("*/status.json")):
            sub_status = load_json(sub_status_path) or {}
            metrics = sub_status.get("metrics") or load_json(sub_status_path.parent / "metrics.json") or {}
            main_rows.append(flat_row("baseline_b", cluster_id, sub_status_path.parent.name, sub_status, metrics))
    return main_rows, discovery_rows


def flat_row(baseline: str, cluster: str, subcluster: str | None, status: dict[str, Any], metrics: dict[str, Any]) -> dict[str, Any]:
    tests = metrics.get("tests") or {}
    tokens = metrics.get("tokens") or {}
    api = metrics.get("api_coverage") or {}
    conflicts = metrics.get("conflicts") or {}
    mdl = metrics.get("mdl") or {}
    return {
        "baseline": baseline,
        "cluster": cluster if subcluster is None else f"{cluster}/{subcluster}",
        "files": tests.get("files"),
        "pass_file": tests.get("file_pass_rate"),
        "pass_test": tests.get("test_pass_rate"),
        "mdl": mdl.get("mdl_compression"),
        "tokens": tokens.get("tokens_compression"),
        "file_api": api.get("file_api_coverage"),
        "api_usage": api.get("api_usage_coverage"),
        "conflict": conflicts.get("conflict_rate"),
        "status": status.get("status", "missing"),
    }


def table(headers: list[str], rows: list[list[str]]) -> list[str]:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    return lines


def collect_failures(results_dir: Path) -> list[str]:
    failures = []
    for status_path in sorted(results_dir.glob("*/*/status.json")) + sorted(results_dir.glob("*/*/*/status.json")):
        status = load_json(status_path) or {}
        if status.get("status") not in {None, "ok"}:
            failures.append(f"- `{status_path.relative_to(ROOT)}`: {status.get('status')} - {status.get('reason', '')}")
        compile_info = status.get("compile") or {}
        if compile_info and not compile_info.get("ok", True):
            failures.append(f"- `{status_path.relative_to(ROOT)}`: compile_failed")
    return failures


DATASET_LABELS = {
    "codecontest": "CodeContests (stdio)",
    "complex": "Scrapy complex (pytest)",
}


def render_dataset_section(results_dir: Path, label: str) -> list[str]:
    rows = baseline_a_rows(results_dir)
    b_rows, discovery_rows = baseline_b_rows(results_dir)
    rows.extend(b_rows)

    lines = [f"## {label}", "", "### Main Table", ""]
    lines.extend(
        table(
            [
                "baseline",
                "cluster",
                "files",
                "pass file %",
                "pass test %",
                "MDL compression",
                "token compression",
                "file API coverage",
                "API usage coverage",
                "conflict rate",
                "status",
            ],
            [
                [
                    row["baseline"],
                    row["cluster"],
                    fmt_num(row["files"]),
                    fmt_pct(row["pass_file"]),
                    fmt_pct(row["pass_test"]),
                    fmt_num(row["mdl"]),
                    fmt_pct(row["tokens"]),
                    fmt_pct(row["file_api"]),
                    fmt_pct(row["api_usage"]),
                    fmt_pct(row["conflict"]),
                    row["status"],
                ]
                for row in rows
            ],
        )
    )

    lines.extend(["", "### Baseline-b Discovery", ""])
    lines.extend(
        table(
            ["cluster", "discovered subclusters", "valid subclusters", "noise files", "notes"],
            [
                [
                    row["cluster"],
                    fmt_num(row["discovered"]),
                    fmt_num(row["valid"]),
                    fmt_num(row["noise"]),
                    row["notes"],
                ]
                for row in discovery_rows
            ],
        )
    )

    failures = collect_failures(results_dir)
    lines.extend(["", "### Failure Appendix", ""])
    lines.extend(failures or ["No recorded failures."])
    return lines


def render_report(results_dirs: list[Path]) -> str:
    lines = [
        "# Baseline Report",
        "",
        "结论先看 `pass rate`，再看压缩率；功能不等价的压缩不计为正收益。",
        "",
        "pass rate 只统计原始代码已通过的测试子集；具体候选数与有效分母保存在各项 `metrics.json` 的 `tests` 字段中。",
        "",
        "MDL 默认按计划保留字段；如果没有配置可返回 logprobs 的参照 LM，则显示 `not_available`。",
        "",
        "> pass rate 只在原始代码已通过的有效子集上计算，因此上限为 100%；与 100% 的差值才是改写在有效测试上的功能退化。",
        "",
    ]
    for results_dir in results_dirs:
        label = DATASET_LABELS.get(results_dir.name, results_dir.name)
        lines.extend(render_dataset_section(results_dir, label))
        lines.append("")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the merged baseline report from result artifacts.")
    parser.add_argument("--results-dir", action="append", type=Path)
    parser.add_argument("--out", type=Path, default=ROOT / "demo" / "reports" / "report.md")
    args = parser.parse_args()
    if not args.results_dir:
        args.results_dir = [ROOT / "demo" / "results" / "codecontest", ROOT / "demo" / "results" / "complex"]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render_report(args.results_dir), encoding="utf-8")
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
