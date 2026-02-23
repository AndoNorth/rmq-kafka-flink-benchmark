# Benchmark Test Plan

This directory defines the test plan and runner for the PoC benchmark suite. The runner executes a sequence of test cases against the Redpanda or RabbitMQ stacks, with predictable time windows that allow Grafana to be reviewed manually after the run completes.

## Directory structure

```
testplan/
├── README.md               # this file
├── testplan.json           # default test plan (authoritative format)
├── run-benchmark-suite.sh  # test runner script
├── generate_report.py      # report generator (Prometheus + run.log → REPORT.md)
└── REPORT.md               # generated benchmark report (not committed)
```

---

## Test plan format

The test plan is a JSON file with a top-level `tests` array. Each entry is either a **fixed** test or a **ramp** test, distinguished by the `type` field.

### Fixed test

Runs a static topology for a set duration, then tears down and waits for a cooldown before the next test.

```json
{
  "name": "A1_RP",
  "type": "fixed",
  "broker": "redpanda",
  "producers": 1,
  "consumers": 1,
  "msg_size_bytes": 256,
  "rate_limit": 0,
  "duration_minutes": 15,
  "cooldown_seconds": 60
}
```

| Field | Description |
|---|---|
| `name` | Short label used in logs and Grafana annotations. Must be unique. |
| `type` | `"fixed"` |
| `broker` | `"redpanda"` or `"rabbitmq"` |
| `producers` | Number of producer replicas to scale |
| `consumers` | Number of consumer replicas to scale |
| `msg_size_bytes` | Payload size in bytes — maps to `MSG_SIZE_BYTES` in `.env` |
| `rate_limit` | Messages/sec per producer (`0` = unlimited) — maps to `RATE_LIMIT` |
| `duration_minutes` | How long to hold the topology before tearing down |
| `cooldown_seconds` | Downtime between this test and the next (default: `60`) |

### Ramp test

Incrementally scales producers step by step with consumers held fixed. There is no fixed duration — the runner evaluates termination after each step by querying Prometheus. Saturation detection thresholds and failure conditions are configured in the runner, not the test plan.

```json
{
  "name": "RAMP_RP",
  "type": "ramp",
  "broker": "redpanda",
  "consumers": 4,
  "initial_producers": 1,
  "max_producers": 64,
  "producer_step": 2,
  "step_duration_seconds": 180,
  "msg_size_bytes": 256,
  "rate_limit": 0
}
```

| Field | Description |
|---|---|
| `name` | Short label used in logs. Must be unique. |
| `type` | `"ramp"` |
| `broker` | `"redpanda"` or `"rabbitmq"` |
| `consumers` | Fixed number of consumer replicas held constant throughout the ramp |
| `initial_producers` | Number of producers to start with |
| `max_producers` | Hard upper bound — ramp stops here regardless of saturation state |
| `producer_step` | How many producers to add at each step |
| `step_duration_seconds` | Seconds to hold each step before the runner queries Prometheus |
| `msg_size_bytes` | Payload size in bytes |
| `rate_limit` | Messages/sec per producer (`0` = unlimited) |

The runner is responsible for deciding when to stop the ramp. After each `step_duration_seconds` interval it queries Prometheus and evaluates four degradation signals (in order): error rate spike, p99 latency breach, consumer lag accumulation, and throughput regression. The ramp also stops if the broker container disappears. These thresholds live in the runner script, not in the test plan.

---

## How the runner works

```
startup
  └── docker compose -f docker-compose-monitoring.yml up -d
      └── wait for Prometheus to be healthy
      └── docker compose -f docker-compose-cadvisor-{standard,wsl2}.yml up -d
              (variant auto-selected from `docker info --format '{{.Driver}}'`)

for each test in testplan.json
  ├── docker compose up --build -d --scale producers=N --scale consumers=M
  │       MSG_SIZE_BYTES and RATE_LIMIT injected as inline env vars (`.env` is not modified)
  │
  ├── [fixed]  sleep duration_minutes × 60
  │            docker compose down -v
  │            sleep cooldown_seconds
  │
  └── [ramp]   loop:
                 bring_up (N producers, fixed consumers)
                 sleep step_duration_seconds          ← observation window
                 query Prometheus snapshot:
                   throughput, error rate, p99 latency, consume/produce ratio
                 abort if broker gone
                 abort if error rate > threshold
                 abort if p99 latency > threshold
                 abort if consume/produce ratio < threshold
                 abort if throughput regression (current < previous)
                 scale up producers by producer_step, repeat
               docker compose down -v

shutdown (optional --no-teardown flag keeps monitoring and cAdvisor up)
  └── docker compose -f docker-compose-cadvisor-{variant}.yml down
  └── docker compose -f docker-compose-monitoring.yml down
```

The runner logs a timestamped entry to `run.log` at the start and end of every test, giving you exact time windows to use in Grafana.

---

## Grafana observation guide

After the run completes, open Grafana at `http://localhost:3000` and use the time picker to zoom into each test window. The `run.log` file (written alongside `run-benchmark-suite.sh`) contains the exact start/end timestamps:

