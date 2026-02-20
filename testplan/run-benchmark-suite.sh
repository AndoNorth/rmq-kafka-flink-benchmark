#!/usr/bin/env bash

set -euo pipefail

# ── Paths ─────────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

LOG_FILE="$SCRIPT_DIR/run.log"

MONITORING_COMPOSE="$REPO_ROOT/docker-compose-monitoring.yml"
REDPANDA_COMPOSE="$REPO_ROOT/docker-compose-redpanda.yml"
RABBITMQ_COMPOSE="$REPO_ROOT/docker-compose-rabbitmq.yml"

PROM_URL="http://localhost:9090"

# ── Runner-side degradation thresholds (not in testplan.json) ────────────────
ERROR_RATE_THRESHOLD="0.05"   # abort when produce error rate exceeds this fraction
LATENCY_P99_THRESHOLD="1.0"  # abort when p99 E2E latency exceeds this (seconds)
LAG_RATIO_THRESHOLD="0.90"   # abort when consumers handle < this fraction of produced msgs

# ── Defaults ──────────────────────────────────────────────────────────────────
PLAN_FILE="$SCRIPT_DIR/testplan.json"
ONLY_TEST=""
NO_TEARDOWN=false
DRY_RUN=false

# ── Usage ─────────────────────────────────────────────────────────────────────
usage() {
    cat <<EOF
Usage: $(basename "$0") [options]

Options:
  --plan <file>    Path to testplan JSON (default: testplan.json)
  --only <name>    Run a single named test and exit
  --no-teardown    Keep the monitoring stack running after the suite finishes
  --dry-run        Print what would execute without starting any containers
  -h, --help       Show this help

Examples:
  ./run-benchmark-suite.sh
  ./run-benchmark-suite.sh --plan custom.json --no-teardown
  ./run-benchmark-suite.sh --only A1_RP
  ./run-benchmark-suite.sh --dry-run
EOF
    exit 0
}

# ── Arg parsing ───────────────────────────────────────────────────────────────
while [[ "$#" -gt 0 ]]; do
    case $1 in
        --plan)       shift; PLAN_FILE="$1" ;;
        --only)       shift; ONLY_TEST="$1" ;;
        --no-teardown) NO_TEARDOWN=true ;;
        --dry-run)    DRY_RUN=true ;;
        -h|--help)    usage ;;
        *) echo "Unknown argument: $1"; usage ;;
    esac
    shift
done

# ── Preflight checks ──────────────────────────────────────────────────────────
[[ -f "$PLAN_FILE" ]] || { echo "Plan file not found: $PLAN_FILE"; exit 1; }
command -v jq  >/dev/null || { echo "jq is required but not installed."; exit 1; }
command -v bc  >/dev/null || { echo "bc is required but not installed."; exit 1; }
command -v curl >/dev/null || { echo "curl is required but not installed."; exit 1; }

# ── Logging ───────────────────────────────────────────────────────────────────
log() {
    local ts
    ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "$ts  $*" | tee -a "$LOG_FILE"
}

log_sep() {
    echo "────────────────────────────────────────────────────────────" | tee -a "$LOG_FILE"
}

# ── Compose helpers ───────────────────────────────────────────────────────────
compose_file_for() {
    case "$1" in
        redpanda) echo "$REDPANDA_COMPOSE" ;;
        rabbitmq) echo "$RABBITMQ_COMPOSE" ;;
        *) log "ERROR: unknown broker '$1'"; exit 1 ;;
    esac
}

services_for() {
    # Sets PRODUCER_SVC and CONSUMER_SVC in caller scope
    PRODUCER_SVC="$1-producer"
    CONSUMER_SVC="$1-consumer"
}

# ── Stack lifecycle ───────────────────────────────────────────────────────────
start_monitoring() {
    if $DRY_RUN; then
        log "[dry-run] docker compose -f $MONITORING_COMPOSE up -d"
        return
    fi
    log "Starting monitoring stack..."
    docker compose -f "$MONITORING_COMPOSE" up -d

    log "Waiting for Prometheus to become ready..."
    local retries=30
    until curl -sf "$PROM_URL/-/ready" >/dev/null 2>&1; do
        [[ $retries -gt 0 ]] || { log "ERROR: Prometheus did not become ready in time."; exit 1; }
        sleep 2; (( retries-- ))
    done
    log "Monitoring stack ready."
}

stop_monitoring() {
    if $NO_TEARDOWN; then
        log "Monitoring stack left running (--no-teardown)."
        return
    fi
    if $DRY_RUN; then
        log "[dry-run] docker compose -f $MONITORING_COMPOSE down"
        return
    fi
    log "Stopping monitoring stack..."
    docker compose -f "$MONITORING_COMPOSE" down
}

