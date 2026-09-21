#!/usr/bin/env bash

# 可直接在本地运行，也可用 sbatch 提交。只负责生成代码与编译；评估见 run_eval.sh。
#SBATCH -J common_extract
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=16G
#SBATCH -t 12:00:00

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${PROJECT_DIR:-$(cd -- "$SCRIPT_DIR/.." && pwd)}"
DATASET="${DATASET:-codecontest}"       # codecontest | libcloud
METHOD="${METHOD:-${BASELINE:-all}}"    # a | b | signal | all
CLUSTER_ID="${CLUSTER_ID:-all}"
RESULTS_DIR="${RESULTS_DIR:-}"
API_TIMEOUT_SEC="${API_TIMEOUT_SEC:-1800}"
MAX_OUTPUT_TOKENS="${MAX_OUTPUT_TOKENS:-65536}"
WORKERS="${WORKERS:-4}"
RESUME="${RESUME:-0}"
SEMANTIC_TOP_FRAC="${SEMANTIC_TOP_FRAC:-0.005}"

if [[ -f "$PROJECT_DIR/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$PROJECT_DIR/.env"
  set +a
fi

case "$DATASET" in
  codecontest)
    DATASET_DIR="$PROJECT_DIR/demo/datasets/codecontest"
    RESULTS_NAME=codecontest
    TEST_MODE=stdio
    ;;
  libcloud)
    DATASET_DIR="$PROJECT_DIR/demo/datasets/libcloud_loadbalancer_real"
    RESULTS_NAME=libcloud_loadbalancer_real
    TEST_MODE=pytest
    ;;
  *)
    echo "错误：DATASET 必须是 codecontest 或 libcloud" >&2
    exit 2
    ;;
esac

MANIFEST="$DATASET_DIR/cluster_manifest.json"
RESULTS_DIR="${RESULTS_DIR:-$PROJECT_DIR/demo/results/${RESULTS_NAME}}"

if [[ ! -f "$MANIFEST" ]]; then
  if [[ "$DATASET" == "libcloud" ]]; then
    python3 -m demo.prepare.prepare_dataset_libcloud_loadbalancer_real
  else
    echo "错误：缺少已跟踪的 CodeContests 清单：$MANIFEST" >&2
    exit 2
  fi
fi

if [[ -z "${DEEPSEEK_API_KEY:-}" ]]; then
  echo "错误：请设置 DEEPSEEK_API_KEY，或在项目根目录创建 .env。" >&2
  exit 2
fi
export DEEPSEEK_BASE_URL="${DEEPSEEK_BASE_URL:-https://api.deepseek.com}"
export DEEPSEEK_MODEL="${DEEPSEEK_MODEL:-deepseek-flash}"

cd "$PROJECT_DIR"
mkdir -p slurm_log

cluster_args=()
if [[ "$CLUSTER_ID" != "all" ]]; then
  cluster_args=(--cluster-id "$CLUSTER_ID")
fi
resume_args=()
if [[ "$RESUME" == "1" ]]; then
  resume_args=(--resume)
fi
common_args=(
  --manifest "$MANIFEST"
  --dataset-dir "$DATASET_DIR"
  --results-dir "$RESULTS_DIR"
  --test-mode "$TEST_MODE"
  --api-timeout-sec "$API_TIMEOUT_SEC"
  --max-output-tokens "$MAX_OUTPUT_TOKENS"
  --skip-metrics
  "${cluster_args[@]}"
  "${resume_args[@]}"
)

run_a() {
  python3 -u -m demo.baselines.run_baseline_a "${common_args[@]}" --workers "$WORKERS"
}

run_b() {
  python3 -u -m demo.baselines.run_baseline_b "${common_args[@]}"
}

run_signal() {
  python3 -u -m demo.baselines.run_signal "${common_args[@]}" \
    --semantic-top-frac "$SEMANTIC_TOP_FRAC" \
    --gate-workers "$WORKERS" --workers "$WORKERS"
}

case "$METHOD" in
  a) run_a ;;
  b) run_b ;;
  signal) run_signal ;;
  all) run_a; run_signal ;;
  *)
    echo "错误：METHOD 必须是 a、b、signal 或 all" >&2
    exit 2
    ;;
esac

echo "抽取完成：$RESULTS_DIR"
