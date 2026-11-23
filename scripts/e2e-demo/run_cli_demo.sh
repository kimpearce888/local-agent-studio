#!/usr/bin/env bash
# The end-to-end CLI demo: a human terminal user creates and solves a
# small sales-summary workflow offline (deterministic mock provider).
# Every step's terminal output is kept in demo-logs/.
#
#   ./run_cli_demo.sh            # from the repo root
#
set -u
cd "$(dirname "$0")/../.."      # repo root

DEMO=scripts/e2e-demo
LOGS=$DEMO/demo-logs
DATA=demo-data
rm -rf "$LOGS" "$DATA" demo-plan.json
mkdir -p "$LOGS"

LAS="python3 -m las"            # the installed entry point: `studio`
GOAL="$(cat $DEMO/goal.txt)"

step() {  # step <number> <label>
  echo
  echo "=== step $1: $2 ==="
}

step 1 "setup - no server available, so add the built-in offline provider"
env -u LAS_MOCK_SCRIPT $LAS providers add --name demo-mock --preset mock \
    --data-dir "$DATA" 2>&1 | tee "$LOGS/01-setup.txt"
env -u LAS_MOCK_SCRIPT $LAS providers --data-dir "$DATA" 2>&1 | tee "$LOGS/01-providers.txt"

step 2 "plan - the goal, with sales.csv attached"
export LAS_MOCK_SCRIPT="$PWD/$DEMO/plan-script.json"
$LAS plan "$GOAL" --provider demo-mock --attach "$DEMO/sales.csv" \
    --data-dir "$DATA" -o demo-plan.json 2>&1 | tee "$LOGS/02-plan.txt"

step 3 "run - t1 works, the run pauses at the approval gate"
export LAS_MOCK_SCRIPT="$PWD/$DEMO/run-script.json"
$LAS run demo-plan.json --provider demo-mock --data-dir "$DATA" 2>&1 \
    | tee "$LOGS/03-run-paused.txt"
RUN_ID=$(grep -oE "run-[0-9]{8}-[0-9]{6}-[0-9a-f]+" "$LOGS/03-run-paused.txt" | head -1)
echo "(run id: $RUN_ID)" | tee -a "$LOGS/03-run-paused.txt"

step 4 "list - the paused run is visible"
$LAS list --data-dir "$DATA" 2>&1 | tee "$LOGS/04-list.txt"

step 5 "approve the gate (spec 7.4: a reason is required)"
$LAS approve "$RUN_ID" t2 --reason "Totals verified against sales.csv \
(475 units, 4302.25 revenue); the public summary is cleared to write." \
    --data-dir "$DATA" 2>&1 | tee "$LOGS/05-approve.txt"

step 6 "resume - t2 writes summary.md, the run succeeds"
$LAS resume "$RUN_ID" --provider demo-mock --data-dir "$DATA" 2>&1 \
    | tee "$LOGS/06-resume.txt"

step 7 "inspect - the finished run, task by task"
$LAS inspect "$RUN_ID" --data-dir "$DATA" 2>&1 | tee "$LOGS/07-inspect.txt"

step 8 "the deliverable the workflow produced"
find "$DATA" -name "summary.md" -exec cat {} \; 2>&1 | tee "$LOGS/08-summary.txt"

echo
echo "=== demo done: logs in $LOGS, data dir: $DATA ==="
