# Common Extraction Baseline Demo

This demo is intentionally self-contained. It reads the prepared CodeContests cluster JSONL files from `Librarian/data/LLM_description_clusters/new/`, but it does not import or depend on Librarian runtime modules.

## Prepare Data

```bash
python3 -m demo.prepare.prepare_dataset
```

This writes:

- `demo/datasets/codecontest/cluster_manifest.json`
- `demo/datasets/codecontest/DATA_INVENTORY.md`
- `demo/datasets/codecontest/clusters/<cluster_id>/original/file_*.py`

Only each row's `solution` field is written into prompt-visible source files. Names, descriptions, difficulty, and tests stay in the manifest for evaluation only.

## Run Baselines

Set the DeepSeek API config first:

```bash
export DEEPSEEK_API_KEY=...
export DEEPSEEK_BASE_URL=https://api.deepseek.com
export DEEPSEEK_MODEL=deepseek-v4-flash
```

Smoke test one cluster:

```bash
python3 -m demo.baselines.run_baseline_a --cluster-id 0
python3 -m demo.baselines.run_baseline_b --cluster-id 0
```

The evaluator first runs the original solution and keeps only tests it passes; the refactored pass rate is calculated over that valid baseline subset. Token and MDL measurements exclude comments and docstrings, retaining executable code only. By default it compares the refactored program output to the original `solution` output with whitespace normalization. To run the Librarian-compatible merge-stage policy instead, use:

```bash
python3 -m demo.baselines.run_baseline_a --cluster-id 0 --test-limit 10 --compare-mode expected --normalize strip
python3 -m demo.baselines.run_baseline_b --cluster-id 0 --test-limit 10 --compare-mode expected --normalize strip
```

Run all clusters:

```bash
python3 -m demo.baselines.run_baseline_a
python3 -m demo.baselines.run_baseline_b
```

## Report

```bash
python3 -m demo.eval.report
```

The merged report (both datasets) is written to `demo/reports/report.md`.

## dataset_complex (Scrapy slice)

`demo/datasets/complex/` is a second dataset: 26 Scrapy middleware/pipeline source
files (download middlewares, spider middlewares, item pipelines, and the shared
`MiddlewareManager` framework) plus 30 upstream pytest test files, byte-identical
to scrapy commit `3c6a62db`. All 24 non-`__init__` source files form a single
cluster `0`; the eval pipeline runs the upstream pytest suite instead of
stdin/stdout tests.

### Environment

The pytest runner needs scrapy installed at the exact slice commit plus pytest
plugins (the demo runs under the `qwen-gguf` conda env):

```bash
conda run -n qwen-gguf pip install pytest pytest-asyncio pytest-twisted pyftpdlib \
  "scrapy @ git+https://github.com/scrapy/scrapy@3c6a62db47536df89676ab9d7347acf5c5c8a2e4"
```

If the git clone fails with an HTTP/2 framing error, retry with HTTP/1.1:
`GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=http.version GIT_CONFIG_VALUE_0=HTTP/1.1`.

### Prepare

```bash
conda run -n qwen-gguf python -m demo.prepare.prepare_dataset_complex
```

Writes `demo/datasets/complex/cluster_manifest.json` (one cluster, 24 member
files with per-file pytest mapping), `clusters/0/original/file_*.py`, and a
generated section in `DATA_INVENTORY.md`. The upstream test infra
(`tests/utils`, `tests/mockserver`, `tests/spiders.py`, `tests/mocks`,
`tests/sample_data`, `tests/keys`, `tests/ignores.txt`) lives in
`dataset_complex/tests/`, fetched from the same commit.

### Run baselines and eval

Same commands as the CodeContests dataset, but with `--test-mode pytest` and the
complex dataset's own paths (results go to `demo/results/complex/` so the
CodeContests results stay untouched):

