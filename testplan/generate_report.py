#!/usr/bin/env python3
"""
generate_report.py — Regenerate a benchmark report skeleton from:
  • testplan.json   — test configurations and structure
  • run.log         — actual start/end timestamps (from the last real suite run)
  • Prometheus HTTP API — per-window metrics (throughput, latency, CPU, memory)

Usage:
    python3 generate_report.py [options]

Options:
    --testplan FILE   testplan.json path       [default: <script-dir>/testplan.json]
    --log FILE        run.log path             [default: <script-dir>/run.log]
    --prom URL        Prometheus base URL      [default: http://localhost:9090]
    --output FILE     Output report path       [default: <script-dir>/REPORT.md]
    --step DURATION   Prometheus range step    [default: 60s]
    --no-metrics      Skip Prometheus, emit placeholders only
"""

import argparse
import json
import re
import sys
import urllib.request
import urllib.parse
from datetime import timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.resolve()

# ── Prometheus queries ────────────────────────────────────────────────────────
# Each value is a PromQL template; {broker} and {step} are substituted per test.
# Results are scalars:  throughput → msg/s,  latency → ms,  cpu → core-%,  mem → MB

QUERIES = {
    "throughput_msg_s": (
        'sum(rate(benchmark_messages_produced_total{{broker="{broker}"}}[{step}]))'
    ),
    "e2e_p50_ms": (
        'histogram_quantile(0.50,'
        ' sum(rate(benchmark_e2e_latency_seconds_bucket{{broker="{broker}"}}[{step}])) by (le))'
        ' * 1000'
    ),
    "e2e_p95_ms": (
        'histogram_quantile(0.95,'
        ' sum(rate(benchmark_e2e_latency_seconds_bucket{{broker="{broker}"}}[{step}])) by (le))'
        ' * 1000'
    ),
    "e2e_p99_ms": (
        'histogram_quantile(0.99,'
        ' sum(rate(benchmark_e2e_latency_seconds_bucket{{broker="{broker}"}}[{step}])) by (le))'
        ' * 1000'
    ),
    "ack_p99_ms": (
        'histogram_quantile(0.99,'
        ' sum(rate(benchmark_produce_ack_latency_seconds_bucket{{broker="{broker}"}}[{step}])) by (le))'
        ' * 1000'
    ),
    "broker_cpu_pct": (
        'sum(rate(container_cpu_usage_seconds_total'
        '{{container_label_com_docker_compose_service="{broker_svc}"}}[{step}])) * 100'
    ),
    "broker_mem_mb": (
        'sum(container_memory_working_set_bytes'
        '{{container_label_com_docker_compose_service="{broker_svc}"}}) / (1024*1024)'
    ),
    "prod_cpu_pct": (
        'sum(rate(container_cpu_usage_seconds_total'
        '{{container_label_com_docker_compose_service="{broker}-producer"}}[{step}])) * 100'
    ),
    "cons_cpu_pct": (
        'sum(rate(container_cpu_usage_seconds_total'
        '{{container_label_com_docker_compose_service="{broker}-consumer"}}[{step}])) * 100'
    ),
    "prod_mem_mb": (
        'sum(container_memory_working_set_bytes'
        '{{container_label_com_docker_compose_service="{broker}-producer"}}) / (1024*1024)'
    ),
    "cons_mem_mb": (
        'sum(container_memory_working_set_bytes'
        '{{container_label_com_docker_compose_service="{broker}-consumer"}}) / (1024*1024)'
    ),
    "errors_s": (
        'sum(rate(benchmark_produce_errors_total{{broker="{broker}"}}[{step}]))'
    ),
}

# ── Prometheus helpers ────────────────────────────────────────────────────────

def prom_query_range(prom_url: str, promql: str, start: str, end: str, step: str):
    """Query Prometheus range API; return average across all series × all time steps."""
    params = urllib.parse.urlencode({"query": promql, "start": start, "end": end, "step": step})
    url = f"{prom_url}/api/v1/query_range?{params}"
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            data = json.load(r)
    except Exception as exc:
        print(f"  WARN: Prometheus query failed ({exc}): {promql[:80]}", file=sys.stderr)
        return None

    if data.get("status") != "success":
        return None
    results = data["data"]["result"]
    if not results:
        return None

    total, count = 0.0, 0
    for series in results:
        for _ts, val in series["values"]:
            try:
                v = float(val)
                if v == v:  # skip NaN
                    total += v
                    count += 1
            except (ValueError, TypeError):
                pass
    return total / count if count > 0 else None


