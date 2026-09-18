#!/usr/bin/env bash
set -u

OUTPUT_DIR=${1:?usage: inventory-linux.sh OUTPUT_DIR [LABEL]}
LABEL=${2:-snapshot}
mkdir -p "$OUTPUT_DIR/smaps_rollup/$LABEL"
REPORT="$OUTPUT_DIR/environment-$LABEL.txt"

run_if_available() {
    local command_name=$1
    shift
    if command -v "$command_name" >/dev/null 2>&1; then
        "$@" 2>&1 || true
    else
        printf '%s: unavailable\n' "$command_name"
    fi
}

{
    echo "captured_at_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "hostname=$(hostname)"
    echo "user=$(id)"
    echo "kernel=$(uname -a)"
    echo
    echo "[os-release]"
    cat /etc/os-release 2>/dev/null || true
    echo
    echo "[cpu]"
    run_if_available lscpu lscpu
    echo
    echo "[memory]"
    run_if_available free free -w -b
    echo
    echo "[filesystem]"
    df -hT 2>&1 || true
    echo
    echo "[cgroup]"
    cat /proc/self/cgroup 2>/dev/null || true
    for file in cpu.max cpuset.cpus.effective memory.max memory.current memory.peak; do
        if [[ -r "/sys/fs/cgroup/$file" ]]; then
            printf '%s=' "$file"
            cat "/sys/fs/cgroup/$file"
        fi
    done
    echo
    echo "[toolchain]"
    run_if_available php php --version
    run_if_available composer composer --version
    run_if_available git git --version
    run_if_available python3 python3 --version
    run_if_available node node --version
    run_if_available npm npm --version
    run_if_available tailscale tailscale version
    echo
    echo "[running-services]"
    if command -v systemctl >/dev/null 2>&1; then
        systemctl list-units --type=service --state=running --no-legend --no-pager 2>&1 || true
    fi
    echo
    echo "[processes]"
    ps -eo pid,ppid,user,comm,state,%cpu,rss --sort=pid 2>&1 || true
    echo
    if sudo -n true >/dev/null 2>&1; then
        echo "sudo_nopasswd=true"
    else
        echo "sudo_nopasswd=false"
    fi
} > "$REPORT"

classify() {
    local comm=${1,,}
    local cmd=${2,,}
    local haystack="$comm $cmd"
    if [[ "$haystack" == *runner.listener* || "$haystack" == *runner.worker* || "$haystack" == *runsvc.sh* ]]; then echo github_actions_runner
    elif [[ "$haystack" == *tailscaled* || "$comm" == tailscale ]]; then echo tailscale
    elif [[ "$haystack" == *deployer/deployer* || "$cmd" == *"/dep "* ]]; then echo deployer
    elif [[ "$haystack" == *composer* ]]; then echo composer
    elif [[ "$haystack" == *artisan* ]]; then echo laravel_artisan
    elif [[ "$comm" == php-fpm* ]]; then echo php_fpm
    elif [[ "$comm" =~ ^(nginx|apache2|httpd|caddy)$ ]]; then echo web_server
    elif [[ "$comm" =~ ^(mysqld|mariadbd|postgres|postmaster)$ ]]; then echo database
    elif [[ "$comm" =~ ^(redis-server|memcached)$ ]]; then echo cache
    elif [[ "$comm" =~ ^(ssh|sshd|scp|sftp-server)$ ]]; then echo ssh
    elif [[ "$comm" == git ]]; then echo git
    elif [[ "$comm" =~ ^(node|npm|npx|vite)$ ]]; then echo node
    elif [[ "$comm" == php* ]]; then echo php_cli
    else echo other
    fi
}

for proc_dir in /proc/[0-9]*; do
    pid=${proc_dir##*/}
    [[ -r "$proc_dir/comm" ]] || continue
    comm=$(cat "$proc_dir/comm" 2>/dev/null || true)
    cmdline=$(tr '\0' ' ' < "$proc_dir/cmdline" 2>/dev/null || true)
    component=$(classify "$comm" "$cmdline")
    [[ "$component" != other ]] || continue
    destination="$OUTPUT_DIR/smaps_rollup/$LABEL/${component}-pid-${pid}.txt"
    {
        echo "pid=$pid"
        echo "component=$component"
        echo "comm=$comm"
        echo "captured_at_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
        if sudo -n true >/dev/null 2>&1; then
            sudo -n cat "$proc_dir/smaps_rollup" 2>&1 || true
        else
            cat "$proc_dir/smaps_rollup" 2>&1 || true
        fi
    } > "$destination"
done

printf 'Inventory written to %s\n' "$OUTPUT_DIR"
