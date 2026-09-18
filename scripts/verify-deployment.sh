#!/usr/bin/env bash
set -Eeuo pipefail

: "${EXPECTED_REVISION:?EXPECTED_REVISION is required}"
DEPLOY_PATH=${DEPLOY_PATH:-/home/mijon/apps/tailscaling}

[[ "$EXPECTED_REVISION" =~ ^[0-9a-f]{40}$ ]]
[[ "$(hostname -s)" == "experimental-vm" ]]
[[ "$(id -un)" == "mijon" ]]
[[ -L "$DEPLOY_PATH/current" ]]

CURRENT=$(readlink -f "$DEPLOY_PATH/current")
[[ -d "$CURRENT" ]]
[[ "$(tr -d '\r\n' < "$CURRENT/REVISION")" == "$EXPECTED_REVISION" ]]
grep -qx "revision=$EXPECTED_REVISION" "$CURRENT/DEPLOYMENT"
grep -qx 'method=runner' "$CURRENT/DEPLOYMENT"
grep -qx 'APP_ENV=production' "$CURRENT/.env"
grep -qx 'APP_DEBUG=false' "$CURRENT/.env"
grep -q '^APP_KEY=base64:' "$CURRENT/.env"
[[ "$(stat -c '%a' "$DEPLOY_PATH/shared/.env")" == "600" ]]
[[ "$(stat -c '%a' "$DEPLOY_PATH/shared/database")" == "700" ]]
[[ "$(stat -c '%a' "$DEPLOY_PATH/shared/database/database.sqlite")" == "600" ]]
[[ -s "$CURRENT/vendor/autoload.php" ]]
[[ -L "$CURRENT/storage" ]]
[[ -L "$CURRENT/database/database.sqlite" ]]
[[ -L "$CURRENT/public/storage" ]]

php "$CURRENT/artisan" about --only=environment
php "$CURRENT/artisan" migrate:status --no-interaction
php "$CURRENT/artisan" route:list --path=up --no-ansi
php -r '$database=$argv[1]; $pdo=new PDO("sqlite:".$database); $result=$pdo->query("PRAGMA integrity_check")->fetchColumn(); if ($result !== "ok") { fwrite(STDERR, "SQLite integrity check failed: $result\n"); exit(1); } echo "SQLite integrity_check=ok\n";' "$DEPLOY_PATH/shared/database/database.sqlite"

printf 'current=%s\n' "$CURRENT"
printf 'revision=%s\n' "$EXPECTED_REVISION"
cat "$CURRENT/DEPLOYMENT"
echo 'Deployment verification passed.'