def fetch_metrics(prom_url: str, broker: str, start: str, end: str, step: str) -> dict:
    """Fetch all QUERIES for one test window. Returns dict of metric→float|None."""
    # broker_svc: the cAdvisor container_label service name for the broker container
    broker_svc = broker  # "redpanda" or "rabbitmq"

    result = {}
    for key, template in QUERIES.items():
        promql = template.format(broker=broker, broker_svc=broker_svc, step=step)
        result[key] = prom_query_range(prom_url, promql, start, end, step)
    return result


# ── run.log parsing ───────────────────────────────────────────────────────────

def parse_run_log(log_path: str) -> dict:
    """
    Find the last non-dry-run suite in run.log and extract:
      tests:       {name → {start, end, broker, producers, consumers, ...}}
      ramp_steps:  {name → [{producers, throughput, p99_s, error_rate, consume_produce}, ...]}
      aborts:      {name → {reason, at_producers}}
      suite_start, suite_end timestamps
    """
    with open(log_path, "r", encoding="utf-8") as fh:
        lines = fh.readlines()

    # Locate all non-dry-run suite boundaries
    suite_starts = []
    for idx, line in enumerate(lines):
        m = re.search(r"Suite starting.*dry_run=(true|false)", line)
        if m and m.group(1) == "false":
            ts_m = re.match(r"(\S+)\s+Suite starting", line)
            if ts_m:
                suite_starts.append((idx, ts_m.group(1)))

    if not suite_starts:
        print("WARNING: No non-dry-run suite found in log. Timestamps will be missing.",
              file=sys.stderr)
        return {"tests": {}, "ramp_steps": {}, "aborts": {},
                "suite_start": None, "suite_end": None}

    # Use the last (most recent) suite
    last_idx, suite_ts = suite_starts[-1]
    suite_lines = lines[last_idx:]

    tests: dict = {}
    ramp_steps: dict = {}
    aborts: dict = {}
    suite_end_ts = None
    # Track current producer count per ramp test across consecutive log lines
    ramp_current_producers: dict = {}

    for line in suite_lines:
        line = line.rstrip()

        # Suite complete
        m = re.match(r"(\S+)\s+Suite complete", line)
        if m:
            suite_end_ts = m.group(1)
            continue

        # START  <name>  key=val ...
        m = re.match(r"(\S+)\s+START\s+(\S+)\s+(.*)", line)
        if m:
            ts, name, rest = m.group(1), m.group(2), m.group(3)
            info: dict = {"start": ts, "end": None}
            for kv in re.finditer(r"(\w+)=([^\s]+)", rest):
                info[kv.group(1)] = kv.group(2)
            tests[name] = info
            continue

        # END    <name>
        m = re.match(r"(\S+)\s+END\s+(\S+)", line)
        if m:
            ts, name = m.group(1), m.group(2)
            if name in tests:
                tests[name]["end"] = ts
            continue

        # RAMP step announcement:  RAMP  <name>  step=N producers=N
        m = re.match(r"\S+\s+RAMP\s+(\S+)\s+step=\d+\s+producers=(\d+)", line)
        if m:
            ramp_current_producers[m.group(1)] = int(m.group(2))
            continue

        # RAMP throughput result:  RAMP  <name>  throughput=... msg/s  error_rate=...  p99=...s
        m = re.match(r"(\S+)\s+RAMP\s+(\S+)\s+throughput=([\d.]+)", line)
        if m:
            name = m.group(2)
            step: dict = {"producers": ramp_current_producers.get(name, "?")}
            for kv in re.finditer(r"([\w/]+)=([\S]+)", line):
                step[kv.group(1)] = kv.group(2)
            step["throughput_msg_s"] = float(step.pop("throughput", 0))
            if "p99" in step:
                step["p99_s"] = float(step.pop("p99").rstrip("s"))
            ramp_steps.setdefault(name, []).append(step)
            continue

        # ABORT  <name>  reason...
        m = re.match(r"(\S+)\s+ABORT\s+(\S+)\s+(.*)", line)
        if m:
            ts, name, reason = m.group(1), m.group(2), m.group(3)
            aborts[name] = {"ts": ts, "reason": reason}
            prod_m = re.search(r"at producers=(\d+)", reason)
            if prod_m:
                aborts[name]["at_producers"] = int(prod_m.group(1))
            continue

    return {
        "suite_start": suite_ts,
        "suite_end": suite_end_ts,
        "tests": tests,
        "ramp_steps": ramp_steps,
        "aborts": aborts,
    }


