# CPU and RAM benchmark methodology

## Scope

Both methods deploy one caller-supplied immutable Git SHA to `/home/mijon/apps/tailscaling` on `experimental-vm` through the same `deployer/deploy.yaml` recipe:

1. **Tailscale SSH:** a GitHub-hosted Ubuntu runner joins the tailnet, runs Deployer, and executes deployment tasks over SSH on `experimental-vm`.
2. **Self-hosted runner:** the GitHub Actions runner on `experimental-vm` runs Deployer against a local Deployer host.

The workflows share a concurrency group, so they cannot mutate the release path simultaneously. Each method performs one unmeasured deployment of the same SHA immediately before sampling. This normalizes the target to a warm Git mirror, Composer cache, release history, shared database, and application cache; reported numbers are therefore warm-state measurements, not cold first-deploy costs.

## Measurement windows

Each capture has explicit phases:

- `warmup`: sampler startup and cache stabilization; excluded from the requested idle average.
- `idle`: 60 seconds by default after the target has been preconditioned and all orchestration/Tailscale setup is ready.
- `deploy`: sample-observed window around the measured Deployer invocation.
- `recovery`: 15 seconds by default after deploy, retained to show how quickly resource use returns to baseline.

The phase file is read on every sample. The first/last observed points can differ from shell boundaries by approximately one process sample plus `/proc` scan time. The report records actual observed duration and maximum sample gap instead of claiming zero boundary error.

## CPU

- Host CPU is calculated from deltas of cumulative `/proc/stat` counters. This is the most reliable host-wide average for the observed interval.
- Per-process CPU is calculated from `utime + stime` in `/proc/<PID>/stat` using the kernel clock-tick frequency (`SC_CLK_TCK`). PIDs are paired with their process start tick to prevent PID-reuse errors.
- Component CPU is the sum of process deltas in a component, divided by observed phase duration. It is reported as core-seconds, percentage of one logical core, and percentage of effective host capacity.
- Sampling defaults to 200 ms. A process that starts and exits entirely between samples can be missed; `/usr/bin/time -v` around Deployer is retained as an independent controller-process cross-check.

## RAM

- Every second, the sampler reads `/proc/<PID>/smaps_rollup` with non-interactive sudo.
- **PSS** is the primary component metric because shared pages are divided proportionally instead of being counted once per process.
- **RSS** is retained for familiarity but must not be summed across workers without acknowledging double-counted shared pages.
- **USS** is calculated as `Private_Clean + Private_Dirty + Private_Hugetlb`.
- **SwapPSS** is recorded separately.
- Means are time-weighted using the trapezoidal rule. Median, p95, and peak are also retained.
- Host used RAM is independently calculated as `MemTotal - MemAvailable`. Cgroup v2 `memory.current`, `memory.peak`, and `cpu.stat` are captured when available.

## Components

Every visible process is sampled. Reporting groups identify: GitHub Actions runner, Tailscale, SSH, Deployer, Composer, Git, Laravel Artisan/workers, PHP CLI, PHP-FPM, web server, database, cache, Node.js, shells, system services, the sampler, and uncategorized processes. Raw rows retain PID, start time, parent PID, UID, command name, and component without storing full command lines that could contain secrets.

An inventory snapshot also preserves the complete `smaps_rollup` output for every recognized process immediately before sampling.

## Accuracy and completeness controls

- Samplers run as root only for `/proc` visibility; output is returned to the runner user afterward.
- A ready-file and process-liveness handshake must succeed before baseline sampling begins.
- The analyzer rejects missing `idle`/`deploy` phases, short idle duration, abnormal sampler termination, missing expected host roles, and any `smaps_rollup` permission failure.
- All timestamps include UTC wall time and monotonic nanoseconds.
- Tool versions, kernel, CPU model, CPU allowance, memory, cgroup path, running services, and filesystem are saved with each artifact.
- Raw CSV, metadata JSON, Deployer logs, `/usr/bin/time -v`, summary JSON, and summary Markdown are uploaded for independent audit.
- `smaps_rollup` process-exit races and other read errors are counted and shown in the report.

For stronger statistical confidence, dispatch each workflow at least three times with the same revision and no external load, alternate method order, and aggregate per-run phase means rather than pooling individual samples.
