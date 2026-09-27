#!/usr/bin/env bash

# Submit one Signal-reuse run for each task in the current existing cohort.
# The known Planet task is omitted per the current task-selection decision.
# Override TASKS to change the cohort, or BATCH to choose another results root.
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
TASKS="${TASKS:-chordparser_roman_editor pymdown_extensions_fancylists stix_shifter_intezer_connector tox_pyproject_toml_loader stix_shifter_abuseipdb_connector pints_mcmc_nuts_samplers feature_engine_datetime_transformers plopp_fast_image_renderer xsdata_tree_serializers}"
BATCH="${BATCH:-reuse-existing-$(date +%Y%m%d-%H%M%S)}"

EXPORTS="ALL,PROJECT_DIR=$PROJECT_DIR,VARIANT=signal,SIGNAL_DELIVERY=reuse,REPS=1,BATCH=$BATCH,TASKS=$TASKS"
echo "Submitting Signal reuse batch: batch=$BATCH tasks=$(wc -w <<< "$TASKS") reps=1"
exec sbatch --chdir="$PROJECT_DIR" --export="$EXPORTS" "$PROJECT_DIR/docs/run_all_once.sh"