```
2026-02-20T10:00:00Z  START  A1_RP   redpanda  producers=2 consumers=2 msg_size=256 rate=0
2026-02-20T10:15:00Z  END    A1_RP
2026-02-20T10:16:00Z  START  A1_RMQ  rabbitmq  producers=2 consumers=2 msg_size=256 rate=0
...
```

For **fixed tests**, each window is `duration_minutes` wide with a `cooldown_seconds` gap — the gap appears as a flat zero on the charts, making boundaries easy to find.

For **ramp tests**, the runner stops at the first degradation signal. Step boundaries appear as staircase jumps in the throughput panel. The interesting area is where throughput stops climbing, latency starts rising, or the consume/produce ratio dips — that is the system's true saturation point, visible in the charts even after the runner has stopped.

Key panels to review per test window:

| Panel | What to look for |
|---|---|
| Throughput (msg/s) | Absolute rate, compare brokers side by side |
| E2E latency (p50/p99) | Latency under load, especially at saturation |
| Produce ack latency | Producer-side backpressure signal |
| Broker CPU / Memory | Whether the broker or the clients are the bottleneck |
| Consumer lag | Whether consumers keep up with producers |

---

## Running the suite

```bash
# From the repo root:
cd testplan

# Run the full default plan
./run-benchmark-suite.sh

# Run with a custom plan file
./run-benchmark-suite.sh --plan my-testplan.json

# Keep monitoring stack running after the suite finishes (recommended)
./run-benchmark-suite.sh --no-teardown

# Run a single named test only
./run-benchmark-suite.sh --only A1_RP

# Dry run — print what would execute without starting containers
./run-benchmark-suite.sh --dry-run
```

Logs are written to `testplan/run.log` and appended on each invocation.

---

## Generating a report

After the suite finishes, run `generate_report.py` to produce a `REPORT.md` skeleton populated with real metrics from Prometheus. The script cross-correlates the exact test windows from `run.log` with Prometheus range queries, so the numbers reflect what actually ran rather than the testplan parameters.

```bash
# From the testplan/ directory (uses defaults: testplan.json, run.log, localhost:9090)
python3 generate_report.py

# Explicit paths / remote Prometheus
python3 generate_report.py \
  --testplan testplan.json \
  --log      run.log \
  --prom     http://localhost:9090 \
  --output   REPORT.md

# Skip Prometheus — emit skeleton with placeholder (—) values from log data only
python3 generate_report.py --no-metrics
```

**Options**

| Flag | Default | Description |
|------|---------|-------------|
| `--testplan FILE` | `testplan.json` | Test plan used to determine group structure and configurations |
| `--log FILE` | `run.log` | Runner log; the last non-dry-run suite is used for timestamps |
| `--prom URL` | `http://localhost:9090` | Prometheus base URL |
| `--output FILE` | `REPORT.md` | Where to write the report (overwritten if it exists) |
| `--step DURATION` | `60s` | Prometheus range step (e.g. `30s`, `60s`) |
| `--no-metrics` | — | Skip all Prometheus queries; all metric cells show `—` |

**What gets generated**

The report skeleton contains:
- Suite start/end timestamps from `run.log`
- Throughput tables per test group (A/B/C, SIZE, RATE, SOAK)
- Ramp step tables directly from the `run.log` step results and abort reasons
- End-to-end latency tables (P50/P95/P99 + produce ack P99) per group
- Broker, producer, and consumer CPU and memory tables
- Conclusions and recommendations scaffolding with `_TODO_` placeholders

Each section has a `> _TODO: add observations_` prompt for you to fill in with screenshots and analysis. Re-running the script overwrites the file, so add your notes in a separate document or after the run has completed and Prometheus data is still live.

**Requirements:** Python 3.9+, no external dependencies. Prometheus must be reachable (use `--no-metrics` if it is not).

---

## Adding or modifying tests

1. Copy an existing entry in `testplan.json` and change the `name` and parameters.
2. Keep names short and unique — they appear in log lines and are useful as Grafana search terms.
3. Use the naming convention below: group letter + number + `_RP`/`_RMQ` suffix. e.g. `A1_RP`, `B1_RMQ`, `RAMP_RP`.
4. Resource limits (CPU/memory caps on broker and client containers) come from `.env` and are **not** overridden per test — edit `.env` before the run if you want to change them, then document which `.env` was in effect in your notes.

---

## Naming convention

Names follow the pattern `<GROUP><N>_<BROKER>` (e.g. `A1_RP`, `B1_RMQ`). Broker suffix is always `_RP` (Redpanda) or `_RMQ` (RabbitMQ). `RAMP_*` tests omit the number.

| Group | Meaning |
|---|---|
| `A*` | Symmetric — equal producers and consumers |
| `B*` | Fan-in — more producers than consumers |
| `C*` | Fan-out — more consumers than producers |
| `SIZE*` | Variable message size (baseline topology) |
| `RATE*` | Rate-limited (baseline topology) |
| `RAMP_*` | Horizontal producer ramp, fixed consumers |
