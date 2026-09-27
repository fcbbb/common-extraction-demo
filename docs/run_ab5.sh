#!/usr/bin/env bash

# 5 个 SWE-rebench 任务 × 3 次重复的单变体批量运行(direct 或 signal)。
# 两个变体各提交一个 job 并行(各占 1 张卡):
#   VARIANT=direct sbatch docs/run_ab5.sh
#   VARIANT=signal sbatch docs/run_ab5.sh
# 变体由提交时的环境变量选择;若 .env 存在则先加载(DEEPSEEK_API_KEY 等)。
# 语义嵌入 worker 通过 DEMO_EMBED_PYTHON 指向带 torch 的 conda 环境(agent env
# 的 torch 2.8.0+cu128 在 535 驱动上实测可用),不依赖本仓库 python。
#SBATCH -J ab5
#SBATCH -p gpu4090
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=16G
#SBATCH --gpus=1
#SBATCH -t 24:00:00

set -Eeuo pipefail

# slurm 会把脚本拷到节点 spool 目录执行,BASH_SOURCE 不可靠;
# 用 SLURM_SUBMIT_DIR(提交时所在目录)定位仓库根。
PROJECT_DIR="${PROJECT_DIR:-${SLURM_SUBMIT_DIR:-$(pwd)}}"
cd "$PROJECT_DIR"

VARIANT="${VARIANT:-direct}"   # direct | signal
REPS="${REPS:-3}"
TASKS="${TASKS:-chordparser_roman_editor pymdown_extensions_fancylists planet_sync_clients stix_shifter_intezer_connector tox_pyproject_toml_loader}"

if [[ -f "$PROJECT_DIR/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$PROJECT_DIR/.env"
  set +a
fi

# 计算节点上没有本地代理(127.0.0.1:14514 只在提交机存在),继承的代理变量
# 会让 pip 走 ProxyError(Connection refused);全部直连(节点实测 aliyun 镜像可直连):
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY

# 语义嵌入 worker 的解释器(agent env:sentence_transformers + torch cu128, cuda 可用)
export DEMO_EMBED_PYTHON="${DEMO_EMBED_PYTHON:-$HOME/miniconda3/envs/agent/bin/python}"

# 节点的 /usr/bin/python3 缺 python3-venv(ensurepip 不可用,venv_failed),
# runner 改用共享 home 里的 conda 3.10 环境解释器(实测可建带 pip 的 venv):
RUNNER_PY="${AB_PYTHON:-$HOME/miniconda3/envs/memorybench/bin/python}"
# agent CLI(mini)装在 qwen-gguf env,节点默认 PATH 没有;任务的 per-run venv
# bin 会排在其前,python3/pytest 仍解析到任务 venv,不受影响:
export PATH="$HOME/miniconda3/envs/qwen-gguf/bin:$PATH"
export HF_ENDPOINT="${HF_ENDPOINT:-https://hf-mirror.com}"
# agent CLI(mini)不在默认 PATH 时在此追加,例如:
# export PATH="$HOME/.local/bin:$PATH"

mkdir -p "$PROJECT_DIR/.cache/batch-logs"
LOG="$PROJECT_DIR/.cache/batch-logs/ab5-${VARIANT}-$(date +%Y%m%d-%H%M%S).log"
echo "batch log: $LOG" >&2

for T in $TASKS; do
  for R in $(seq "$REPS"); do
    echo "===== $T $VARIANT rep${R} =====" | tee -a "$LOG"
    "$RUNNER_PY" -m demo.downstream.run_once --task-id "$T" --variant "$VARIANT" \
      2>&1 | tee -a "$LOG" || echo "[$T $VARIANT rep${R}] exit=$?" | tee -a "$LOG"
  done
done

echo "===== SUMMARY ($VARIANT) =====" | tee -a "$LOG"
python3 - "$LOG" <<'PYEOF' | tee -a "$LOG"
import glob, json, os, sys
runs = []
for p in sorted(glob.glob('.cache/downstream-runs/*/*/*/run.json')):
    try:
        r = json.load(open(p))
    except Exception:
        continue
    if r.get('variant') == os.environ.get('VARIANT', 'direct') or True:
        runs.append((r.get('task_id', '?'), r.get('variant', '?'),
                     os.path.basename(os.path.dirname(p)), r.get('status', '?')))
for row in runs:
    print(f"{row[0]:38s} {row[1]:7s} {row[2]}  {row[3]}")
print(f"total runs on record: {len(runs)}")
PYEOF