bring_up() {
    local compose_file="$1" producer_svc="$2" consumer_svc="$3"
    local producers="$4" consumers="$5" msg_size="$6" rate_limit="$7"

    if $DRY_RUN; then
        log "[dry-run] MSG_SIZE_BYTES=$msg_size RATE_LIMIT=$rate_limit docker compose -f $compose_file up --build -d --scale $producer_svc=$producers --scale $consumer_svc=$consumers"
        return
    fi

    MSG_SIZE_BYTES="$msg_size" RATE_LIMIT="$rate_limit" \
        docker compose -f "$compose_file" up --build -d \
        --scale "$producer_svc=$producers" \
        --scale "$consumer_svc=$consumers"
}

tear_down() {
    local compose_file="$1"
    if $DRY_RUN; then
        log "[dry-run] docker compose -f $compose_file down -v"
        return
    fi
    docker compose -f "$compose_file" down -v
}

tear_down_all() {
    docker compose -f "$REDPANDA_COMPOSE" down -v 2>/dev/null || true
    docker compose -f "$RABBITMQ_COMPOSE" down -v 2>/dev/null || true
}

# ── Prometheus queries ────────────────────────────────────────────────────────
query_prom() {
    curl -sf "$PROM_URL/api/v1/query" \
        --data-urlencode "query=$1" \
        | jq -r '.data.result[0].value[1] // "0"'
}

current_throughput() {
    query_prom 'sum(rate(benchmark_messages_produced_total[1m]))'
}

current_error_rate() {
    # produce errors as a fraction of total attempted over the last minute
    query_prom 'sum(rate(benchmark_produce_errors_total[1m])) / (sum(rate(benchmark_messages_produced_total[1m])) + sum(rate(benchmark_produce_errors_total[1m])))'
}

current_latency_p99() {
    query_prom 'histogram_quantile(0.99, sum(rate(benchmark_e2e_latency_seconds_bucket[1m])) by (le))'
}

consume_to_produce_ratio() {
    # fraction of produced messages that consumers are keeping up with
    query_prom 'sum(rate(benchmark_messages_consumed_total[1m])) / sum(rate(benchmark_messages_produced_total[1m]))'
}

broker_is_healthy() {
    local container
    container=$(docker ps --filter "name=$1" --format "{{.Names}}" | head -n1)
    [[ -n "$container" ]]
}

# ── Test runners ──────────────────────────────────────────────────────────────
run_fixed_test() {
    local name="$1" broker="$2" producers="$3" consumers="$4"
    local msg_size="$5" rate_limit="$6" duration_min="$7" cooldown="$8"

    local compose_file; compose_file=$(compose_file_for "$broker")
    services_for "$broker"

    log_sep
    log "START  $name  broker=$broker producers=$producers consumers=$consumers msg_size=${msg_size}B rate=${rate_limit} duration=${duration_min}m"

    bring_up "$compose_file" "$PRODUCER_SVC" "$CONSUMER_SVC" \
        "$producers" "$consumers" "$msg_size" "$rate_limit"

    local duration_sec=$(( duration_min * 60 ))
    if $DRY_RUN; then
        log "[dry-run] sleep $duration_sec"
    else
        sleep "$duration_sec"
    fi

    tear_down "$compose_file"
    log "END    $name"

    if $DRY_RUN; then
        log "[dry-run] sleep $cooldown (cooldown)"
    else
        log "Cooldown ${cooldown}s..."
        sleep "$cooldown"
    fi
}

