#!/usr/bin/env python3
"""Summarize raw deployment metrics without third-party dependencies."""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable


def number(value: str | None, cast: type = float) -> float | int | None:
    if value is None or value == "":
        return None
    try:
        return cast(value)
    except (TypeError, ValueError):
        return None


def percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(quantile * len(ordered)) - 1))
    return ordered[index]


def time_weighted_mean(points: list[tuple[float, float]]) -> float:
    if not points:
        return 0.0
    points = sorted(points)
    if len(points) == 1 or points[-1][0] <= points[0][0]:
        return statistics.fmean(value for _, value in points)
    area = 0.0
    for previous, current in zip(points, points[1:]):
        delta = current[0] - previous[0]
        if delta > 0:
            area += delta * (previous[1] + current[1]) / 2.0
    return area / (points[-1][0] - points[0][0])


def stats(values_by_time: list[tuple[float, float]], divisor: float = 1.0) -> dict[str, float]:
    values = [value / divisor for _, value in values_by_time]
    if not values:
        return {"mean": 0.0, "time_weighted_mean": 0.0, "median": 0.0, "p95": 0.0, "peak": 0.0}
    return {
        "mean": statistics.fmean(values),
        "time_weighted_mean": time_weighted_mean([(time_value, value / divisor) for time_value, value in values_by_time]),
        "median": statistics.median(values),
        "p95": percentile(values, 0.95),
        "peak": max(values),
    }


def find_captures(inputs: Iterable[Path]) -> list[Path]:
    captures: set[Path] = set()
    for entry in inputs:
        if (entry / "metadata.json").is_file() and (entry / "process_samples.csv").is_file():
            captures.add(entry.resolve())
            continue
        if entry.exists():
            for metadata in entry.rglob("metadata.json"):
                parent = metadata.parent
                if (parent / "process_samples.csv").is_file() and (parent / "system_samples.csv").is_file():
                    captures.add(parent.resolve())
    return sorted(captures)


