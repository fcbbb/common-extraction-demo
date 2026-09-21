#!/usr/bin/env bash

# 对已有抽取产物执行编译、行为测试和指标统计，不调用远端 API。
#SBATCH -J common_eval
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=16
#SBATCH --mem=32G
#SBATCH -t 24:00:00

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${PROJECT_DIR:-$(cd -- "$SCRIPT_DIR/.." && pwd)}"
DATASET="${DATASET:-codecontest}"       # codecontest | libcloud
METHOD="${METHOD:-${BASELINE:-all}}"    # a | b | signal | all
CLUSTER_ID="${CLUSTER_ID:-all}"
RESULTS_DIR="${RESULTS_DIR:-}"
TEST_TIMEOUT_SEC="${TEST_TIMEOUT_SEC:-}"
TEST_WORKERS="${TEST_WORKERS:-16}"
ENABLE_MDL="${ENABLE_MDL:-0}"
REPORT_OUT="${REPORT_OUT:-}"

case "$DATASET" in
  codecontest)
    DATASET_DIR="$PROJECT_DIR/demo/datasets/codecontest"
    RESULTS_NAME=codecontest
    TEST_MODE=stdio
    TEST_TIMEOUT_SEC="${TEST_TIMEOUT_SEC:-10}"
    ;;
  libcloud)
    DATASET_DIR="$PROJECT_DIR/demo/datasets/libcloud_loadbalancer_real"
    RESULTS_NAME=libcloud_loadbalancer_real
    TEST_MODE=pytest
    TEST_TIMEOUT_SEC="${TEST_TIMEOUT_SEC:-120}"
    ;;
  *)
    echo "错误：DATASET 必须是 codecontest 或 libcloud" >&2
    exit 2
    ;;
esac

MANIFEST="$DATASET_DIR/cluster_manifest.json"
RESULTS_DIR="${RESULTS_DIR:-$PROJECT_DIR/demo/results/${RESULTS_NAME}}"
REPORT_OUT="${REPORT_OUT:-$PROJECT_DIR/demo/reports/report_${DATASET}.md}"

if [[ ! -f "$MANIFEST" ]]; then
  echo "错误：缺少数据集清单 $MANIFEST；Libcloud 可先运行 make prepare-libcloud。" >&2
  exit 2
fi

cd "$PROJECT_DIR"
cluster_args=()
if [[ "$CLUSTER_ID" != "all" ]]; then
  cluster_args=(--cluster-id "$CLUSTER_ID")
fi
mdl_args=(--skip-mdl)
if [[ "$ENABLE_MDL" == "1" ]]; then
  mdl_args=()
fi
common_args=(
  "${cluster_args[@]}"
  --manifest "$MANIFEST"
  --dataset-dir "$DATASET_DIR"
  --results-dir "$RESULTS_DIR"
  --test-mode "$TEST_MODE"
  --timeout-sec "$TEST_TIMEOUT_SEC"
  --test-limit 0
  --compare-mode original
  --normalize whitespace
  --workers "$TEST_WORKERS"
  "${mdl_args[@]}"
)

run_method() {
  python3 -u -m demo.eval.run_existing_metrics --baseline "$1" "${common_args[@]}"
}

case "$METHOD" in
  a|b|signal) run_method "$METHOD" ;;
  all) run_method a; run_method signal ;;
  *)
    echo "错误：METHOD 必须是 a、b、signal 或 all" >&2
    exit 2
    ;;
esac

python3 -u -m demo.eval.report --results-dir "$RESULTS_DIR" --out "$REPORT_OUT"
echo "评估完成：$REPORT_OUT"
