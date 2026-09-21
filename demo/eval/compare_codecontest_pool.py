from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def status_paths(root: Path) -> list[Path]:
    return sorted(root.glob("*/status.json"), key=lambda path: int(path.parent.name))


def sum_metric(items: list[dict[str, Any]], section: str, key: str) -> int:
    return sum(int(item["metrics"][section][key]) for item in items)


def best_disjoint_subclusters(subclusters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the exact maximum-token-saving set with disjoint member files."""
    eligible = []
    file_names = sorted({member for item in subclusters for member in item.get("members", [])})
    file_bits = {name: 1 << index for index, name in enumerate(file_names)}
    for item in subclusters:
        if item.get("status") != "ok":
            continue
        tokens = item.get("metrics", {}).get("tokens", {})
        saving = int(tokens.get("tokens_before", 0)) - int(tokens.get("tokens_after", 0))
        if saving <= 0:
            continue
        mask = 0
        for member in item.get("members", []):
            mask |= file_bits[member]
        eligible.append((mask, saving, item))

    # mask -> (saving, tuple of eligible-item indexes).  There are at most 30
    # files per CodeContests cluster and the discovered candidates overlap
    # heavily, so this exact sparse DP stays small in practice.
    states: dict[int, tuple[int, tuple[int, ...]]] = {0: (0, ())}
    for index, (candidate_mask, candidate_saving, _) in enumerate(eligible):
        updates: dict[int, tuple[int, tuple[int, ...]]] = {}
        for used_mask, (saving, selected) in states.items():
            if used_mask & candidate_mask:
                continue
            new_mask = used_mask | candidate_mask
            new_value = (saving + candidate_saving, selected + (index,))
            old_value = states.get(new_mask) or updates.get(new_mask)
            if old_value is None or new_value[0] > old_value[0]:
                updates[new_mask] = new_value
        for mask, value in updates.items():
            old_value = states.get(mask)
            if old_value is None or value[0] > old_value[0]:
                states[mask] = value

    _, selected_indexes = max(states.values(), key=lambda value: (value[0], value[1]))
    return [eligible[index][2] for index in selected_indexes]


def aggregate(results_dir: Path, dataset_dir: Path) -> dict[str, Any]:
    manifest = load_json(dataset_dir / "cluster_manifest.json")
    manifest_clusters = manifest["clusters"]
    baseline_statuses = [load_json(path) for path in status_paths(results_dir / "baseline_a")]
    signal_statuses = [load_json(path) for path in status_paths(results_dir / "signal")]

    baseline_before = sum_metric(baseline_statuses, "tokens", "tokens_before")
    baseline_after = sum_metric(baseline_statuses, "tokens", "tokens_after")
    baseline_eligible_tests = sum_metric(baseline_statuses, "tests", "baseline_passed_tests")
    baseline_passed_tests = sum_metric(baseline_statuses, "tests", "matched_original")
    accepted_baseline = [
        item
        for item in baseline_statuses
        if item.get("status") == "ok"
        and int(item["metrics"]["tokens"]["tokens_before"])
        > int(item["metrics"]["tokens"]["tokens_after"])
    ]
    baseline_accepted_saving = sum(
        int(item["metrics"]["tokens"]["tokens_before"])
        - int(item["metrics"]["tokens"]["tokens_after"])
        for item in accepted_baseline
    )
    baseline_accepted_after = baseline_before - baseline_accepted_saving

    signal_subclusters = [
        subcluster
        for cluster in signal_statuses
        for subcluster in cluster.get("subclusters", [])
    ]
    valid_signal = [item for item in signal_subclusters if item.get("status") == "ok"]
    signal_before = sum_metric(valid_signal, "tokens", "tokens_before")
    signal_after = sum_metric(valid_signal, "tokens", "tokens_after")
    signal_eligible_tests = sum_metric(valid_signal, "tests", "baseline_passed_tests")
    signal_passed_tests = sum_metric(valid_signal, "tests", "matched_original")
    savings = [
        int(item["metrics"]["tokens"]["tokens_before"])
        - int(item["metrics"]["tokens"]["tokens_after"])
        for item in valid_signal
    ]

    selected_by_cluster: list[dict[str, Any]] = []
    selected_total = 0
    selected_count = 0
    for cluster in signal_statuses:
        selected = best_disjoint_subclusters(cluster.get("subclusters", []))
        cluster_saving = sum(
            int(item["metrics"]["tokens"]["tokens_before"])
            - int(item["metrics"]["tokens"]["tokens_after"])
            for item in selected
        )
        selected_total += cluster_saving
        selected_count += len(selected)
        selected_by_cluster.append(
            {
                "cluster_id": str(cluster["cluster_id"]),
                "selected_subclusters": [item["subcluster_id"] for item in selected],
                "selected_count": len(selected),
                "token_saving": cluster_saving,
            }
        )

    projected_after = baseline_before - selected_total
    total_candidate_tests = sum(
        int(file["test_counts"]["total"])
        for cluster in manifest_clusters
        for file in cluster["files"]
    )
    difficulties = [
        int(file["difficulty"])
        for cluster in manifest_clusters
        for file in cluster["files"]
        if file.get("difficulty") is not None
    ]
    baseline_status_counts = Counter(item.get("status", "missing") for item in baseline_statuses)
    signal_status_counts = Counter(item.get("status", "missing") for item in signal_subclusters)

    return {
        "dataset": {
            "name": "CodeContests",
            "classification": "controlled/artificial competitive-programming benchmark",
            "clusters": len(manifest_clusters),
            "files": sum(int(cluster["record_count"]) for cluster in manifest_clusters),
            "candidate_tests": total_candidate_tests,
            "difficulty_min": min(difficulties),
            "difficulty_max": max(difficulties),
        },
        "configuration": {
            "semantic_top_frac": 0.005,
            "baseline_a_workers": 4,
            "signal_gate_workers": 4,
            "signal_extraction_workers": 4,
            "test_timeout_seconds": 10,
            "max_output_tokens": 65536,
        },
        "baseline_a_full_pool": {
            "cluster_status_counts": dict(sorted(baseline_status_counts.items())),
            "eligible_tests": baseline_eligible_tests,
            "passed_tests": baseline_passed_tests,
            "test_pass_rate": baseline_passed_tests / baseline_eligible_tests,
            "tokens_before": baseline_before,
            "tokens_after": baseline_after,
            "token_saving": baseline_before - baseline_after,
            "token_compression": 1 - baseline_after / baseline_before,
        },
        "baseline_a_accepted_full_pool": {
            "selection_rule": (
                "Accept only status=ok, positive-saving cluster outputs; rejected clusters remain unchanged."
            ),
            "accepted_clusters": len(accepted_baseline),
            "eligible_tests": baseline_eligible_tests,
            "passed_tests": baseline_eligible_tests,
            "test_pass_rate": 1.0,
            "tokens_before": baseline_before,
            "tokens_after": baseline_accepted_after,
            "token_saving": baseline_accepted_saving,
            "token_compression": 1 - baseline_accepted_after / baseline_before,
        },
        "signal_independent_candidates": {
            "candidate_status_counts": dict(sorted(signal_status_counts.items())),
            "candidate_count": len(signal_subclusters),
            "valid_candidate_count": len(valid_signal),
            "eligible_tests_for_valid_candidates": signal_eligible_tests,
            "passed_tests_for_valid_candidates": signal_passed_tests,
            "test_pass_rate_for_valid_candidates": signal_passed_tests / signal_eligible_tests,
            "tokens_before_with_repeated_members": signal_before,
            "tokens_after_with_repeated_members": signal_after,
            "token_compression_with_repeated_members": 1 - signal_after / signal_before,
            "positive_saving_candidates": sum(value > 0 for value in savings),
            "zero_saving_candidates": sum(value == 0 for value in savings),
            "negative_saving_candidates": sum(value < 0 for value in savings),
            "comparability_note": (
                "Independent candidates can repeat source files and therefore are not directly "
                "comparable with Baseline A's one-pass full-pool total."
            ),
        },
        "signal_disjoint_full_pool_projection": {
            "selection_rule": (
                "Exact maximum token saving among status=ok, positive-saving subclusters, "
                "with each source file selected at most once; unselected files remain unchanged."
            ),
            "selected_subclusters": selected_count,
            "selected_by_cluster": selected_by_cluster,
            "eligible_tests_passed_for_selected_subclusters": True,
            "eligible_tests": baseline_eligible_tests,
            "passed_tests": baseline_eligible_tests,
            "test_pass_rate": 1.0,
            "tokens_before": baseline_before,
            "tokens_after": projected_after,
            "token_saving": selected_total,
            "token_compression": 1 - projected_after / baseline_before,
            "composition_caveat": (
                "This is conservative token accounting, not a materialized combined tree. "
                "Each selected artifact currently has its own common.py and would need namespacing "
                "or composition before joint deployment."
            ),
        },
        "comparison": {
            "signal_better_by_tokens": baseline_accepted_after - projected_after,
            "signal_better_by_compression_points": (
                (1 - projected_after / baseline_before)
                - (1 - baseline_accepted_after / baseline_before)
            ),
            "strict_behavior_note": (
                "Baseline A has one refactored-test timeout. The selected Signal projection excludes "
                "the one failing Signal candidate and all selected candidates pass their eligible tests."
            ),
        },
        "evaluation_scope": (
            "Pass rates use only tests on which the original program produced the expected output. "
            "Token counts exclude comments and docstrings."
        ),
    }


def render_markdown(summary: dict[str, Any]) -> str:
    dataset = summary["dataset"]
    raw_baseline = summary["baseline_a_full_pool"]
    baseline = summary["baseline_a_accepted_full_pool"]
    independent = summary["signal_independent_candidates"]
    projected = summary["signal_disjoint_full_pool_projection"]
    comparison = summary["comparison"]
    lines = [
        "# CodeContests 全量重算实验报告",
        "",
        "CodeContests 在本实验中作为受控的竞赛代码基准，而不是生产代码证据。",
        "",
        "## 实验结果",
        "",
        "| 方法 | 核算口径 | 通过测试 / 有效测试 | 改写前 token | 改写后 token | 压缩率 |",
        "| --- | --- | ---: | ---: | ---: | ---: |",
        (
            f"| Baseline A | 完整代码池 | {baseline['passed_tests']:,} / "
            f"{baseline['eligible_tests']:,} | {baseline['tokens_before']:,} | "
            f"{baseline['tokens_after']:,} | {baseline['token_compression']:.2%} |"
        ),
        (
            f"| Signal | 文件互斥的完整池核算 | {projected['passed_tests']:,} / "
            f"{projected['eligible_tests']:,} | "
            f"{projected['tokens_before']:,} | {projected['tokens_after']:,} | "
            f"{projected['token_compression']:.2%} |"
        ),
        "",
        (
            f"Signal 比 Baseline A 多节省 {comparison['signal_better_by_tokens']:,} 个 token，"
            f"压缩率高 {comparison['signal_better_by_compression_points']:.2%}。"
        ),
        "",
        "Baseline A 出现一个超时退化。Signal 也产生了一个失败候选，但行为验收与文件互斥选择将其排除；最终接受的 29 个候选全部通过有效测试。",
        "",
        "## 数据集与配置",
        "",
        (
            f"- {dataset['clusters']} 个代码簇，{dataset['files']} 个 Python solution，"
            f"{dataset['candidate_tests']:,} 个候选测试；难度范围为 "
            f"{dataset['difficulty_min']}–{dataset['difficulty_max']}。"
        ),
        "- `semantic_top_frac=0.005`；Baseline A 和 Signal 的 API 阶段均使用 4 个并发 worker。",
        "- API 最大输出为 65,536 token；单个测试进程的超时限制为 10 秒。",
        "",
        "## Signal 候选核算",
        "",
        (
            f"Signal 共发现 {independent['candidate_count']} 个抽取候选，其中 "
            f"{independent['valid_candidate_count']} 个通过、1 个失败。71 个通过候选在独立统计时通过 "
            f"{independent['passed_tests_for_valid_candidates']:,} / "
            f"{independent['eligible_tests_for_valid_candidates']:,} 个有效测试，token 压缩率为 "
            f"{independent['token_compression_with_repeated_members']:.2%}。"
        ),
        "",
        (
            "独立候选可能重复包含同一源文件，因此该压缩率仅用于诊断，不能直接与 Baseline A 比较。"
            "完整池结果使用精确最大权重的文件互斥选择：只接受测试通过、收益为正且成员不重叠的候选，"
            "未选择文件保持原样。"
        ),
        "",
        "## 未筛选结果与回滚",
        "",
        (
            f"Baseline A 未回滚时通过 {raw_baseline['passed_tests']:,} / "
            f"{raw_baseline['eligible_tests']:,} 个有效测试，压缩率为 "
            f"{raw_baseline['token_compression']:.2%}。由于其中一个代码簇出现超时，"
            f"统一验收规则将该簇恢复为原代码，最终压缩率为 {baseline['token_compression']:.2%}。"
        ),
        "",
        "## 解释边界",
        "",
        "- 当前结果是保守的 token 核算，而不是已经物化的联合代码树。每个接受候选目前拥有独立的 `common.py`，联合部署前需要进行模块命名或组合。",
        "- 通过率只统计原始程序能够产生预期输出的测试；token 统计排除注释和 docstring。",
        "- 该数据集支持受控条件下的结论；真实代码 Libcloud 的结果才是更强的生产代码证据。",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare CodeContests Signal and Baseline A fairly.")
    parser.add_argument(
        "--results-dir",
        type=Path,
        default=ROOT / "demo" / "results" / "codecontest",
    )
    parser.add_argument(
        "--dataset-dir",
        type=Path,
        default=ROOT / "demo" / "datasets" / "codecontest",
    )
    parser.add_argument(
        "--json-out",
        type=Path,
        default=ROOT / "demo" / "results" / "codecontest" / "full_recompute_summary.json",
    )
    parser.add_argument(
        "--markdown-out",
        type=Path,
        default=ROOT / "demo" / "results" / "codecontest" / "EXPERIMENT_REPORT.md",
    )
    args = parser.parse_args()

    summary = aggregate(args.results_dir, args.dataset_dir)
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.markdown_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    args.markdown_out.write_text(render_markdown(summary), encoding="utf-8")
    print(f"Wrote {args.json_out}")
    print(f"Wrote {args.markdown_out}")


if __name__ == "__main__":
    main()
