"""Validate the small, committed release artifacts without raw experiment logs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]


def load_json(relative_path: str) -> dict[str, Any]:
    path = ROOT / relative_path
    if not path.is_file():
        raise AssertionError(f"缺少发布产物：{relative_path}")
    return json.loads(path.read_text(encoding="utf-8"))


def verify() -> list[str]:
    codecontest = load_json("demo/results/codecontest/full_recompute_summary.json")
    libcloud = load_json("demo/results/libcloud_loadbalancer_real/full_pool_comparison.json")
    recall = load_json("demo/results/libcloud_loadbalancer_real/embedding_recall_summary.json")

    assert codecontest["dataset"]["clusters"] == 10
    assert codecontest["dataset"]["files"] == 300
    baseline = codecontest["baseline_a_accepted_full_pool"]
    signal = codecontest["signal_disjoint_full_pool_projection"]
    comparison = codecontest["comparison"]
    assert baseline["passed_tests"] == baseline["eligible_tests"] == 24897
    assert signal["passed_tests"] == signal["eligible_tests"] == 24897
    assert baseline["tokens_before"] == signal["tokens_before"] == 121644
    assert baseline["tokens_after"] == 117004
    assert signal["tokens_after"] == 115399
    assert comparison["signal_better_by_tokens"] == baseline["tokens_after"] - signal["tokens_after"]

    rows = {row["method"]: row for row in libcloud["rows"]}
    assert libcloud["dataset"] == "apache/libcloud"
    assert libcloud["file_count"] == 10
    assert rows["baseline_a"]["tokens_before"] == rows["signal"]["tokens_before"] == 26045
    assert rows["baseline_a"]["tokens_after"] == 26031
    assert rows["signal"]["tokens_after"] == 23471
    assert libcloud["decision"] == "signal_not_worse"
    assert recall["model"] == "codefuse-ai/C2LLM-0.5B"
    assert recall["units"] == 400
    assert recall["recall_at_0_88"] > 0.89

    readable = [
        "demo/reports/final_report_signal.md",
        "demo/results/codecontest/EXPERIMENT_REPORT.md",
        "demo/results/libcloud_loadbalancer_real/EXPERIMENT_REPORT.md",
    ]
    for relative_path in readable:
        text = (ROOT / relative_path).read_text(encoding="utf-8")
        assert len(text.strip()) > 100, f"报告为空或过短：{relative_path}"

    return [
        "CodeContests：Signal 5.13%，Baseline A 3.81%，有效测试全部通过",
        "Libcloud：Signal 9.88%，Baseline A 0.05%",
        "三份中文报告均存在且非空",
    ]


def main() -> None:
    for line in verify():
        print(f"[通过] {line}")


if __name__ == "__main__":
    main()