```bash
conda run -n qwen-gguf python -m demo.baselines.run_baseline_a \
  --manifest demo/datasets/complex/cluster_manifest.json \
  --dataset-dir demo/datasets/complex --results-dir demo/results/complex \
  --test-mode pytest --timeout-sec 120
conda run -n qwen-gguf python -m demo.baselines.run_baseline_b \
  --manifest demo/datasets/complex/cluster_manifest.json \
  --dataset-dir demo/datasets/complex --results-dir demo/results/complex \
  --test-mode pytest --timeout-sec 120
```

To evaluate an alternate existing result directory such as
`demo/results/complex_edits/`, use `RESULTS_SUFFIX=_edits` with
`docs/run_eval.sh`.

To recompute metrics for existing extraction artifacts:

```bash
conda run -n qwen-gguf python -m demo.eval.run_existing_metrics \
  --manifest demo/datasets/complex/cluster_manifest.json \
  --dataset-dir demo/datasets/complex --results-dir demo/results/complex \
  --baseline a --test-mode pytest --timeout-sec 120
conda run -n qwen-gguf python -m demo.eval.report --results-dir demo/results/complex
```

### How pytest mode works

`demo/eval/run_tests_pytest.py` builds a temp layout per test run: the installed
`scrapy` package is symlink-copied with the cluster's original (or refactored)
files overlaid at their real package paths, `common.py` is placed at the temp
root (so refactored `import common` works), and the dataset's `tests/` dir plus
the vendored upstream root `conftest.py` (`demo/eval/pytest_conftest.py`, which
provides the `mockserver` fixture and `--reactor=asyncio`) are copied in. Each
test file is run once for the original layout and once for the refactored
layout. Only cases that pass in the original layout are selected for the
refactored run and reported denominator; a selected case counts as matched when
the refactored case also passes. Skips (e.g. optional deps like
reppy/mitmproxy) are therefore excluded from the denominator. `test_limit` does
not apply in pytest mode.

## Slurm Submission

Two dedicated scripts under `docs/` (both also run locally as plain shell
scripts):

- `docs/run_extract.sh` — DeepSeek extraction + compile only (CPU job; does not
  run tests/metrics). Supports `RESUME=1` to skip clusters/subclusters that
  already have `status.json` ok (re-running only the missing parts).
- `docs/run_eval.sh` — compile + tests + metrics + GGUF MDL + report on existing
  extraction artifacts (GPU job; does not call DeepSeek). `MODEL_PATH`,
  `N_CTX`, `N_GPU_LAYERS`, `N_BATCH` configure MDL; a missing model file only
  warns and MDL reports `not_available`.

Both accept the same `DATASET` switch: `codecontest` (default) or `complex`
(pytest mode, `demo/results/complex/`, conda env `qwen-gguf` activated
automatically). Useful overrides: `API_TIMEOUT_SEC`,
`MAX_OUTPUT_TOKENS`, `TEST_TIMEOUT_SEC` (use 120 for complex), `TEST_LIMIT`,
`COMPARE_MODE`, `NORMALIZE`, `CLUSTER_ID`.

Recommended flow — extract first, then evaluate:

```bash
# 1. extraction (needs DEEPSEEK_API_KEY)
sbatch --export=ALL,DEEPSEEK_API_KEY=your_key,BASELINE=both,CLUSTER_ID=all docs/run_extract.sh

# 2. evaluation (needs a GPU for MDL)
sbatch --export=ALL,BASELINE=both,CLUSTER_ID=all docs/run_eval.sh
```

Re-run only failed extractions:

```bash
sbatch --export=ALL,DEEPSEEK_API_KEY=your_key,BASELINE=both,CLUSTER_ID=all,RESUME=1 docs/run_extract.sh
```

Complex dataset (Scrapy slice):

```bash
sbatch --export=ALL,DEEPSEEK_API_KEY=your_key,DATASET=complex,BASELINE=both docs/run_extract.sh
sbatch --export=ALL,DATASET=complex,BASELINE=both,TEST_TIMEOUT_SEC=120 docs/run_eval.sh
```

Logs are written under `slurm_log/`; per-cluster `status.json` /
`metrics.json` / `mdl_metrics.json` live under the dataset's results dir.