# ── testplan helpers ──────────────────────────────────────────────────────────

def load_testplan(path: str) -> list:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)["tests"]


def group_name(test_name: str) -> str:
    """Extract logical group prefix: A1_RP → A, SIZE1K_RMQ → SIZE, RAMP_RP → RAMP."""
    m = re.match(r"([A-Z]+)", test_name)
    return m.group(1) if m else test_name


def broker_tag(broker: str) -> str:
    return "RP" if broker == "redpanda" else "RMQ"


# ── Formatting helpers ────────────────────────────────────────────────────────

def fmt(value, unit="", decimals=0, scale=1.0, na="—"):
    if value is None:
        return na
    v = value * scale
    if decimals == 0:
        return f"{v:,.0f}{unit}"
    return f"{v:,.{decimals}f}{unit}"


def fmt_ms(value_ms):
    """Format a latency value: <1000 ms → ms, ≥1000 ms → seconds."""
    if value_ms is None:
        return "—"
    if abs(value_ms) < 1000:
        return f"{value_ms:.0f} ms"
    return f"{value_ms/1000:.1f} s"


def fmt_throughput(msg_s):
    if msg_s is None:
        return "—"
    if msg_s >= 1000:
        return f"{msg_s/1000:.1f} K"
    return f"{msg_s:.0f}"


# ── Report sections ───────────────────────────────────────────────────────────

def section_header(title: str, level: int = 2) -> str:
    prefix = "#" * level
    return f"\n{prefix} {title}\n"


def build_throughput_table(group_tests: list, metrics: dict, testplan_map: dict) -> str:
    rows = []
    for t in group_tests:
        name = t["name"]
        broker = t["broker"]
        m = metrics.get(name, {})
        tp = fmt_throughput(m.get("throughput_msg_s"))
        rows.append(
            f"| {name} | {t.get('producers','?')}p:{t.get('consumers','?')}c"
            f" | {broker.capitalize()} | {tp} |"
        )
    header = (
        "| Test | Config | Broker | Avg msg/s |\n"
        "|------|--------|--------|-----------:|"
    )
    return header + "\n" + "\n".join(rows)


def build_latency_table(group_tests: list, metrics: dict) -> str:
    rows = []
    for t in group_tests:
        name = t["name"]
        m = metrics.get(name, {})
        rows.append(
            f"| {name} | {t['broker'].capitalize()}"
            f" | {fmt_ms(m.get('e2e_p50_ms'))}"
            f" | {fmt_ms(m.get('e2e_p95_ms'))}"
            f" | {fmt_ms(m.get('e2e_p99_ms'))}"
            f" | {fmt_ms(m.get('ack_p99_ms'))} |"
        )
    header = (
        "| Test | Broker | E2E P50 | E2E P95 | E2E P99 | Ack P99 |\n"
        "|------|--------|--------:|--------:|--------:|--------:|"
    )
    return header + "\n" + "\n".join(rows)


def build_cpu_table(all_tests: list, metrics: dict) -> str:
    rows = []
    for t in all_tests:
        name = t["name"]
        m = metrics.get(name, {})
        rows.append(
            f"| {name} | {t.get('producers','?')}p:{t.get('consumers','?')}c"
            f" | {t['broker'].capitalize()}"
            f" | {fmt(m.get('broker_cpu_pct'), '%', 0)}"
            f" | {fmt(m.get('prod_cpu_pct'),   '%', 0)}"
            f" | {fmt(m.get('cons_cpu_pct'),   '%', 0)} |"
        )
    header = (
        "| Test | Config | Broker | Broker CPU | Prod CPU (total) | Cons CPU (total) |\n"
        "|------|--------|--------|----------:|-----------------:|-----------------:|"
    )
    return header + "\n" + "\n".join(rows)


def build_memory_table(all_tests: list, metrics: dict) -> str:
    rows = []
    for t in all_tests:
        name = t["name"]
        m = metrics.get(name, {})
        rows.append(
            f"| {name} | {t.get('producers','?')}p:{t.get('consumers','?')}c"
            f" | {t['broker'].capitalize()}"
            f" | {fmt(m.get('broker_mem_mb'), ' MB', 0)}"
            f" | {fmt(m.get('prod_mem_mb'),   ' MB', 0)}"
            f" | {fmt(m.get('cons_mem_mb'),   ' MB', 0)} |"
        )
    header = (
        "| Test | Config | Broker | Broker Mem | Prod Mem (total) | Cons Mem (total) |\n"
        "|------|--------|--------|----------:|-----------------:|-----------------:|"
    )
    return header + "\n" + "\n".join(rows)