def analyze_capture(directory: Path, min_idle_seconds: float) -> dict[str, Any]:
    metadata = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
    if metadata.get("schema_version") != 2:
        raise ValueError(f"{directory}: unsupported or incomplete sampler schema")
    if metadata.get("stop_reason") != "stop_file":
        raise ValueError(f"{directory}: sampler did not stop cleanly via stop file")
    if int(metadata.get("smaps_permission_denied") or 0) != 0:
        raise ValueError(f"{directory}: smaps_rollup permission failures make memory totals incomplete")
    clk_tck = int(metadata["clock_ticks_per_second"])
    cpu_count = int(metadata.get("effective_cpu_count") or metadata.get("logical_cpu_count") or 1)
    capture_boot = float(metadata["capture_start_boottime_s"])
    interval = float(metadata["interval_s"])

    system_rows: list[dict[str, str]] = []
    with (directory / "system_samples.csv").open(newline="", encoding="utf-8") as stream:
        system_rows = list(csv.DictReader(stream))
    if len(system_rows) < 3:
        raise ValueError(f"{directory}: too few system samples")
    elapsed_points = [float(row["elapsed_s"]) for row in system_rows]
    sample_gaps = [current - previous for previous, current in zip(elapsed_points, elapsed_points[1:])]
    max_sample_gap = max(sample_gaps, default=0.0)

    phase_bounds: dict[str, list[float]] = {}
    for row in system_rows:
        elapsed = float(row["elapsed_s"])
        phase_bounds.setdefault(row["phase"], [elapsed, elapsed])
        phase_bounds[row["phase"]][0] = min(phase_bounds[row["phase"]][0], elapsed)
        phase_bounds[row["phase"]][1] = max(phase_bounds[row["phase"]][1], elapsed)
    missing_phases = {"idle", "deploy"} - set(phase_bounds)
    if missing_phases:
        raise ValueError(f"{directory}: missing required phases {sorted(missing_phases)}")
    idle_duration = phase_bounds["idle"][1] - phase_bounds["idle"][0]
    idle_tolerance = max(1.0, 2.0 * max(interval, max_sample_gap))
    if idle_duration < max(0.0, min_idle_seconds - idle_tolerance):
        raise ValueError(
            f"{directory}: idle phase {idle_duration:.3f}s is shorter than requested "
            f"{min_idle_seconds:.3f}s beyond {idle_tolerance:.3f}s sampling tolerance"
        )
    if phase_bounds["deploy"][1] - phase_bounds["deploy"][0] <= 0:
        raise ValueError(f"{directory}: deploy phase has no measurable duration")

    system_cpu: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    previous_system: dict[str, str] | None = None
    host_memory: dict[str, list[tuple[float, float]]] = defaultdict(list)
    cgroup_memory: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for row in system_rows:
        phase = row["phase"]
        elapsed = float(row["elapsed_s"])
        used = number(row.get("mem_used_kib"))
        if used is not None:
            host_memory[phase].append((elapsed, float(used)))
        current = number(row.get("cgroup_memory_current_bytes"))
        if current is not None:
            cgroup_memory[phase].append((elapsed, float(current) / 1024.0))
        if previous_system is not None and previous_system["phase"] == phase:
            total_delta = int(row["cpu_total_ticks"]) - int(previous_system["cpu_total_ticks"])
            idle_delta = int(row["cpu_idle_ticks"]) - int(previous_system["cpu_idle_ticks"])
            if total_delta > 0 and 0 <= idle_delta <= total_delta:
                system_cpu[phase]["total_ticks"] += total_delta
                system_cpu[phase]["busy_ticks"] += total_delta - idle_delta
        previous_system = row

    component_cpu_ticks: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    memory_samples: dict[str, dict[int, dict[str, dict[str, float]]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(lambda: defaultdict(float)))
    )
    previous_process: dict[tuple[int, int], tuple[str, int, str]] = {}
    seen_process: set[tuple[int, int]] = set()
    process_rows = 0
    memory_rows = 0

    with (directory / "process_samples.csv").open(newline="", encoding="utf-8") as stream:
        for row in csv.DictReader(stream):
            process_rows += 1
            phase = row["phase"]
            elapsed = float(row["elapsed_s"])
            timestamp_ns = int(row["monotonic_ns"])
            pid_key = (int(row["pid"]), int(row["start_ticks"]))
            ticks = int(row["cpu_ticks"])
            component = row["component"]
            previous = previous_process.get(pid_key)
            if previous is not None and previous[0] == phase:
                delta = ticks - previous[1]
                if delta >= 0:
                    component_cpu_ticks[phase][component] += delta
            elif pid_key not in seen_process:
                phase_start = phase_bounds.get(phase, [elapsed, elapsed])[0]
                process_start_boot = pid_key[1] / clk_tck
                phase_start_boot = capture_boot + phase_start
                observation_boot = capture_boot + elapsed
                # A process first observed after it started within this phase can
                # contribute its full counter without importing pre-phase CPU.
                if phase_start_boot - interval <= process_start_boot <= observation_boot + interval:
                    component_cpu_ticks[phase][component] += ticks
            previous_process[pid_key] = (phase, ticks, component)
            seen_process.add(pid_key)

            if row.get("memory_sample") == "1":
                memory_rows += 1
                for field in ("rss_smaps_kib", "pss_kib", "uss_kib", "swap_pss_kib"):
                    value = number(row.get(field))
                    if value is not None:
                        memory_samples[phase][timestamp_ns][component][field] += float(value)

    phases: dict[str, Any] = {}
    for phase, bounds in sorted(phase_bounds.items(), key=lambda item: item[1][0]):
        duration = max(0.0, bounds[1] - bounds[0])
        cpu_data = system_cpu.get(phase, {})
        total_ticks = float(cpu_data.get("total_ticks", 0.0))
        busy_ticks = float(cpu_data.get("busy_ticks", 0.0))
        host_cpu_capacity = 100.0 * busy_ticks / total_ticks if total_ticks else 0.0
        host_busy_cores = busy_ticks / (clk_tck * duration) if duration > 0 else 0.0

        timestamps = sorted(memory_samples.get(phase, {}))
        components = set(component_cpu_ticks.get(phase, {}))
        for sample in memory_samples.get(phase, {}).values():
            components.update(sample)
        component_result: dict[str, Any] = {}
        all_process_points: dict[str, list[tuple[float, float]]] = {
            field: [] for field in ("rss_smaps_kib", "pss_kib", "uss_kib", "swap_pss_kib")
        }
        for timestamp_ns in timestamps:
            elapsed = (timestamp_ns - int(metadata["capture_start_monotonic_ns"])) / 1_000_000_000
            for field in all_process_points:
                total = sum(
                    values.get(field, 0.0)
                    for values in memory_samples[phase][timestamp_ns].values()
                )
                all_process_points[field].append((elapsed, total))

        for component in sorted(components):
            points: dict[str, list[tuple[float, float]]] = {
                field: [] for field in ("rss_smaps_kib", "pss_kib", "uss_kib", "swap_pss_kib")
            }
            for timestamp_ns in timestamps:
                elapsed = (timestamp_ns - int(metadata["capture_start_monotonic_ns"])) / 1_000_000_000
                values = memory_samples[phase][timestamp_ns].get(component, {})
                for field in points:
                    points[field].append((elapsed, values.get(field, 0.0)))
            ticks = component_cpu_ticks.get(phase, {}).get(component, 0.0)
            core_seconds = ticks / clk_tck
            component_result[component] = {
                "cpu_core_seconds": core_seconds,
                "avg_cpu_pct_one_core": 100.0 * core_seconds / duration if duration > 0 else 0.0,
                "avg_cpu_pct_host_capacity": 100.0 * core_seconds / (duration * cpu_count) if duration > 0 else 0.0,
                "rss_mib": stats(points["rss_smaps_kib"], 1024.0),
                "pss_mib": stats(points["pss_kib"], 1024.0),
                "uss_mib": stats(points["uss_kib"], 1024.0),
                "swap_pss_mib": stats(points["swap_pss_kib"], 1024.0),
            }

        phases[phase] = {
            "duration_s": duration,
            "host": {
                "avg_cpu_pct_capacity": host_cpu_capacity,
                "avg_busy_cores": host_busy_cores,
                "used_memory_mib": stats(host_memory.get(phase, []), 1024.0),
                "cgroup_memory_mib": stats(cgroup_memory.get(phase, []), 1024.0),
                "summed_process_pss_mib": stats(all_process_points["pss_kib"], 1024.0),
                "summed_process_rss_mib": stats(all_process_points["rss_smaps_kib"], 1024.0),
            },
            "components": component_result,
            "system_sample_count": sum(1 for row in system_rows if row["phase"] == phase),
            "memory_sample_count": len(timestamps),
        }

    for required_phase in ("idle", "deploy"):
        if phases[required_phase]["system_sample_count"] < 2:
            raise ValueError(f"{directory}: phase {required_phase} has too few CPU samples")
        if phases[required_phase]["memory_sample_count"] < 1:
            raise ValueError(f"{directory}: phase {required_phase} has no smaps_rollup sample")

    return {
        "capture_directory": str(directory),
        "run_id": metadata["run_id"],
        "method": metadata["method"],
        "host_role": metadata["host_role"],
        "hostname": metadata["hostname"],
        "environment": {
            "kernel": metadata.get("kernel"),
            "os_release": metadata.get("os_release"),
            "cpu_model": metadata.get("cpu_model"),
            "logical_cpu_count": metadata.get("logical_cpu_count"),
            "effective_cpu_count": metadata.get("effective_cpu_count"),
            "memory_total_mib": (metadata.get("memory_total_kib") or 0) / 1024.0,
            "cgroup_v2_path": metadata.get("cgroup_v2_path"),
        },
        "quality": {
            "sample_interval_s": metadata.get("interval_s"),
            "memory_interval_s": metadata.get("memory_interval_s"),
            "max_sample_gap_s": max_sample_gap,
            "sampler_duration_s": metadata.get("duration_s"),
            "process_rows": process_rows,
            "memory_rows": memory_rows,
            "smaps_ok": metadata.get("smaps_ok"),
            "smaps_permission_denied": metadata.get("smaps_permission_denied"),
            "smaps_process_exited": metadata.get("smaps_process_exited"),
            "smaps_other_errors": metadata.get("smaps_other_errors"),
            "process_stat_errors": metadata.get("process_stat_errors"),
            "cpu_note": "Per-process CPU uses kernel tick deltas; processes shorter than the sampling interval can be missed. Host CPU uses exact /proc/stat deltas.",
            "memory_note": "PSS/RSS/USS use smaps_rollup. Means are time-weighted; USS is Private_Clean + Private_Dirty + Private_Hugetlb.",
        },
        "phases": phases,
    }


