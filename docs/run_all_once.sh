#!/usr/bin/env bash

# 当前筛查任务集 × 1 次重复的快速批:验证 grounding 白名单 + 任务 prompt 两处修改。
# 与历史 run 的隔离:结果写到独立根 .cache/downstream-runs-$BATCH/(默认 postfix),
# 不与 .cache/downstream-runs/ 的历史 run 混在一起;汇总与 rescore 都指向新根。
#
# 提交(默认只跑 signal;direct 代码路径本次未改,历史 direct run 仍是有效对照):
#   VARIANT=signal sbatch docs/run_all_once.sh
# 两臂都跑(同 job 顺序执行)或分别提交两个 job:
#   VARIANT="direct signal" sbatch docs/run_all_once.sh
#
# 可调环境变量:
#   VARIANT  变体列表(默认 signal)
#   REPS     每任务重复数(默认 1)
#   BATCH    结果隔离根后缀(默认 postfix → .cache/downstream-runs-postfix)
#   TASKS    任务列表(默认下方 10 个 SWE-rebench 筛查任务;legacy 任务用 TASKS 覆盖)
#
# 预期行为:9 个任务复用已蒸馏的共享 pack(本轮只变任务 prompt,隔离 prompt
# 修改的效果);plopp 首次生成 pack(验证 grounding 白名单修复,应落地 2 个 pattern)。

#SBATCH -J all1
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

VARIANTS="${VARIANT:-signal}"
REPS="${REPS:-1}"
BATCH="${BATCH:-postfix}"
RUNS_ROOT="$PROJECT_DIR/.cache/downstream-runs-$BATCH"
TASKS="${TASKS:-chordparser_roman_editor pymdown_extensions_fancylists planet_sync_clients stix_shifter_intezer_connector tox_pyproject_toml_loader stix_shifter_abuseipdb_connector pints_mcmc_nuts_samplers feature_engine_datetime_transformers plopp_fast_image_renderer xsdata_tree_serializers}"

if [[ -f "$PROJECT_DIR/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$PROJECT_DIR/.env"
  set +a
fi

# 计算节点上没有本地代理(127.0.0.1:14514 只在提交机存在),继承的代理变量
# 会让 pip 走 ProxyError(Connection refused);全部直连:
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY

# 语义嵌入 worker 的解释器(agent env:sentence_transformers + torch cu128):
export DEMO_EMBED_PYTHON="${DEMO_EMBED_PYTHON:-$HOME/miniconda3/envs/agent/bin/python}"

# 节点 /usr/bin/python3 缺 python3-venv,runner 用共享 home 的 conda 3.10 解释器:
RUNNER_PY="${AB_PYTHON:-$HOME/miniconda3/envs/memorybench/bin/python}"
# agent CLI(mini)装在 qwen-gguf env,节点默认 PATH 没有:
export PATH="$HOME/miniconda3/envs/qwen-gguf/bin:$PATH"
export HF_ENDPOINT="${HF_ENDPOINT:-https://hf-mirror.com}"

mkdir -p "$PROJECT_DIR/.cache/batch-logs" "$RUNS_ROOT"
LOG="$PROJECT_DIR/.cache/batch-logs/all1-${BATCH}-$(date +%Y%m%d-%H%M%S).log"
echo "batch log: $LOG" >&2
GIT_REV="$(git -C "$PROJECT_DIR" rev-parse --short HEAD 2>/dev/null || echo unknown)"
GIT_DIRTY="$(git -C "$PROJECT_DIR" status --porcelain 2>/dev/null | wc -l || echo '?')"
echo "git: $GIT_REV, dirty files: $GIT_DIRTY" | tee -a "$LOG"
echo "runs root: $RUNS_ROOT" | tee -a "$LOG"
echo "variants: $VARIANTS  reps: $REPS  tasks: $(echo $TASKS | wc -w)" | tee -a "$LOG"

run_id() {
  "$RUNNER_PY" -c "from datetime import datetime; print(datetime.now().strftime('%Y%m%d-%H%M%S-%f'))"
}

for V in $VARIANTS; do
  for T in $TASKS; do
    for R in $(seq "$REPS"); do
      RESULT_DIR="$RUNS_ROOT/$T/$V/$(run_id)"
      echo "===== $T $V rep${R} -> $RESULT_DIR =====" | tee -a "$LOG"
      "$RUNNER_PY" -m demo.downstream.run_once \
        --task-id "$T" --variant "$V" \
        --result-dir "$RESULT_DIR" \
        2>&1 | tee -a "$LOG" || echo "[$T $V rep${R}] exit=$?" | tee -a "$LOG"
    done
  done
done

echo "===== SUMMARY (batch=$BATCH) =====" | tee -a "$LOG"
"$RUNNER_PY" - "$RUNS_ROOT" <<'PYEOF' | tee -a "$LOG"
import glob, json, sys

rows = []
for p in sorted(glob.glob(sys.argv[1] + "/*/*/*/run.json")):
    try:
        r = json.load(open(p))
    except Exception:
        continue
    t, u = r.get("tests") or {}, r.get("usage") or {}
    gen = u.get("signal_context_generation_wall_time_sec")
    if r.get("variant") == "signal":
        # 复用检查约 0.0007s;从 .partial 缓存恢复落地约 0.26s;全新蒸馏为分钟级。
        pack = "reused" if gen is None or gen < 0.01 else (
            f"gen {gen:.0f}s" if gen >= 1 else "materialized")
    else:
        pack = "-"
    tok = u.get("total_tokens")
    tok = f"{tok / 1e6:.1f}M" if isinstance(tok, (int, float)) else "-"
    rows.append(
        f"{r.get('task_id', '?'):40s} {r.get('variant', '?'):7s} "
        f"future={t.get('future_passed')}/{t.get('future_total')}  "
        f"tok={tok:>6s}  pack={pack}  {r.get('status')}"
    )
print("\n".join(rows))
print(f"total new runs: {len(rows)}")
PYEOF

echo "" | tee -a "$LOG"
echo "对比历史 run(固定分母口径):" | tee -a "$LOG"
echo "  python3 -m demo.downstream.rescore_runs --runs-root '$RUNS_ROOT'" | tee -a "$LOG"
echo "  python3 -m demo.downstream.rescore_runs   # 历史根,输出旧批对照表" | tee -a "$LOG"
