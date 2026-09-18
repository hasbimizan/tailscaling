# Laravel deployment benchmark: Tailscale SSH vs self-hosted runner

This repository contains a Laravel 12 starter and two reproducible deployment paths to the same Linux VM (`experimental-vm`). Both paths execute the single Deployer recipe at [`deployer/deploy.yaml`](deployer/deploy.yaml), precondition the same target state, and deploy a caller-supplied immutable Git SHA to `/home/mijon/apps/tailscaling`.

## Layout

- `application/` — Laravel skeleton 12.12.2 with Laravel Framework 12.69.2 pinned in `composer.lock`; dependency resolution targets PHP 8.2.
- `deployer/deploy.yaml` — shared zero-downtime release recipe, SQLite persistence, production environment reconciliation, migrations, cache generation, and pre-publish verification.
- `.github/workflows/test.yaml` — GitHub-hosted runner → Tailscale 1.82.0 → SSH → `experimental-vm`.
- `.github/workflows/runners.yaml` — self-hosted GitHub runner on `experimental-vm` → local Deployer host.
- `scripts/metrics/` — raw `/proc` sampler, complete `smaps_rollup` snapshots, analyzer, and workflow harnesses.
- `docs/benchmark-methodology.md` — definitions, formulas, phase boundaries, component scope, and limitations.
- [`docs/benchmark-results-2026-09-18.md`](docs/benchmark-results-2026-09-18.md) — measured 3×3 comparison, all process components, raw `smaps_rollup` examples, and deployment verification evidence.

## Required target state

`experimental-vm` must have:

- Linux x64, PHP >= 8.2, Composer 2, Git, Python 3, `curl`, `tar`, and `/usr/bin/time`;
- PHP extensions `pdo_sqlite`, `mbstring`, `openssl`, `tokenizer`, `ctype`, and `fileinfo`;
- user `mijon` with write access to `/home/mijon/apps`;
- passwordless non-interactive sudo for the metrics reader (`sudo -n true`);
- the self-hosted Actions runner registered with the custom label `experimental-vm` and running as `mijon`;
- Tailscale SSH policy allowing `tag:github-actions` to connect as `mijon`.

Repository Actions secrets:

- `TS_OAUTH_CLIENT_ID`
- `TS_OAUTH_SECRET`

If the CLI runtime is not installed yet, run the idempotent provisioning workflow once. It uses the distribution's APT packages and does not configure a web server:

```powershell
gh workflow run "Provision deployment target" --ref main
```

The recipe persists `.env`, `storage`, and `database/database.sqlite` between releases. Every deployment reconciles production-safe non-secret environment values, protects `.env` with mode `0600`, generates a Laravel key if missing, migrates SQLite, verifies the release, then atomically updates `current`. It does **not** install or reconfigure Nginx/Caddy/PHP-FPM; point an existing web server at `/home/mijon/apps/tailscaling/current/public` and configure runtime ownership separately if HTTP service is required.

## Run the comparison

Both workflows are manual, restricted to a workflow dispatch from `main`, and require the same exact 40-character commit SHA. After pushing, capture it once and pass it to both runs:

```powershell
$revision = git rev-parse origin/main
gh workflow run "Deploy via Tailscale SSH" --ref main -f revision=$revision -f baseline_seconds=60 -f recovery_seconds=15 -f sample_interval=0.2
gh workflow run "Deploy via Self-hosted Runner" --ref main -f revision=$revision -f baseline_seconds=60 -f recovery_seconds=15 -f sample_interval=0.2
```

The shared concurrency group serializes both runs. Immediately before each measured capture, the workflow performs one unmeasured deployment of the same SHA so the Git mirror, Composer cache, release state, and application cache are warm for both methods. Each artifact includes raw process/system CSV, environment inventory, full pre-sampling `smaps_rollup` files, Deployer output, `/usr/bin/time -v`, `summary.json`, and `summary.md`.

Both workflows download Deployer 7.5.12 as a standalone PHAR and verify SHA-256 `b55c6609653e888c672d327c407f8bba6324b9c9cc24f9dcfb3f4b3922760632`; the self-hosted path therefore does not warm the application's Composer cache merely to obtain the orchestrator.

## Local validation

```powershell
composer validate --working-dir=application --strict
composer install --working-dir=application --no-interaction
Push-Location application
php artisan test --compact
php vendor/bin/dep -f ../deployer/deploy.yaml tree deploy
php vendor/bin/dep -f ../deployer/deploy.yaml config tailscale
php vendor/bin/dep -f ../deployer/deploy.yaml config runner
Pop-Location
```

## Metric interpretation

- Component RAM uses time-weighted **PSS** from `/proc/<PID>/smaps_rollup`; RSS and USS are also reported.
- Host RAM uses `MemTotal - MemAvailable`; cgroup memory is a separate cross-check.
- Host CPU uses exact `/proc/stat` deltas.
- Component CPU uses per-PID kernel tick deltas at 200 ms by default and is shown as core-seconds plus average percent of one core.
- `idle`, `deploy`, warm-up, and recovery are separate observed phases; setup and the preconditioning deployment are intentionally excluded.
- The analyzer rejects incomplete collectors, missing host roles, missing phases, a too-short idle window, and any `smaps_rollup` permission failure.

See [the complete methodology](docs/benchmark-methodology.md) before comparing values.

## References

- [Deployer YAML recipes](https://deployer.org/docs/7.x/yaml)
- [Deployer Laravel recipe](https://deployer.org/docs/7.x/recipe/laravel)
- [Deployer host and localhost behavior](https://deployer.org/docs/7.x/hosts)

Content derived from these references was rephrased for compliance with licensing restrictions.
