#!/usr/bin/env bash
#
# Sprint 1 Task 1 acceptance check.
#
# Runs the pipeline once without the refactor (pre-sprint baseline), then
# again with the refactor applied, and diffs every output byte-for-byte.
# Pass = the rename + __init__.py shim did not change simulator behaviour.
#
# Run from the repo root AFTER Task 1 has been applied to the working tree
# but BEFORE the changes are committed. The script stashes the uncommitted
# refactor to produce the pre-sprint baseline, then pops it back.
#
# Usage:
#   bash scripts/verify_sprint1_task1.sh [TICKER] [DATE]
#
# Defaults: TICKER=drr DATE=20240905
#
set -euo pipefail

TICKER="${1:-drr}"
DATE="${2:-20240905}"

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

STAMP="$(date +%Y%m%d-%H%M%S)"
BASELINE_DIR="/tmp/swp-sprint1-baseline-${STAMP}"
OUTPUT_DIR="${REPO_ROOT}/data/outputs/${DATE}"
PROCESSED_DIR="${REPO_ROOT}/data/processed/${DATE}"

# --- Pre-flight ---------------------------------------------------------------
echo "==> Pre-flight"

if ! git diff --quiet HEAD -- src/pipeline/sweep_simulator src/pipeline/sweep_simulator.py 2>/dev/null ||
   [[ -n "$(git ls-files --others --exclude-standard src/pipeline/sweep_simulator 2>/dev/null)" ]]; then
  echo "    refactor is in the working tree, as expected"
else
  echo "ERROR: refactor not detected in working tree. Apply Sprint 1 Task 1 first."
  exit 1
fi

if [[ ! -d "${REPO_ROOT}/data/raw" ]]; then
  echo "ERROR: ${REPO_ROOT}/data/raw not found. Need raw input CSVs to run the pipeline."
  exit 1
fi

# --- Stash the refactor so we can run the pre-sprint version -----------------
echo "==> Stashing refactor"
STASH_MSG="sprint1-task1-verify-${STAMP}"
git stash push -u -m "${STASH_MSG}" >/dev/null
trap 'echo "==> Restoring refactor from stash"; git stash pop >/dev/null 2>&1 || true' EXIT

# --- Run 1: pre-sprint -------------------------------------------------------
echo "==> Clearing stale outputs + pycache"
rm -rf "${OUTPUT_DIR}" "${PROCESSED_DIR}"
find "${REPO_ROOT}/src" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true

echo "==> Run 1: pre-sprint simulator"
( cd src && python main.py --ticker "${TICKER}" --date "${DATE}" --stage 1 --stage 2 )

echo "==> Snapshotting pre-sprint output to ${BASELINE_DIR}"
mkdir -p "${BASELINE_DIR}"
cp -R "${OUTPUT_DIR}"  "${BASELINE_DIR}/outputs"
cp -R "${PROCESSED_DIR}" "${BASELINE_DIR}/processed"

# --- Restore refactor and run again ------------------------------------------
echo "==> Restoring refactor"
git stash pop >/dev/null
trap - EXIT   # stash is back, no further auto-restore needed

echo "==> Clearing outputs + pycache before Run 2"
rm -rf "${OUTPUT_DIR}" "${PROCESSED_DIR}"
find "${REPO_ROOT}/src" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true

echo "==> Run 2: refactored simulator"
( cd src && python main.py --ticker "${TICKER}" --date "${DATE}" --stage 1 --stage 2 )

# --- Diff --------------------------------------------------------------------
echo "==> Diffing"

OUTPUTS_DIFF="$(diff -r "${OUTPUT_DIR}"  "${BASELINE_DIR}/outputs"  || true)"
PROC_DIFF="$(diff -r "${PROCESSED_DIR}" "${BASELINE_DIR}/processed" || true)"

if [[ -z "${OUTPUTS_DIFF}" && -z "${PROC_DIFF}" ]]; then
  echo
  echo "    PASS — outputs byte-identical"
  echo "    baseline retained at: ${BASELINE_DIR}"
  exit 0
fi

echo
echo "    FAIL — outputs differ. Investigate before committing."
echo "---- data/outputs diff ----"
echo "${OUTPUTS_DIFF}"
echo "---- data/processed diff ----"
echo "${PROC_DIFF}"
echo
echo "    baseline retained at: ${BASELINE_DIR}"
exit 1