def fmt(value: float) -> str:
    return f"{value:.2f}"


def render_markdown(captures: list[dict[str, Any]]) -> str:
    lines = [
        "# Deployment resource benchmark",
        "",
        "CPU component percentages are relative to one logical core and may exceed 100%. Host CPU is relative to total effective capacity. Memory means are time-weighted PSS from `/proc/<pid>/smaps_rollup`.",
        "",
    ]
    for capture in captures:
        env = capture["environment"]
        lines.extend(
            [
                f"## {capture['method']} — {capture['host_role']} ({capture['hostname']})",
                "",
                f"Environment: {env.get('os_release', {}).get('PRETTY_NAME', 'unknown')}; kernel {env.get('kernel')}; {env.get('effective_cpu_count')} effective CPUs; {fmt(env.get('memory_total_mib', 0.0))} MiB RAM.",
                "",
            ]
        )
        for phase, data in capture["phases"].items():
            host = data["host"]
            lines.extend(
                [
                    f"### Phase `{phase}` ({data['duration_s']:.2f} s)",
                    "",
                    f"Host average: **{host['avg_cpu_pct_capacity']:.2f}% CPU capacity** ({host['avg_busy_cores']:.3f} busy cores), **{host['used_memory_mib']['time_weighted_mean']:.2f} MiB used RAM**; summed process PSS **{host['summed_process_pss_mib']['time_weighted_mean']:.2f} MiB**.",
                    "",
                    "| Component | CPU avg (% one core) | CPU core-s | PSS avg MiB | PSS p95 MiB | PSS peak MiB | RSS avg MiB | USS avg MiB |",
                    "|---|---:|---:|---:|---:|---:|---:|---:|",
                ]
            )
            ordered = sorted(
                data["components"].items(),
                key=lambda item: (
                    item[1]["cpu_core_seconds"],
                    item[1]["pss_mib"]["time_weighted_mean"],
                ),
                reverse=True,
            )
            for component, values in ordered:
                lines.append(
                    "| {name} | {cpu:.2f} | {core:.3f} | {pss:.2f} | {p95:.2f} | {peak:.2f} | {rss:.2f} | {uss:.2f} |".format(
                        name=component,
                        cpu=values["avg_cpu_pct_one_core"],
                        core=values["cpu_core_seconds"],
                        pss=values["pss_mib"]["time_weighted_mean"],
                        p95=values["pss_mib"]["p95"],
                        peak=values["pss_mib"]["peak"],
                        rss=values["rss_mib"]["time_weighted_mean"],
                        uss=values["uss_mib"]["time_weighted_mean"],
                    )
                )
            lines.append("")
        quality = capture["quality"]
        lines.extend(
            [
                "Quality: interval {sample}s, maximum observed gap {gap:.3f}s, smaps interval {memory}s, {ok} successful smaps reads, {denied} permission failures, {exited} process-race misses.".format(
                    sample=quality["sample_interval_s"],
                    gap=quality["max_sample_gap_s"],
                    memory=quality["memory_interval_s"],
                    ok=quality["smaps_ok"],
                    denied=quality["smaps_permission_denied"],
                    exited=quality["smaps_process_exited"],
                ),
                "",
            ]
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", action="append", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--expected-method", required=True, choices=("tailscale", "runner"))
    parser.add_argument("--min-idle-seconds", required=True, type=float)
    args = parser.parse_args()
    captures = find_captures(args.input)
    if not captures:
        raise SystemExit("No metric captures found")
    try:
        results = [analyze_capture(capture, args.min_idle_seconds) for capture in captures]
    except (KeyError, ValueError) as exc:
        raise SystemExit(f"Invalid metric capture: {exc}") from exc

    expected_roles = {
        "tailscale": {"hosted-runner", "target"},
        "runner": {"runner-target"},
    }[args.expected_method]
    methods = {result["method"] for result in results}
    roles = {result["host_role"] for result in results}
    run_ids = {result["run_id"] for result in results}
    if methods != {args.expected_method}:
        raise SystemExit(f"Expected method {args.expected_method}, found {sorted(methods)}")
    if roles != expected_roles:
        raise SystemExit(f"Expected roles {sorted(expected_roles)}, found {sorted(roles)}")
    if len(results) != len(expected_roles):
        raise SystemExit("Duplicate or missing metric captures")
    if len(run_ids) != 1:
        raise SystemExit(f"Capture run IDs differ: {sorted(run_ids)}")

    args.output.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 2,
        "captures": results,
        "methodology": {
            "cpu": "Host: /proc/stat deltas. Components: summed /proc/PID/stat utime+stime deltas divided by CLK_TCK and phase duration.",
            "memory": "Time-weighted process-group PSS/RSS/USS from /proc/PID/smaps_rollup; host used memory is MemTotal-MemAvailable.",
        },
    }
    (args.output / "summary.json").write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    (args.output / "summary.md").write_text(render_markdown(results), encoding="utf-8")
    print(render_markdown(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
