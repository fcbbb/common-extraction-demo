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
        return "不可用"
    if isinstance(value, (int, float)):
        return f"{value * 100:.1f}%"
    return str(value)


def fmt_num(value: Any) -> str:
    if value is None:
        return "不可用"
    if value == "not_available":
        return "不可用"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


METHOD_LABELS = {
    "baseline_a": "Baseline A",
    "baseline_b": "Baseline B",
    "signal": "Signal",
}


STATUS_LABELS = {
    "ok": "通过",
    "tests_failed": "测试失败",
    "partial_failed": "部分失败",
    "compile_failed": "编译失败",
    "failed": "失败",
    "missing": "缺失",
    "not_run": "未运行",
}


def fmt_status(value: Any) -> str:
    return STATUS_LABELS.get(str(value), str(value))


def baseline_a_rows(results_dir: Path) -> list[dict[str, Any]]:
    rows = []
    for status_path in sorted((results_dir / "baseline_a").glob("*/status.json")):
        status = load_json(status_path) or {}
        metrics = status.get("metrics") or load_json(status_path.parent / "metrics.json") or {}
        rows.append(flat_row("baseline_a", status_path.parent.name, None, status, metrics))
    return rows


def method_rows(results_dir: Path, method: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Main-table rows + discovery-table rows for one subcluster method dir (baseline_b, signal)."""
    main_rows = []
    discovery_rows = []
    method_root = results_dir / method
    if not method_root.exists():
        return main_rows, discovery_rows
    for cluster_status_path in sorted(method_root.glob("*/status.json")):
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
        current_subclusters = status.get("subclusters")
        if isinstance(current_subclusters, list):
            # A rerun may leave obsolete sub_* directories behind.  The
            # cluster status is the authoritative list for the current
            # discovery, so do not mix stale artifacts into the report.
            for sub_status in current_subclusters:
                sub_id = sub_status.get("subcluster_id")
                if not sub_id:
                    continue
                sub_dir = cluster_status_path.parent / sub_id
                metrics = sub_status.get("metrics") or load_json(sub_dir / "metrics.json") or {}
                main_rows.append(flat_row(method, cluster_id, sub_id, sub_status, metrics))
        else:
            for sub_status_path in sorted(cluster_status_path.parent.glob("*/status.json")):
                sub_status = load_json(sub_status_path) or {}
                metrics = sub_status.get("metrics") or load_json(sub_status_path.parent / "metrics.json") or {}
                main_rows.append(flat_row(method, cluster_id, sub_status_path.parent.name, sub_status, metrics))
    return main_rows, discovery_rows


def flat_row(baseline: str, cluster: str, subcluster: str | None, status: dict[str, Any], metrics: dict[str, Any]) -> dict[str, Any]:
    tests = metrics.get("tests") or {}
    tokens = metrics.get("tokens") or {}
    api = metrics.get("api_coverage") or {}
    conflicts = metrics.get("conflicts") or {}
    mdl = metrics.get("mdl") or {}
    return {
        "baseline": METHOD_LABELS.get(baseline, baseline),
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


def _relpath(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def collect_failures(results_dir: Path) -> list[str]:
    failures = []
    for status_path in sorted(results_dir.glob("*/*/status.json")):
        status = load_json(status_path) or {}
        if status.get("status") not in {None, "ok"}:
            failures.append(
                f"- `{_relpath(status_path)}`：{fmt_status(status.get('status'))}"
                f"；{status.get('reason', '')}"
            )
        compile_info = status.get("compile") or {}
        if compile_info and not compile_info.get("ok", True):
            failures.append(f"- `{_relpath(status_path)}`：编译失败")
        current_subclusters = status.get("subclusters")
        if not isinstance(current_subclusters, list):
            continue
        for sub_status in current_subclusters:
            if sub_status.get("status") in {None, "ok"}:
                continue
            sub_id = sub_status.get("subcluster_id", "unknown")
            sub_path = status_path.parent / sub_id / "status.json"
            failures.append(
                f"- `{_relpath(sub_path)}`：{fmt_status(sub_status.get('status'))}"
                f"；{sub_status.get('reason', '')}"
            )
    return failures


DATASET_LABELS = {
    "codecontest": "CodeContests（标准输入输出测试）",
    "complex": "Scrapy complex（pytest 测试）",
    "libcloud_loadbalancer_real": "Apache Libcloud 负载均衡驱动（pytest 测试）",
}


def render_dataset_section(results_dir: Path, label: str) -> list[str]:
    rows = baseline_a_rows(results_dir)
    discovery_sections = []
    for method in ("baseline_b", "signal"):
        m_rows, discovery_rows = method_rows(results_dir, method)
        rows.extend(m_rows)
        if discovery_rows:
            discovery_sections.append((method, discovery_rows))

    lines = [f"## {label}", "", "### 结果主表", ""]
    lines.extend(
        table(
            [
                "方法",
                "代码簇",
                "文件数",
                "文件通过率",
                "测试通过率",
                "MDL 压缩率",
                "token 压缩率",
                "文件 API 覆盖率",
                "API 使用覆盖率",
                "冲突率",
                "状态",
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
                    fmt_status(row["status"]),
                ]
                for row in rows
            ],
        )
    )

    for method, discovery_rows in discovery_sections:
        lines.extend(["", f"### {METHOD_LABELS.get(method, method)} 发现结果", ""])
        lines.extend(
            table(
                ["代码簇", "发现候选数", "有效候选数", "噪声文件数", "状态"],
                [
                    [
                        row["cluster"],
                        fmt_num(row["discovered"]),
                        fmt_num(row["valid"]),
                        fmt_num(row["noise"]),
                        fmt_status(row["notes"]),
                    ]
                    for row in discovery_rows
                ],
            )
        )

    failures = collect_failures(results_dir)
    lines.extend(["", "### 失败附录", ""])
    lines.extend(failures or ["没有记录到失败项。"])
    return lines


def render_report(results_dirs: list[Path]) -> str:
    lines = [
        "# 方法评估明细报告",
        "",
        "结论先看测试通过率，再看压缩率；功能不等价的压缩不计为正收益。",
        "",
        "通过率只统计原始代码已通过的测试子集；具体候选数与有效分母保存在各项 `metrics.json` 的 `tests` 字段中。",
        "",
        "MDL 默认保留相应字段；如果没有配置可返回对数概率的参照语言模型，则显示“不可用”。",
        "",
        "> 通过率只在原始代码已通过的有效子集上计算，因此上限为 100%；与 100% 的差值才是改写在有效测试上的功能退化。",
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
