#!/usr/bin/env bash
set -Eeuo pipefail

: "${METRICS_ROOT:?METRICS_ROOT is required}"
: "${SSH_TARGET:?SSH_TARGET is required}"
: "${REMOTE_METRICS_ROOT:?REMOTE_METRICS_ROOT is required}"
: "${DEPLOY_BIN:?DEPLOY_BIN is required}"
: "${DEPLOY_REVISION:?DEPLOY_REVISION is required}"

BASELINE_SECONDS=${BASELINE_SECONDS:-60}
WARMUP_SECONDS=${WARMUP_SECONDS:-5}
RECOVERY_SECONDS=${RECOVERY_SECONDS:-15}
SAMPLE_INTERVAL=${SAMPLE_INTERVAL:-0.2}
RUN_ID=${GITHUB_RUN_ID:-local}-${GITHUB_RUN_ATTEMPT:-1}
LOCAL_CAPTURE="$METRICS_ROOT/runner"
LOCAL_PHASE="$METRICS_ROOT/local.phase"
LOCAL_STOP="$METRICS_ROOT/local.stop"
LOCAL_READY="$METRICS_ROOT/local.ready"
REMOTE_PHASE="$REMOTE_METRICS_ROOT/phase"
REMOTE_STOP="$REMOTE_METRICS_ROOT/stop"
REMOTE_READY="$REMOTE_METRICS_ROOT/ready"
SSH=(ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15 "$SSH_TARGET")
SCP=(scp -q -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15)
LOCAL_ACTIVE=0
REMOTE_ACTIVE=0
LOCAL_SAMPLER_PID=""
REMOTE_CLEANUP=0

mkdir -p "$METRICS_ROOT" "$LOCAL_CAPTURE"
rm -f "$LOCAL_STOP" "$LOCAL_READY"
printf 'warmup\n' > "$LOCAL_PHASE"

