#!/usr/bin/env bash
set -Eeuo pipefail

: "${METRICS_ROOT:?METRICS_ROOT is required}"
: "${DEPLOY_BIN:?DEPLOY_BIN is required}"
: "${DEPLOY_REVISION:?DEPLOY_REVISION is required}"

BASELINE_SECONDS=${BASELINE_SECONDS:-60}
WARMUP_SECONDS=${WARMUP_SECONDS:-5}
RECOVERY_SECONDS=${RECOVERY_SECONDS:-15}
SAMPLE_INTERVAL=${SAMPLE_INTERVAL:-0.2}
RUN_ID=${GITHUB_RUN_ID:-local}-${GITHUB_RUN_ATTEMPT:-1}
CAPTURE="$METRICS_ROOT/runner-target"
PHASE_FILE="$METRICS_ROOT/phase"
STOP_FILE="$METRICS_ROOT/stop"
READY_FILE="$METRICS_ROOT/ready"
SAMPLER_PID=""
SAMPLING_ACTIVE=0

mkdir -p "$METRICS_ROOT" "$CAPTURE"
rm -f "$STOP_FILE" "$READY_FILE"
printf 'warmup\n' > "$PHASE_FILE"

finish_sampling() {
    if [[ "$SAMPLING_ACTIVE" != 1 ]]; then
        return
    fi
    touch "$STOP_FILE"
    for _ in $(seq 1 100); do
        kill -0 "$SAMPLER_PID" 2>/dev/null || break
        sleep 0.2
    done
    if kill -0 "$SAMPLER_PID" 2>/dev/null; then
        sudo -n kill -TERM "$SAMPLER_PID" 2>/dev/null || true
    fi
    wait "$SAMPLER_PID" 2>/dev/null || true
    SAMPLING_ACTIVE=0
}
trap finish_sampling EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

[[ "$(hostname -s)" == "experimental-vm" ]]
[[ "$(id -un)" == "mijon" ]]
[[ "$DEPLOY_REVISION" =~ ^[0-9a-f]{40}$ ]]
sudo -n true
for command_name in php composer python3 git curl tar /usr/bin/time; do
    command -v "$command_name" >/dev/null || {
        echo "Missing target command: $command_name" >&2
        exit 127
    }
done
php -r 'exit(version_compare(PHP_VERSION, "8.2.0", ">=") ? 0 : 1);'
php -r 'foreach (["pdo_sqlite", "mbstring", "openssl", "tokenizer", "ctype", "fileinfo"] as $extension) { if (!extension_loaded($extension)) { fwrite(STDERR, "Missing PHP extension: $extension\n"); exit(1); } }'
composer --version
python3 --version
git --version
"$DEPLOY_BIN" --version

# Normalize the target immediately before measurement so both methods use a
# warm Git mirror, Composer cache, shared state, and the exact same revision.
"$DEPLOY_BIN" -f deployer/deploy.yaml deploy runner \
    --revision="$DEPLOY_REVISION" \
    -o deployment_method=runner-precondition \
    -o deployment_run_id="$RUN_ID-precondition" \
    --no-interaction -q > "$METRICS_ROOT/precondition.log" 2>&1

bash scripts/metrics/inventory-linux.sh "$CAPTURE" pre-sampling
sudo -n python3 scripts/metrics/sample_linux.py \
    --output-dir "$CAPTURE" \
    --phase-file "$PHASE_FILE" \
    --stop-file "$STOP_FILE" \
    --ready-file "$READY_FILE" \
    --method runner \
    --host-role runner-target \
    --run-id "$RUN_ID" \
    --interval "$SAMPLE_INTERVAL" \
    --memory-interval 1.0 &
SAMPLER_PID=$!
SAMPLING_ACTIVE=1

for _ in $(seq 1 100); do
    [[ -s "$READY_FILE" ]] && kill -0 "$SAMPLER_PID" 2>/dev/null && break
    sleep 0.1
done
[[ -s "$READY_FILE" ]]
kill -0 "$SAMPLER_PID"

sleep "$WARMUP_SECONDS"
printf 'idle\n' > "$PHASE_FILE"
sleep "$BASELINE_SECONDS"

printf 'deploy\n' > "$PHASE_FILE"
set +e
/usr/bin/time -v -o "$METRICS_ROOT/deployer-time.txt" \
    "$DEPLOY_BIN" -f deployer/deploy.yaml deploy runner \
    --revision="$DEPLOY_REVISION" \
    -o deployment_method=runner \
    -o deployment_run_id="$RUN_ID" \
    --no-interaction -vvv 2>&1 | tee "$METRICS_ROOT/deployer.log"
DEPLOY_EXIT=${PIPESTATUS[0]}
set -e

printf 'recovery\n' > "$PHASE_FILE"
sleep "$RECOVERY_SECONDS"
finish_sampling
trap - EXIT INT TERM

sudo -n chown -R "$(id -u):$(id -g)" "$METRICS_ROOT"
python3 scripts/metrics/analyze.py \
    --input "$CAPTURE" \
    --output "$METRICS_ROOT/report" \
    --expected-method runner \
    --min-idle-seconds "$BASELINE_SECONDS"
printf '%s\n' "$DEPLOY_EXIT" > "$METRICS_ROOT/deploy-exit-code"

echo "Deployment exit code: $DEPLOY_EXIT"
echo "Metrics root: $METRICS_ROOT"