def build_ramp_table(ramp_steps: list, abort: dict | None, test_name: str) -> str:
    if not ramp_steps:
        return "_No ramp step data found in run.log._"
    rows = []
    for step in ramp_steps:
        producers = step.get("producers", "?")
        tp = step.get("throughput_msg_s", None)
        p99_s = step.get("p99_s", None)
        tp_str = f"{tp/1000:.1f} K" if tp is not None else "—"
        p99_str = f"{p99_s*1000:.0f} ms" if (p99_s is not None and p99_s < 10) else (
            f"{p99_s:.2f} s" if p99_s is not None else "—"
        )
        result = "OK"
        try:
            prod_int = int(producers)
        except (TypeError, ValueError):
            prod_int = None
        if abort and prod_int is not None and abort.get("at_producers") == prod_int:
            result = f"**ABORT** — {abort['reason']}"
        rows.append(f"| {producers} | {tp_str} | {p99_str} | {result} |")
    header = (
        "| Producers | Throughput | P99 Latency | Result |\n"
        "|----------:|-----------:|------------:|--------|"
    )
    return header + "\n" + "\n".join(rows)


# ── Main report builder ───────────────────────────────────────────────────────

def build_report(testplan: list, log_data: dict, metrics: dict, prom_url: str) -> str:
    suite_start = log_data.get("suite_start", "unknown")
    suite_end = log_data.get("suite_end", "unknown")
    tests_map = {t["name"]: t for t in testplan}

    # Group tests by prefix
    groups: dict = {}
    for t in testplan:
        g = group_name(t["name"])
        groups.setdefault(g, []).append(t)

    lines = []

    # ── Header ────────────────────────────────────────────────────────────────
    lines.append("# Benchmark Report — Redpanda vs RabbitMQ SuperStreams\n")
    lines.append(f"**Suite start:** {suite_start}  ")
    lines.append(f"**Suite end:**   {suite_end}  ")
    lines.append(f"**Prometheus:**  {prom_url}  ")
    lines.append("")
    lines.append("> _Auto-generated skeleton — add commentary and screenshots below each table._")
    lines.append("")
    lines.append("---")

    # ── Environment ───────────────────────────────────────────────────────────
    lines.append(section_header("Environment"))
    lines.append("| Parameter | Default | Notes |")
    lines.append("|-----------|---------|-------|")
    lines.append("| Broker CPU limit   | 2.0 cores | `BROKER_CPUS` |")
    lines.append("| Broker memory      | 2 GB      | `BROKER_MEMORY` |")
    lines.append("| Producer CPU       | 0.5 cores each | `PRODUCER_CPUS` |")
    lines.append("| Producer memory    | 256 MB each    | `PRODUCER_MEMORY` |")
    lines.append("| Consumer CPU       | 0.5 cores each | `CONSUMER_CPUS` |")
    lines.append("| Consumer memory    | 256 MB each    | `CONSUMER_MEMORY` |")
    lines.append("| Partitions         | 8              | `PARTITIONS` |")
    lines.append("")
    lines.append(
        "> CPU figures use **core-% units**: 100% = 1 core fully utilised. "
        "Broker budget = 200% (2 cores)."
    )
    lines.append("")

    # ── Throughput ────────────────────────────────────────────────────────────
    lines.append(section_header("1. Throughput"))
    lines.append(
        "> All values are **averages over the test window**. "
        "Rate-limited tests may read lower than target due to startup averaging."
    )

    group_order = []
    seen = set()
    for t in testplan:
        g = group_name(t["name"])
        if g not in seen:
            group_order.append(g)
            seen.add(g)

    subsection = 1
    for g in group_order:
        g_tests = groups[g]
        sample = g_tests[0]

        # Determine group description
        if g in ("A", "B", "C"):
            desc = {
                "A": "Symmetric scaling (N producers : N consumers)",
                "B": "Producer-heavy (N producers : 1 consumer)",
                "C": "Consumer-heavy (1 producer : N consumers)",
            }[g]
            msg_size = sample.get("msg_size_bytes", 256)
            rate = sample.get("rate_limit", 0)
            rate_str = f", rate={rate} msg/s/producer" if rate else ", unlimited rate"
            lines.append(section_header(
                f"1.{subsection} Group {g} — {desc}, {msg_size} B{rate_str}", level=3
            ))
        elif g == "SIZE":
            lines.append(section_header(
                f"1.{subsection} Message Size Variation (2p:2c, unlimited rate)", level=3
            ))
        elif g == "RATE":
            lines.append(section_header(
                f"1.{subsection} Rate-Limited Tests (2p:2c, 256 B)", level=3
            ))
        elif g == "SOAK":
            lines.append(section_header(
                f"1.{subsection} Soak Tests (60 minutes)", level=3
            ))
        elif g == "RAMP":
            lines.append(section_header(
                f"1.{subsection} Ramp-Up Tests (producers step {sample.get('initial_producers','?')}→{sample.get('max_producers','?')})", level=3
            ))
        else:
            lines.append(section_header(f"1.{subsection} {g}", level=3))
        subsection += 1

        if g == "RAMP":
            # Ramp gets its own step table from log data, not Prometheus averages
            for t in g_tests:
                name = t["name"]
                broker = t["broker"]
                cfg = (
                    f"consumers={t.get('consumers','?')}, "
                    f"step={t.get('producer_step','?')}, "
                    f"step_duration={t.get('step_duration_seconds','?')}s"
                )
                lines.append(f"**{name}** ({broker}, {cfg})\n")
                lines.append(build_ramp_table(
                    log_data["ramp_steps"].get(name, []),
                    log_data["aborts"].get(name),
                    name,
                ))
                lines.append("")
        elif g == "SIZE":
            # Size tests — show msg_size in table
            rows = []
            for t in g_tests:
                name = t["name"]
                m = metrics.get(name, {})
                rows.append(
                    f"| {name} | {t['msg_size_bytes']} B | {t['broker'].capitalize()}"
                    f" | {fmt_throughput(m.get('throughput_msg_s'))} |"
                )
            lines.append(
                "| Test | Msg Size | Broker | Avg msg/s |\n"
                "|------|----------|--------|-----------:|"
            )
            lines += rows
            lines.append("")
        elif g == "RATE":
            # Rate tests — show target vs achieved
            rows = []
            for t in g_tests:
                name = t["name"]
                m = metrics.get(name, {})
                target = t.get("rate_limit", 0) * t.get("producers", 1)
                achieved = m.get("throughput_msg_s")
                eff = f"{achieved/target*100:.0f}%" if (achieved and target) else "—"
                rows.append(
                    f"| {name} | {target:,} | {t['broker'].capitalize()}"
                    f" | {fmt_throughput(achieved)} | {eff} |"
                )
            lines.append(
                "| Test | Target total | Broker | Achieved msg/s | Efficiency |\n"
                "|------|-------------|--------|---------------:|-----------:|"
            )
            lines += rows
            lines.append("")
        else:
            lines.append(build_throughput_table(g_tests, metrics, tests_map))
            lines.append("")

        lines.append("> _TODO: add observations_")
        lines.append("")

    # ── E2E Latency ───────────────────────────────────────────────────────────
    lines.append(section_header("2. End-to-End Latency"))
    lines.append(
        "> Latency is measured from the timestamp embedded in each message at produce time "
        "to receipt at the consumer. Values are time-averaged quantiles over each test window."
    )
    lines.append("")
    lines.append(
        "> **Note:** High P50/P99 values in the B-series (producer-heavy) and large-message "
        "tests indicate queue backlog growth, not single-message pipeline latency."
    )

    subsection = 1
    for g in group_order:
        g_tests = groups[g]
        if g == "RAMP":
            # Already captured in ramp step table above
            continue
        lines.append(section_header(f"2.{subsection} Group {g}", level=3))
        subsection += 1
        lines.append(build_latency_table(g_tests, metrics))
        lines.append("")
        lines.append("> _TODO: add observations_")
        lines.append("")

    # ── CPU ───────────────────────────────────────────────────────────────────
    lines.append(section_header("3. CPU Usage"))
    lines.append(
        "> 100% = 1 core. Broker budget = 200% (2 cores). "
        "Producer/consumer values are **totals across all instances**."
    )
    lines.append("")

    # Split fixed tests from ramp/soak for readability
    fixed_tests = [t for t in testplan if t["type"] == "fixed"]
    lines.append(build_cpu_table(fixed_tests, metrics))
    lines.append("")
    lines.append("> _TODO: add observations_")
    lines.append("")

    # ── Memory ────────────────────────────────────────────────────────────────
    lines.append(section_header("4. Memory Usage"))
    lines.append(
        "> Working set memory (resident, excludes file cache). "
        "Producer/consumer values are **totals across all instances**."
    )
    lines.append("")
    lines.append(build_memory_table(fixed_tests, metrics))
    lines.append("")
    lines.append("> _TODO: add observations_")
    lines.append("")

    # ── Conclusions ───────────────────────────────────────────────────────────
    lines.append(section_header("5. Conclusions"))
    lines.append(section_header("5.1 Throughput Summary", level=3))
    lines.append("| Scenario | Winner | Notes |")
    lines.append("|----------|--------|-------|")
    for g in group_order:
        if g in ("RAMP",):
            continue
        lines.append(f"| Group {g} | _TODO_ | _TODO_ |")
    lines.append("")

    lines.append(section_header("5.2 Latency Summary", level=3))
    lines.append("> _TODO: summarise key latency findings per scenario_")
    lines.append("")

    lines.append(section_header("5.3 Resource Efficiency", level=3))
    lines.append("> _TODO: summarise CPU and memory observations_")
    lines.append("")

    lines.append(section_header("5.4 Recommendations", level=3))
    lines.append("| Use Case | Recommendation |")
    lines.append("|----------|---------------|")
    lines.append("| High-throughput streaming | _TODO_ |")
    lines.append("| Low-latency workloads     | _TODO_ |")
    lines.append("| Large-message workloads   | _TODO_ |")
    lines.append("| Long soak / sustained     | _TODO_ |")
    lines.append("")

    lines.append(section_header("5.5 Open Questions / Follow-ups", level=3))
    lines.append("- [ ] _TODO_")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(
        "_Report generated by `generate_report.py` from Prometheus metrics "
        f"({prom_url}) and `run.log`. "
        "Values are averages over each test window. Replace `_TODO_` sections with your analysis._"
    )

    return "\n".join(lines) + "\n"


