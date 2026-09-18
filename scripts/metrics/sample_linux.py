#!/usr/bin/env python3
"""Low-overhead Linux process and host sampler for deployment benchmarks.

CPU comes from cumulative kernel counters in /proc. Memory comes from
/proc/<pid>/smaps_rollup so PSS is not double-counted across shared pages.
The raw CSV files are intentionally retained so every reported value can be
recalculated independently.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import platform
import re
import signal
import socket
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROCESS_FIELDS = [
    "timestamp_utc",
    "monotonic_ns",
    "elapsed_s",
    "phase",
    "method",
    "host_role",
    "hostname",
    "pid",
    "start_ticks",
    "ppid",
    "uid",
    "comm",
    "component",
    "cpu_ticks",
    "utime_ticks",
    "stime_ticks",
    "rss_stat_kib",
    "memory_sample",
    "rss_smaps_kib",
    "pss_kib",
    "uss_kib",
    "swap_pss_kib",
    "smaps_status",
]

SYSTEM_FIELDS = [
    "timestamp_utc",
    "monotonic_ns",
    "elapsed_s",
    "phase",
    "method",
    "host_role",
    "hostname",
    "cpu_total_ticks",
    "cpu_idle_ticks",
    "cpu_user_ticks",
    "cpu_system_ticks",
    "cpu_iowait_ticks",
    "cpu_steal_ticks",
    "mem_total_kib",
    "mem_available_kib",
    "mem_used_kib",
    "swap_total_kib",
    "swap_free_kib",
    "load_1m",
    "load_5m",
    "load_15m",
    "procs_running",
    "procs_blocked",
    "processes_created",
    "cgroup_cpu_usage_usec",
    "cgroup_memory_current_bytes",
    "cgroup_memory_peak_bytes",
    "memory_sample",
]

STOP_REQUESTED = False


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def request_stop(_signum: int, _frame: Any) -> None:
    global STOP_REQUESTED
    STOP_REQUESTED = True


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def read_phase(path: Path) -> str:
    try:
        value = read_text(path).strip()
    except OSError:
        value = "unknown"
    value = re.sub(r"[^a-zA-Z0-9_.-]+", "_", value)
    return value or "unknown"


def parse_cpu_list(value: str) -> int:
    count = 0
    for part in value.strip().split(","):
        if not part:
            continue
        if "-" in part:
            start, end = part.split("-", 1)
            count += int(end) - int(start) + 1
        else:
            count += 1
    return count


def effective_cpu_count() -> int:
    try:
        status = read_text(Path("/proc/self/status"))
        match = re.search(r"^Cpus_allowed_list:\s*(.+)$", status, re.MULTILINE)
        if match:
            return parse_cpu_list(match.group(1))
    except (OSError, ValueError):
        pass
    return os.cpu_count() or 1


def cgroup_directory() -> Path | None:
    try:
        for line in read_text(Path("/proc/self/cgroup")).splitlines():
            fields = line.split(":", 2)
            if len(fields) == 3 and fields[0] == "0":
                return Path("/sys/fs/cgroup") / fields[2].lstrip("/")
    except OSError:
        return None
    return None


def read_optional_int(path: Path | None) -> int | None:
    if path is None:
        return None
    try:
        value = read_text(path).strip()
        return None if value == "max" else int(value)
    except (OSError, ValueError):
        return None


def read_cgroup_metrics(directory: Path | None) -> dict[str, int | None]:
    result: dict[str, int | None] = {
        "cpu_usage_usec": None,
        "memory_current": None,
        "memory_peak": None,
    }
    if directory is None:
        return result
    try:
        for line in read_text(directory / "cpu.stat").splitlines():
            key, value = line.split(None, 1)
            if key == "usage_usec":
                result["cpu_usage_usec"] = int(value)
    except (OSError, ValueError):
        pass
    result["memory_current"] = read_optional_int(directory / "memory.current")
    result["memory_peak"] = read_optional_int(directory / "memory.peak")
    return result


def read_os_release() -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        for line in read_text(Path("/etc/os-release")).splitlines():
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key] = value.strip().strip('"')
    except OSError:
        pass
    return values


def cpu_model() -> str:
    try:
        for line in read_text(Path("/proc/cpuinfo")).splitlines():
            if line.lower().startswith("model name"):
                return line.split(":", 1)[1].strip()
    except (OSError, IndexError):
        pass
    return platform.processor() or "unknown"


def read_meminfo() -> dict[str, int]:
    values: dict[str, int] = {}
    try:
        for line in read_text(Path("/proc/meminfo")).splitlines():
            key, raw = line.split(":", 1)
            token = raw.strip().split()[0]
            values[key] = int(token)
    except (OSError, ValueError, IndexError):
        pass
    return values


def read_system_cpu() -> dict[str, int]:
    values: dict[str, int] = {}
    try:
        for line in read_text(Path("/proc/stat")).splitlines():
            if line.startswith("cpu "):
                fields = [int(value) for value in line.split()[1:]]
                fields += [0] * (8 - len(fields))
                # guest and guest_nice are already included in user/nice.
                values["user"] = fields[0] + fields[1]
                values["system"] = fields[2] + fields[5] + fields[6]
                values["idle"] = fields[3] + fields[4]
                values["iowait"] = fields[4]
                values["steal"] = fields[7]
                values["total"] = sum(fields[:8])
            elif line.startswith("processes "):
                values["processes"] = int(line.split()[1])
            elif line.startswith("procs_running "):
                values["procs_running"] = int(line.split()[1])
            elif line.startswith("procs_blocked "):
                values["procs_blocked"] = int(line.split()[1])
    except (OSError, ValueError, IndexError):
        pass
    return values


def parse_proc_stat(pid: int) -> dict[str, int | str]:
    raw = read_text(Path("/proc") / str(pid) / "stat")
    left = raw.find("(")
    right = raw.rfind(")")
    if left < 0 or right < left:
        raise ValueError("malformed /proc stat")
    comm = raw[left + 1 : right]
    fields = raw[right + 2 :].split()
    utime = int(fields[11])
    stime = int(fields[12])
    return {
        "comm": comm,
        "ppid": int(fields[1]),
        "utime": utime,
        "stime": stime,
        "cpu_ticks": utime + stime,
        "start_ticks": int(fields[19]),
        "rss_pages": max(0, int(fields[21])),
    }


def read_identity(pid: int) -> tuple[int | None, str]:
    uid: int | None = None
    try:
        status = read_text(Path("/proc") / str(pid) / "status")
        match = re.search(r"^Uid:\s*(\d+)", status, re.MULTILINE)
        if match:
            uid = int(match.group(1))
    except OSError:
        pass
    try:
        raw = (Path("/proc") / str(pid) / "cmdline").read_bytes()
        cmdline = raw.replace(b"\x00", b" ").decode("utf-8", errors="replace").strip()
    except OSError:
        cmdline = ""
    return uid, cmdline


def classify_process(comm: str, cmdline: str) -> str:
    name = comm.lower()
    haystack = f"{name} {cmdline.lower()}"
    if "sample_linux.py" in haystack:
        return "metrics_sampler"
    if any(token in haystack for token in ("runner.listener", "runner.worker", "runsvc.sh")):
        return "github_actions_runner"
    if "tailscaled" in haystack or re.search(r"(^|[/ ])tailscale( |$)", haystack):
        return "tailscale"
    if "deployer/deployer" in haystack or re.search(r"[/ ]dep( |$)", haystack):
        return "deployer"
    if "composer" in haystack:
        return "composer"
    if "artisan" in haystack and any(token in haystack for token in ("queue:", "horizon", "schedule:")):
        return "laravel_worker"
    if "artisan" in haystack:
        return "laravel_artisan"
    if "php-fpm" in haystack:
        return "php_fpm"
    if name in {"nginx", "apache2", "httpd", "caddy"}:
        return "web_server"
    if name in {"mysqld", "mariadbd", "postgres", "postmaster"}:
        return "database"
    if name in {"redis-server", "memcached"}:
        return "cache"
    if name in {"ssh", "sshd", "scp", "sftp-server"}:
        return "ssh"
    if name == "git" or re.search(r"(^|[/ ])git( |$)", haystack):
        return "git"
    if name in {"node", "npm", "npx", "vite"}:
        return "node"
    if name.startswith("php"):
        return "php_cli"
    if name in {"bash", "sh", "dash", "zsh", "sudo", "nohup", "sleep"}:
        return "shell"
    if name in {"systemd", "systemd-journal", "systemd-resolve", "cron", "dbus-daemon"}:
        return "system_service"
    return "other"


def read_smaps_rollup(pid: int) -> tuple[dict[str, int], str]:
    values: dict[str, int] = {}
    path = Path("/proc") / str(pid) / "smaps_rollup"
    try:
        for line in read_text(path).splitlines():
            if ":" not in line:
                continue
            key, raw = line.split(":", 1)
            token = raw.strip().split()
            if token and token[0].isdigit():
                values[key] = int(token[0])
        return values, "ok"
    except PermissionError:
        return values, "permission_denied"
    except FileNotFoundError:
        return values, "process_exited"
    except OSError as exc:
        return values, f"os_error_{exc.errno or 'unknown'}"


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temporary, path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--phase-file", required=True, type=Path)
    parser.add_argument("--stop-file", required=True, type=Path)
    parser.add_argument("--ready-file", required=True, type=Path)
    parser.add_argument("--method", required=True)
    parser.add_argument("--host-role", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--interval", type=float, default=0.2)
    parser.add_argument("--memory-interval", type=float, default=1.0)
    parser.add_argument("--max-duration", type=float, default=1800.0)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if not (0.05 <= args.interval <= 10):
        raise SystemExit("--interval must be between 0.05 and 10 seconds")
    if args.memory_interval < args.interval:
        raise SystemExit("--memory-interval must be >= --interval")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.ready_file.parent.mkdir(parents=True, exist_ok=True)
    args.ready_file.unlink(missing_ok=True)
    hostname = socket.gethostname()
    clk_tck = int(os.sysconf(os.sysconf_names["SC_CLK_TCK"]))
    page_kib = int(os.sysconf("SC_PAGE_SIZE")) // 1024
    start_monotonic_ns = time.monotonic_ns()
    try:
        start_boottime_s = time.clock_gettime(time.CLOCK_BOOTTIME)
    except (AttributeError, OSError):
        start_boottime_s = float(read_text(Path("/proc/uptime")).split()[0])
    cgroup = cgroup_directory()
    meminfo = read_meminfo()

    metadata: dict[str, Any] = {
        "schema_version": 2,
        "run_id": args.run_id,
        "method": args.method,
        "host_role": args.host_role,
        "hostname": hostname,
        "capture_start_utc": utc_now(),
        "capture_start_monotonic_ns": start_monotonic_ns,
        "capture_start_boottime_s": start_boottime_s,
        "interval_s": args.interval,
        "memory_interval_s": args.memory_interval,
        "max_duration_s": args.max_duration,
        "clock_ticks_per_second": clk_tck,
        "page_size_kib": page_kib,
        "logical_cpu_count": os.cpu_count() or 1,
        "effective_cpu_count": effective_cpu_count(),
        "cpu_model": cpu_model(),
        "memory_total_kib": meminfo.get("MemTotal"),
        "kernel": platform.release(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "os_release": read_os_release(),
        "boot_id": read_text(Path("/proc/sys/kernel/random/boot_id")).strip(),
        "cgroup_v2_path": str(cgroup) if cgroup else None,
        "sampler_uid": os.getuid(),
        "smaps_ok": 0,
        "smaps_permission_denied": 0,
        "smaps_process_exited": 0,
        "smaps_other_errors": 0,
        "process_stat_errors": 0,
        "samples": 0,
        "memory_samples": 0,
    }
    atomic_json(args.output_dir / "metadata.json", metadata)

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    identity_cache: dict[tuple[int, int], tuple[int | None, str]] = {}
    process_path = args.output_dir / "process_samples.csv"
    system_path = args.output_dir / "system_samples.csv"

    with process_path.open("w", newline="", encoding="utf-8") as process_file, system_path.open(
        "w", newline="", encoding="utf-8"
    ) as system_file:
        process_writer = csv.DictWriter(process_file, fieldnames=PROCESS_FIELDS)
        system_writer = csv.DictWriter(system_file, fieldnames=SYSTEM_FIELDS)
        process_writer.writeheader()
        system_writer.writeheader()
        process_file.flush()
        system_file.flush()
        args.ready_file.write_text(utc_now() + "\n", encoding="utf-8")

        next_tick = time.monotonic()
        next_memory = next_tick
        while True:
            now = time.monotonic()
            now_ns = time.monotonic_ns()
            elapsed = (now_ns - start_monotonic_ns) / 1_000_000_000
            timestamp = utc_now()
            phase = read_phase(args.phase_file)
            memory_due = now >= next_memory
            if memory_due:
                next_memory = now + args.memory_interval
                metadata["memory_samples"] += 1

            system_cpu = read_system_cpu()
            memory = read_meminfo()
            try:
                load_1m, load_5m, load_15m = os.getloadavg()
            except OSError:
                load_1m = load_5m = load_15m = math.nan
            cgroup_metrics = read_cgroup_metrics(cgroup)
            system_writer.writerow(
                {
                    "timestamp_utc": timestamp,
                    "monotonic_ns": now_ns,
                    "elapsed_s": f"{elapsed:.9f}",
                    "phase": phase,
                    "method": args.method,
                    "host_role": args.host_role,
                    "hostname": hostname,
                    "cpu_total_ticks": system_cpu.get("total", ""),
                    "cpu_idle_ticks": system_cpu.get("idle", ""),
                    "cpu_user_ticks": system_cpu.get("user", ""),
                    "cpu_system_ticks": system_cpu.get("system", ""),
                    "cpu_iowait_ticks": system_cpu.get("iowait", ""),
                    "cpu_steal_ticks": system_cpu.get("steal", ""),
                    "mem_total_kib": memory.get("MemTotal", ""),
                    "mem_available_kib": memory.get("MemAvailable", ""),
                    "mem_used_kib": (
                        memory.get("MemTotal", 0) - memory.get("MemAvailable", 0)
                        if "MemTotal" in memory and "MemAvailable" in memory
                        else ""
                    ),
                    "swap_total_kib": memory.get("SwapTotal", ""),
                    "swap_free_kib": memory.get("SwapFree", ""),
                    "load_1m": f"{load_1m:.4f}",
                    "load_5m": f"{load_5m:.4f}",
                    "load_15m": f"{load_15m:.4f}",
                    "procs_running": system_cpu.get("procs_running", ""),
                    "procs_blocked": system_cpu.get("procs_blocked", ""),
                    "processes_created": system_cpu.get("processes", ""),
                    "cgroup_cpu_usage_usec": cgroup_metrics["cpu_usage_usec"] or "",
                    "cgroup_memory_current_bytes": cgroup_metrics["memory_current"] or "",
                    "cgroup_memory_peak_bytes": cgroup_metrics["memory_peak"] or "",
                    "memory_sample": int(memory_due),
                }
            )

            for entry in sorted(Path("/proc").iterdir(), key=lambda item: int(item.name) if item.name.isdigit() else -1):
                if not entry.name.isdigit():
                    continue
                pid = int(entry.name)
                try:
                    proc = parse_proc_stat(pid)
                except (OSError, ValueError, IndexError):
                    metadata["process_stat_errors"] += 1
                    continue
                key = (pid, int(proc["start_ticks"]))
                if key not in identity_cache:
                    identity_cache[key] = read_identity(pid)
                uid, cmdline = identity_cache[key]
                component = classify_process(str(proc["comm"]), cmdline)

                smaps: dict[str, int] = {}
                smaps_status = "not_sampled"
                if memory_due:
                    smaps, smaps_status = read_smaps_rollup(pid)
                    if smaps_status == "ok":
                        metadata["smaps_ok"] += 1
                    elif smaps_status == "permission_denied":
                        metadata["smaps_permission_denied"] += 1
                    elif smaps_status == "process_exited":
                        metadata["smaps_process_exited"] += 1
                    else:
                        metadata["smaps_other_errors"] += 1
                private_kib = sum(
                    smaps.get(key_name, 0)
                    for key_name in ("Private_Clean", "Private_Dirty", "Private_Hugetlb")
                )
                process_writer.writerow(
                    {
                        "timestamp_utc": timestamp,
                        "monotonic_ns": now_ns,
                        "elapsed_s": f"{elapsed:.9f}",
                        "phase": phase,
                        "method": args.method,
                        "host_role": args.host_role,
                        "hostname": hostname,
                        "pid": pid,
                        "start_ticks": proc["start_ticks"],
                        "ppid": proc["ppid"],
                        "uid": "" if uid is None else uid,
                        "comm": proc["comm"],
                        "component": component,
                        "cpu_ticks": proc["cpu_ticks"],
                        "utime_ticks": proc["utime"],
                        "stime_ticks": proc["stime"],
                        "rss_stat_kib": int(proc["rss_pages"]) * page_kib,
                        "memory_sample": int(memory_due),
                        "rss_smaps_kib": smaps.get("Rss", "") if memory_due else "",
                        "pss_kib": smaps.get("Pss", "") if memory_due else "",
                        "uss_kib": private_kib if memory_due and smaps_status == "ok" else "",
                        "swap_pss_kib": smaps.get("SwapPss", "") if memory_due else "",
                        "smaps_status": smaps_status,
                    }
                )

            metadata["samples"] += 1
            if metadata["samples"] % 5 == 0:
                process_file.flush()
                system_file.flush()
            if STOP_REQUESTED or args.stop_file.exists() or elapsed >= args.max_duration:
                break
            next_tick += args.interval
            delay = next_tick - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            else:
                next_tick = time.monotonic()

    metadata["capture_end_utc"] = utc_now()
    metadata["duration_s"] = (time.monotonic_ns() - start_monotonic_ns) / 1_000_000_000
    metadata["stop_reason"] = (
        "signal" if STOP_REQUESTED else "stop_file" if args.stop_file.exists() else "max_duration"
    )
    atomic_json(args.output_dir / "metadata.json", metadata)
    return 0


if __name__ == "__main__":
    sys.exit(main())