finish_sampling() {
    if [[ "$LOCAL_ACTIVE" == 1 ]]; then
        touch "$LOCAL_STOP"
        for _ in $(seq 1 100); do
            kill -0 "$LOCAL_SAMPLER_PID" 2>/dev/null || break
            sleep 0.2
        done
        if kill -0 "$LOCAL_SAMPLER_PID" 2>/dev/null; then
            sudo -n kill -TERM "$LOCAL_SAMPLER_PID" 2>/dev/null || true
        fi
        wait "$LOCAL_SAMPLER_PID" 2>/dev/null || true
        LOCAL_ACTIVE=0
    fi
    if [[ "$REMOTE_ACTIVE" == 1 ]]; then
        "${SSH[@]}" "touch '$REMOTE_STOP'; pid=\$(cat '$REMOTE_METRICS_ROOT/sampler.pid' 2>/dev/null || true); if [ -n \"\$pid\" ]; then for i in \$(seq 1 100); do sudo -n kill -0 \"\$pid\" 2>/dev/null || exit 0; sleep 0.2; done; sudo -n kill -TERM \"\$pid\" 2>/dev/null || true; fi" >/dev/null 2>&1 || true
        REMOTE_ACTIVE=0
    fi
}
cleanup() {
    finish_sampling
    if [[ "$REMOTE_CLEANUP" == 1 ]]; then
        "${SSH[@]}" "rm -rf '$REMOTE_METRICS_ROOT'" >/dev/null 2>&1 || true
    fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

[[ "$DEPLOY_REVISION" =~ ^[0-9a-f]{40}$ ]]
sudo -n true
command -v php python3 git curl tar /usr/bin/time >/dev/null
"${SSH[@]}" "set -e; test \"\$(hostname -s)\" = experimental-vm; test \"\$(id -un)\" = mijon; sudo -n true; command -v php composer python3 git curl tar /usr/bin/time >/dev/null; php -r 'exit(version_compare(PHP_VERSION, \"8.2.0\", \">=\") ? 0 : 1);'; php -r 'foreach ([\"pdo_sqlite\", \"mbstring\", \"openssl\", \"tokenizer\", \"ctype\", \"fileinfo\"] as \$extension) { if (!extension_loaded(\$extension)) { fwrite(STDERR, \"Missing PHP extension: \$extension\\n\"); exit(1); } }'; composer --version; git --version"
"$DEPLOY_BIN" --version

# Use a matching warm state before the measured invocation.
"$DEPLOY_BIN" -f deployer/deploy.yaml deploy tailscale \
    --revision="$DEPLOY_REVISION" \
    -o deployment_method=tailscale-precondition \
    -o deployment_run_id="$RUN_ID-precondition" \
    --no-interaction -q > "$METRICS_ROOT/precondition.log" 2>&1

bash scripts/metrics/inventory-linux.sh "$LOCAL_CAPTURE" pre-sampling
"${SSH[@]}" "mkdir -p '$REMOTE_METRICS_ROOT'"
REMOTE_CLEANUP=1
"${SCP[@]}" scripts/metrics/sample_linux.py scripts/metrics/inventory-linux.sh "$SSH_TARGET:$REMOTE_METRICS_ROOT/"
"${SSH[@]}" "bash '$REMOTE_METRICS_ROOT/inventory-linux.sh' '$REMOTE_METRICS_ROOT/target' pre-sampling"

sudo -n python3 scripts/metrics/sample_linux.py \
    --output-dir "$LOCAL_CAPTURE" \
    --phase-file "$LOCAL_PHASE" \
    --stop-file "$LOCAL_STOP" \
    --ready-file "$LOCAL_READY" \
    --method tailscale \
    --host-role hosted-runner \
    --run-id "$RUN_ID" \
    --interval "$SAMPLE_INTERVAL" \
    --memory-interval 1.0 &
LOCAL_SAMPLER_PID=$!
LOCAL_ACTIVE=1

"${SSH[@]}" "printf 'warmup\\n' > '$REMOTE_PHASE'; rm -f '$REMOTE_STOP' '$REMOTE_READY'; sudo -n nohup python3 '$REMOTE_METRICS_ROOT/sample_linux.py' --output-dir '$REMOTE_METRICS_ROOT/target' --phase-file '$REMOTE_PHASE' --stop-file '$REMOTE_STOP' --ready-file '$REMOTE_READY' --method tailscale --host-role target --run-id '$RUN_ID' --interval '$SAMPLE_INTERVAL' --memory-interval 1.0 > '$REMOTE_METRICS_ROOT/sampler.log' 2>&1 < /dev/null & echo \$! > '$REMOTE_METRICS_ROOT/sampler.pid'"
REMOTE_ACTIVE=1

for _ in $(seq 1 100); do
    [[ -s "$LOCAL_READY" ]] && kill -0 "$LOCAL_SAMPLER_PID" 2>/dev/null && break
    sleep 0.1
done
[[ -s "$LOCAL_READY" ]]
kill -0 "$LOCAL_SAMPLER_PID"
"${SSH[@]}" "for i in \$(seq 1 100); do pid=\$(cat '$REMOTE_METRICS_ROOT/sampler.pid' 2>/dev/null || true); test -s '$REMOTE_READY' && sudo -n kill -0 \"\$pid\" 2>/dev/null && exit 0; sleep 0.1; done; exit 1"

sleep "$WARMUP_SECONDS"
printf 'idle\n' > "$LOCAL_PHASE"
"${SSH[@]}" "printf 'idle\\n' > '$REMOTE_PHASE'"
sleep "$BASELINE_SECONDS"

printf 'deploy\n' > "$LOCAL_PHASE"
"${SSH[@]}" "printf 'deploy\\n' > '$REMOTE_PHASE'"
set +e
/usr/bin/time -v -o "$METRICS_ROOT/deployer-time.txt" \
    "$DEPLOY_BIN" -f deployer/deploy.yaml deploy tailscale \
    --revision="$DEPLOY_REVISION" \
    -o deployment_method=tailscale \
    -o deployment_run_id="$RUN_ID" \
    --no-interaction -vvv 2>&1 | tee "$METRICS_ROOT/deployer.log"
DEPLOY_EXIT=${PIPESTATUS[0]}
set -e

printf 'recovery\n' > "$LOCAL_PHASE"
"${SSH[@]}" "printf 'recovery\\n' > '$REMOTE_PHASE'" || true
sleep "$RECOVERY_SECONDS"
finish_sampling

sudo -n chown -R "$(id -u):$(id -g)" "$METRICS_ROOT"
"${SSH[@]}" "sudo -n chown -R \"\$(id -u):\$(id -g)\" '$REMOTE_METRICS_ROOT'"
mkdir -p "$METRICS_ROOT/target"
"${SCP[@]}" -r "$SSH_TARGET:$REMOTE_METRICS_ROOT/target/." "$METRICS_ROOT/target/"
"${SCP[@]}" "$SSH_TARGET:$REMOTE_METRICS_ROOT/sampler.log" "$METRICS_ROOT/target/sampler-wrapper.log" || true
python3 scripts/metrics/analyze.py \
    --input "$LOCAL_CAPTURE" \
    --input "$METRICS_ROOT/target" \
    --output "$METRICS_ROOT/report" \
    --expected-method tailscale \
    --min-idle-seconds "$BASELINE_SECONDS"
printf '%s\n' "$DEPLOY_EXIT" > "$METRICS_ROOT/deploy-exit-code"
"${SSH[@]}" "rm -rf '$REMOTE_METRICS_ROOT'" || true
REMOTE_CLEANUP=0
trap - EXIT INT TERM

echo "Deployment exit code: $DEPLOY_EXIT"
echo "Metrics root: $METRICS_ROOT"
