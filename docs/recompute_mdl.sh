#!/bin/bash

# Dedicated MDL-only recompute job: baseline_b member-scoped before/after.
# Tests/tokens/API/conflicts are NOT recomputed (existing status.json stays).
#SBATCH -J common_mdl
#SBATCH -p gpu4090
#SBATCH -o /home/xiaoheng/demo_common_extraction/slurm_log/mdl_recompute_%j.log
#SBATCH -e /home/xiaoheng/demo_common_extraction/slurm_log/mdl_recompute_%j.err
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=32G
#SBATCH --gres=gpu:n4090:1
#SBATCH -t 12:00:00

set -Eeuo pipefail

PROJECT_DIR="/home/xiaoheng/demo_common_extraction"
cd "$PROJECT_DIR"
export PYTHONUNBUFFERED=1

source "$HOME/miniconda3/etc/profile.d/conda.sh"
conda activate qwen-gguf
export LD_LIBRARY_PATH="${CONDA_PREFIX}/lib/python3.10/site-packages/nvidia/cuda_runtime/lib:${LD_LIBRARY_PATH:-}"

log() { echo "[$(date '+%F %T')] $*"; }

log "Job started: mdl_recompute (member-scoped MDL only, code + complex)"
nvidia-smi || true
log "Running: python -u demo/scripts/recompute_mdl_b.py"
python -u demo/scripts/recompute_mdl_b.py
log "MDL recompute finished"
