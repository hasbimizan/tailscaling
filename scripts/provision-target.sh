#!/usr/bin/env bash
set -Eeuo pipefail

EXPECTED_HOST=${EXPECTED_HOST:-experimental-vm}
EXPECTED_USER=${EXPECTED_USER:-mijon}

[[ "$(hostname -s)" == "$EXPECTED_HOST" ]]
[[ "$(id -un)" == "$EXPECTED_USER" ]]
sudo -n true

cat /etc/os-release
uname -a

required_commands=(php composer python3 git curl tar /usr/bin/time)
required_extensions=(pdo_sqlite mbstring openssl tokenizer ctype fileinfo)

runtime_ready() {
    local command_name extension
    for command_name in "${required_commands[@]}"; do
        command -v "$command_name" >/dev/null 2>&1 || return 1
    done
    php -r 'exit(version_compare(PHP_VERSION, "8.2.0", ">=") ? 0 : 1);' || return 1
    for extension in "${required_extensions[@]}"; do
        php -r "exit(extension_loaded('$extension') ? 0 : 1);" || return 1
    done
    composer --version --no-ansi | grep -qE '^Composer version 2\.'
}

if runtime_ready; then
    echo "Target runtime already satisfies all requirements."
else
    if ! command -v apt-get >/dev/null 2>&1; then
        echo "Unsupported target: apt-get is required for automatic provisioning." >&2
        exit 1
    fi

    sudo apt-get update
    candidate=$(apt-cache policy php-cli | awk '/Candidate:/ {print $2; exit}')
    echo "php-cli candidate: ${candidate:-unknown}"
    if [[ -z "$candidate" || "$candidate" == "(none)" ]]; then
        echo "No php-cli candidate is available from configured repositories." >&2
        exit 1
    fi

    sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        ca-certificates \
        composer \
        curl \
        git \
        php-cli \
        php-curl \
        php-mbstring \
        php-sqlite3 \
        php-xml \
        php-zip \
        python3 \
        tar \
        time \
        unzip
fi

if ! runtime_ready; then
    echo "Provisioning completed but the runtime contract is still unsatisfied." >&2
    php --version 2>/dev/null || true
    composer --version --no-ansi 2>/dev/null || true
    php -m 2>/dev/null || true
    exit 1
fi

php --version
composer --version --no-ansi
python3 --version
git --version
printf 'Required PHP extensions:\n'
php -r 'foreach (["pdo_sqlite", "mbstring", "openssl", "tokenizer", "ctype", "fileinfo"] as $extension) { printf("%s=%s\n", $extension, extension_loaded($extension) ? "yes" : "no"); }'
echo "Target provisioning verified."