run_ramp_test() {
    local name="$1" broker="$2" consumers="$3"
    local initial_producers="$4" max_producers="$5" producer_step="$6"
    local step_duration="$7" msg_size="$8" rate_limit="$9"

    local compose_file; compose_file=$(compose_file_for "$broker")
    services_for "$broker"

    log_sep
    log "START  $name  broker=$broker consumers=$consumers producers=${initial_producers}→${max_producers} step=$producer_step step_dur=${step_duration}s"

    local producers=$initial_producers
    local previous_tp=0
    local step=0

    while [[ $producers -le $max_producers ]]; do
        log "RAMP   $name  step=$step producers=$producers"

        bring_up "$compose_file" "$PRODUCER_SVC" "$CONSUMER_SVC" \
            "$producers" "$consumers" "$msg_size" "$rate_limit"

        if $DRY_RUN; then
            log "[dry-run] sleep $step_duration  then evaluate saturation"
            producers=$(( producers + producer_step ))
            step=$(( step + 1 ))
            continue
        fi

        sleep "$step_duration"

        # --- Termination checks ---

        if ! broker_is_healthy "$broker"; then
            log "ABORT  $name  broker container gone at producers=$producers"
            break
        fi

        local current_tp error_rate latency_p99 lag_ratio
        current_tp=$(current_throughput)
        error_rate=$(current_error_rate)
        latency_p99=$(current_latency_p99)
        lag_ratio=$(consume_to_produce_ratio)
        log "RAMP   $name  throughput=$current_tp msg/s  error_rate=$error_rate  p99=${latency_p99}s  consume/produce=$lag_ratio"

        # Error rate spike
        if (( $(echo "$error_rate > $ERROR_RATE_THRESHOLD" | bc -l) )); then
            log "ABORT  $name  error rate $error_rate exceeded threshold $ERROR_RATE_THRESHOLD at producers=$producers"
            break
        fi

        # Latency breach — system is running but responses are unacceptable
        if (( $(echo "$latency_p99 > $LATENCY_P99_THRESHOLD" | bc -l) )); then
            log "ABORT  $name  p99 latency ${latency_p99}s exceeded threshold ${LATENCY_P99_THRESHOLD}s at producers=$producers"
            break
        fi

        # Consumer lag — consumers can't drain as fast as producers fill
        if (( $(echo "$lag_ratio < $LAG_RATIO_THRESHOLD" | bc -l) )); then
            log "ABORT  $name  consume/produce ratio $lag_ratio below threshold $LAG_RATIO_THRESHOLD at producers=$producers"
            break
        fi

        # Throughput regression — adding producers is actively hurting throughput
        if [[ "$previous_tp" != "0" ]]; then
            local growth
            growth=$(echo "scale=4; ($current_tp - $previous_tp) / $previous_tp * 100" | bc -l)
            log "RAMP   $name  throughput_growth=${growth}%"
            if (( $(echo "$growth < 0" | bc -l) )); then
                log "ABORT  $name  throughput regression ${growth}% at producers=$producers — system overloaded"
                break
            fi
        fi

        previous_tp=$current_tp
        producers=$(( producers + producer_step ))
        step=$(( step + 1 ))
    done

    tear_down "$compose_file"
    log "END    $name"
}

# ── Cleanup trap ──────────────────────────────────────────────────────────────
cleanup() {
    log "Caught signal — tearing down all stacks..."
    tear_down_all
    stop_monitoring
    exit 1
}
trap cleanup SIGINT SIGTERM

# ── Main ──────────────────────────────────────────────────────────────────────
log_sep
log "Suite starting  plan=$PLAN_FILE  dry_run=$DRY_RUN  no_teardown=$NO_TEARDOWN  only=${ONLY_TEST:-<all>}"

start_monitoring

test_count=$(jq '.tests | length' "$PLAN_FILE")

for (( i=0; i<test_count; i++ )); do
    t=$(jq ".tests[$i]" "$PLAN_FILE")
    name=$(jq -r '.name' <<< "$t")
    type=$(jq -r '.type' <<< "$t")

    if [[ -n "$ONLY_TEST" && "$name" != "$ONLY_TEST" ]]; then
        continue
    fi

    case "$type" in
        fixed)
            run_fixed_test \
                "$name" \
                "$(jq -r '.broker'           <<< "$t")" \
                "$(jq -r '.producers'        <<< "$t")" \
                "$(jq -r '.consumers'        <<< "$t")" \
                "$(jq -r '.msg_size_bytes'   <<< "$t")" \
                "$(jq -r '.rate_limit'       <<< "$t")" \
                "$(jq -r '.duration_minutes' <<< "$t")" \
                "$(jq -r '.cooldown_seconds' <<< "$t")"
            ;;
        ramp)
            run_ramp_test \
                "$name" \
                "$(jq -r '.broker'                <<< "$t")" \
                "$(jq -r '.consumers'             <<< "$t")" \
                "$(jq -r '.initial_producers'     <<< "$t")" \
                "$(jq -r '.max_producers'         <<< "$t")" \
                "$(jq -r '.producer_step'         <<< "$t")" \
                "$(jq -r '.step_duration_seconds' <<< "$t")" \
                "$(jq -r '.msg_size_bytes'        <<< "$t")" \
                "$(jq -r '.rate_limit'            <<< "$t")"
            ;;
        *)
            log "SKIP   $name  unknown type '$type'"
            ;;
    esac
done

log_sep
log "Suite complete."

stop_monitoring
