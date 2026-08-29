#!/bin/bash

# --- Slurm job parameters ---
# Dedicated evaluation job: compile + tests + metrics + GGUF MDL + report.
# Requires a GPU for MDL (llama-cpp-python); extraction must already be done
# (run_extract.sh). Tests/metrics run on CPU.
#SBATCH -J common_eval
#SBATCH -p gpu4090
#SBATCH -o /home/xiaoheng/demo_common_extraction/slurm_log/eval_%j.log
#SBATCH -e /home/xiaoheng/demo_common_extraction/slurm_log/eval_%j.err
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=16
#SBATCH --mem=32G
#SBATCH --gres=gpu:n4090:1
#SBATCH -t 24:00:00

set -Eeuo pipefail

PROJECT_DIR="/home/xiaoheng/demo_common_extraction"
CONDA_ENV="${CONDA_ENV:-qwen-gguf}"
DATASET="${DATASET:-codecontest}"         # codecontest (default) or complex (Scrapy slice)
BASELINE="${BASELINE:-a}"                 # a, b, or both
CLUSTER_ID="${CLUSTER_ID:-all}"           # 0..9, or all
RESULTS_SUFFIX="${RESULTS_SUFFIX:-}"       # optional result directory suffix, e.g. _edits
TEST_TIMEOUT_SEC="${TEST_TIMEOUT_SEC:-5}"
TEST_LIMIT="${TEST_LIMIT:-0}"             # 0 means all tests
COMPARE_MODE="${COMPARE_MODE:-original}"  # original or expected
NORMALIZE="${NORMALIZE:-whitespace}"      # whitespace or strip
TEST_WORKERS="${TEST_WORKERS:-16}"        # parallel test workers (0 = auto)
REPORT_OUT="${REPORT_OUT:-$PROJECT_DIR/demo/reports/report.md}"

export TEST_WORKERS
export MODEL_PATH="${MODEL_PATH:-$PROJECT_DIR/model/Qwen3.8-27B-Q4_K_M.gguf}"
export N_CTX="${N_CTX:-4096}"
export N_GPU_LAYERS="${N_GPU_LAYERS:--1}"
export N_BATCH="${N_BATCH:-256}"

case "$DATASET" in
  codecontest)
    MANIFEST="$PROJECT_DIR/demo/datasets/codecontest/cluster_manifest.json"
    DATASET_DIR="$PROJECT_DIR/demo/datasets/codecontest"
    RESULTS_DIR="$PROJECT_DIR/demo/results/codecontest${RESULTS_SUFFIX}"
    TEST_MODE=stdio
    ;;
  complex)
    MANIFEST="$PROJECT_DIR/demo/datasets/complex/cluster_manifest.json"
    DATASET_DIR="$PROJECT_DIR/demo/datasets/complex"
    RESULTS_DIR="$PROJECT_DIR/demo/results/complex${RESULTS_SUFFIX}"
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
  if command -v nvidia-smi >/dev/null 2>&1; then
    log "GPU state at failure:"
    nvidia-smi || true
  fi
  exit "$code"
}
trap on_error ERR

mkdir -p "$PROJECT_DIR/slurm_log"

log "Job started"
log "Job id: ${SLURM_JOB_ID:-local}"
log "Host: $(hostname)"
log "Project: $PROJECT_DIR"
log "Conda env: $CONDA_ENV"
log "Dataset: $DATASET"
log "Results dir: $RESULTS_DIR"
log "Results suffix: $RESULTS_SUFFIX"
log "Baseline: $BASELINE"
log "Cluster: $CLUSTER_ID"
log "Test policy: limit=$TEST_LIMIT compare=$COMPARE_MODE normalize=$NORMALIZE"
log "Model path: $MODEL_PATH"
log "n_ctx: $N_CTX"
log "n_gpu_layers: $N_GPU_LAYERS"
log "n_batch: $N_BATCH"

cd "$PROJECT_DIR"
export PYTHONUNBUFFERED=1

if command -v nvidia-smi >/dev/null 2>&1; then
  log "GPU information:"
  nvidia-smi || true
fi

if [[ ! -f "$MODEL_PATH" ]]; then
  log "WARNING: model file does not exist: $MODEL_PATH"
  log "MDL metrics will report not_available; all other metrics still run."
  log "Set MODEL_PATH to a logprob-capable GGUF to enable MDL."
fi

source "$HOME/miniconda3/etc/profile.d/conda.sh"
conda activate "$CONDA_ENV"

# Compute nodes need the env-shipped CUDA runtime visible before importing
# llama_cpp (same setup as test_gguf.sbatch).
export LD_LIBRARY_PATH="${CONDA_PREFIX}/lib/python3.10/site-packages/nvidia/cuda_runtime/lib:${LD_LIBRARY_PATH:-}"

log "Python:"
which python
python --version
log "LD_LIBRARY_PATH: ${LD_LIBRARY_PATH}"

python - <<'PY'
import importlib.metadata as metadata
for name in ["llama-cpp-python", "numpy"]:
    try:
        print(name, metadata.version(name), flush=True)
    except metadata.PackageNotFoundError:
        print(name, "MISSING", flush=True)
PY

cluster_args=()
if [[ "$CLUSTER_ID" != "all" ]]; then
  cluster_args=(--cluster-id "$CLUSTER_ID")
fi

log "Starting metrics on existing extraction artifacts"
run_cmd python -u -m demo.eval.run_existing_metrics \
  --baseline "$BASELINE" \
  "${cluster_args[@]}" \
  --manifest "$MANIFEST" \
  --dataset-dir "$DATASET_DIR" \
  --results-dir "$RESULTS_DIR" \
  --timeout-sec "$TEST_TIMEOUT_SEC" \
  --test-limit "$TEST_LIMIT" \
  --compare-mode "$COMPARE_MODE" \
  --normalize "$NORMALIZE" \
  --test-mode "$TEST_MODE"

report_args=(--results-dir "$RESULTS_DIR" --out "$REPORT_OUT")
if [[ -z "$RESULTS_SUFFIX" ]]; then
  report_args=(
    --results-dir "$PROJECT_DIR/demo/results/codecontest"
    --results-dir "$PROJECT_DIR/demo/results/complex"
    --out "$REPORT_OUT"
  )
fi
run_cmd python -u -m demo.eval.report "${report_args[@]}"

log "Recent MDL metrics:"
find "$RESULTS_DIR" -maxdepth 4 -name mdl_metrics.json -type f -print | sort | while read -r path; do
  echo "--- $path"
  python -m json.tool "$path" | sed -n '1,120p'
done

log "Recent status files:"
find "$RESULTS_DIR" -name status.json -maxdepth 4 -type f -print | sort | while read -r path; do
  echo "--- $path"
  python -m json.tool "$path" | sed -n '1,80p'
done

log "Report path: $REPORT_OUT"
log "Job finished"