# ── CLI ───────────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--testplan",   default=str(SCRIPT_DIR / "testplan.json"),
                   help="Path to testplan.json")
    p.add_argument("--log",        default=str(SCRIPT_DIR / "run.log"),
                   help="Path to run.log")
    p.add_argument("--prom",       default="http://localhost:9090",
                   help="Prometheus base URL")
    p.add_argument("--output",     default=str(SCRIPT_DIR / "REPORT.md"),
                   help="Output report path")
    p.add_argument("--step",       default="60s",
                   help="Prometheus range step (e.g. 30s, 60s)")
    p.add_argument("--no-metrics", action="store_true",
                   help="Skip Prometheus queries; emit placeholder values only")
    return p.parse_args()


def main():
    args = parse_args()

    print(f"Loading testplan: {args.testplan}", file=sys.stderr)
    testplan = load_testplan(args.testplan)
    print(f"  {len(testplan)} tests loaded.", file=sys.stderr)

    print(f"Parsing run.log: {args.log}", file=sys.stderr)
    log_data = parse_run_log(args.log)
    n_windows = sum(
        1 for t in log_data["tests"].values()
        if t.get("start") and t.get("end")
    )
    print(f"  {n_windows} complete test windows found.", file=sys.stderr)

    # Check Prometheus availability
    metrics: dict = {}
    if args.no_metrics:
        print("Skipping Prometheus queries (--no-metrics).", file=sys.stderr)
    else:
        try:
            with urllib.request.urlopen(f"{args.prom}/-/ready", timeout=5) as r:
                pass
            print(f"Querying Prometheus: {args.prom}", file=sys.stderr)
        except Exception as exc:
            print(f"WARNING: Prometheus not reachable ({exc}). "
                  "Using placeholder values. Pass --no-metrics to suppress this.",
                  file=sys.stderr)
            args.no_metrics = True

    if not args.no_metrics:
        for t in testplan:
            name = t["name"]
            window = log_data["tests"].get(name, {})
            start = window.get("start")
            end = window.get("end")
            broker = t.get("broker", window.get("broker", ""))

            if not (start and end and broker):
                print(f"  SKIP {name}: missing window or broker info", file=sys.stderr)
                metrics[name] = {}
                continue

            print(f"  {name} [{start} → {end}]", file=sys.stderr)
            metrics[name] = fetch_metrics(args.prom, broker, start, end, args.step)

    print(f"Generating report: {args.output}", file=sys.stderr)
    report = build_report(testplan, log_data, metrics, args.prom)

    with open(args.output, "w", encoding="utf-8") as fh:
        fh.write(report)

    print(f"Done. Report written to {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
