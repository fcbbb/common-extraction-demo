#!/bin/bash

# --- Slurm job parameters ---
# Dedicated DeepSeek extraction job (CPU only; DeepSeek is an API call).
# Extracts common.py + refactored files and compiles them; no tests/metrics.
# Run evaluation separately with run_eval.sh.
#SBATCH -p gpu3090
#SBATCH -J common_extract
#SBATCH -o /home/xiaoheng/demo_common_extraction/slurm_log/extract_%j.log
#SBATCH -e /home/xiaoheng/demo_common_extraction/slurm_log/extract_%j.err
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=16
#SBATCH --mem=16G
#SBATCH -t 12:00:00

set -Eeuo pipefail

PROJECT_DIR="/home/xiaoheng/demo_common_extraction"
DATASET="${DATASET:-codecontest}"         # codecontest (default) or complex (Scrapy slice)
BASELINE="${BASELINE:-both}"              # a, b, or both
CLUSTER_ID="${CLUSTER_ID:-all}"           # 0..9, or all
API_TIMEOUT_SEC="${API_TIMEOUT_SEC:-300}"
MAX_OUTPUT_TOKENS="${MAX_OUTPUT_TOKENS:-128000}"
RESUME="${RESUME:-0}"                     # 1 skips ok clusters/subclusters

case "$DATASET" in
  codecontest)
    PREPARE_MODULE=demo.prepare.prepare_dataset
    MANIFEST="$PROJECT_DIR/demo/datasets/codecontest/cluster_manifest.json"
    DATASET_DIR="$PROJECT_DIR/demo/datasets/codecontest"
    RESULTS_DIR="$PROJECT_DIR/demo/results/codecontest"
    TEST_MODE=stdio
    ;;
  complex)
    PREPARE_MODULE=demo.prepare.prepare_dataset_complex
    MANIFEST="$PROJECT_DIR/demo/datasets/complex/cluster_manifest.json"
    DATASET_DIR="$PROJECT_DIR/demo/datasets/complex"
    RESULTS_DIR="$PROJECT_DIR/demo/results/complex"
    TEST_MODE=pytest
    ;;
  *)
    echo "ERROR: unknown DATASET=$DATASET (expected codecontest or complex)" >&2
    exit 2
    ;;
esac

log() {
  echo "[$(date '+%F %T')] $*"
}

run_cmd() {
  log "RUN: $*"
  "$@"
  local code=$?
  log "EXIT($code): $*"
  return "$code"
}

on_error() {
  local code=$?
  log "ERROR: command failed with exit code ${code}"
  log "PWD: $(pwd)"
  log "Last status files:"
  find "$RESULTS_DIR" -name status.json -maxdepth 4 -type f -print 2>/dev/null | sort | tail -20 || true
  exit "$code"
}
trap on_error ERR

mkdir -p "$PROJECT_DIR/slurm_log"

log "Job started"
log "Job id: ${SLURM_JOB_ID:-local}"
log "Host: $(hostname)"
log "Project: $PROJECT_DIR"
log "Dataset: $DATASET"
log "Results dir: $RESULTS_DIR"
log "Baseline: $BASELINE"
log "Cluster: $CLUSTER_ID"
log "API timeout: $API_TIMEOUT_SEC"
log "Max output tokens: $MAX_OUTPUT_TOKENS"
log "Resume: $RESUME"
log "Stages: extraction + compile only (tests/metrics run later via run_eval.sh)"

if command -v nvidia-smi >/dev/null 2>&1; then
  log "GPU info, if visible on this node:"
  nvidia-smi || true
else
  log "nvidia-smi not found; continuing because this job does not need GPU."
fi

source "$HOME/.bashrc" || true
cd "$PROJECT_DIR"
export PYTHONUNBUFFERED=1

if [[ "$DATASET" == "complex" ]]; then
  CONDA_ENV="${CONDA_ENV:-qwen-gguf}"
  source "$HOME/miniconda3/etc/profile.d/conda.sh"
  conda activate "$CONDA_ENV"
  log "Activated conda env: $CONDA_ENV"
fi

log "Python:"
python3 --version

if [[ -z "${DEEPSEEK_API_KEY:-}" && "$RESUME" != "1" ]]; then
  log "ERROR: DEEPSEEK_API_KEY is not set."
  log "Submit with, for example:"
  log "  sbatch --export=ALL,DEEPSEEK_API_KEY=your_key,DEEPSEEK_MODEL=deepseek-v4-flash run_extract.sh"
  exit 2
fi

export DEEPSEEK_BASE_URL="${DEEPSEEK_BASE_URL:-https://api.deepseek.com}"
export DEEPSEEK_MODEL="${DEEPSEEK_MODEL:-deepseek-v4-flash}"

log "DeepSeek base url: $DEEPSEEK_BASE_URL"
log "DeepSeek model: $DEEPSEEK_MODEL"
if [[ -n "${DEEPSEEK_API_KEY:-}" ]]; then
  log "DeepSeek key: set, length ${#DEEPSEEK_API_KEY}; value hidden"
else
  log "DeepSeek key: not set; allowed only if --resume finds reusable ok artifacts."
fi

run_cmd python3 -u -m "$PREPARE_MODULE"
if [[ -n "${DEEPSEEK_API_KEY:-}" ]]; then
  run_cmd python3 -u -m demo.baselines.check_api \
    --api-timeout-sec "$API_TIMEOUT_SEC" \
    --max-output-tokens 32
else
  log "Skipping DeepSeek API check because DEEPSEEK_API_KEY is not set."
fi

cluster_args=()
if [[ "$CLUSTER_ID" != "all" ]]; then
  cluster_args=(--cluster-id "$CLUSTER_ID")
fi

common_args=(
  --manifest "$MANIFEST"
  --dataset-dir "$DATASET_DIR"
  --results-dir "$RESULTS_DIR"
  --api-timeout-sec "$API_TIMEOUT_SEC"
  --max-output-tokens "$MAX_OUTPUT_TOKENS"
  --test-mode "$TEST_MODE"
  --skip-metrics
)
if [[ "$RESUME" == "1" ]]; then
  common_args+=(--resume)
fi

if [[ "$BASELINE" == "a" || "$BASELINE" == "both" ]]; then
  log "Starting baseline-a extraction"
  run_cmd python3 -u -m demo.baselines.run_baseline_a "${cluster_args[@]}" "${common_args[@]}"
fi

if [[ "$BASELINE" == "b" || "$BASELINE" == "both" ]]; then
  log "Starting baseline-b extraction"
  run_cmd python3 -u -m demo.baselines.run_baseline_b "${cluster_args[@]}" "${common_args[@]}"
fi

log "Recent status files:"
find "$RESULTS_DIR" -name status.json -maxdepth 4 -type f -print | sort | while read -r path; do
  echo "--- $path"
  python3 -m json.tool "$path" | sed -n '1,80p'
done

log "Job finished"
