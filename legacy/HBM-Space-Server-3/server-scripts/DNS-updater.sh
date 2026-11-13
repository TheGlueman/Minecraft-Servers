#!/usr/bin/env bash
set -euo pipefail

server_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
# The DNS updater stayed in the repo's scripts/, two levels up from legacy/<server>.
repo_root=$(dirname -- "$(dirname -- "$server_root")")
exec "$repo_root/scripts/dns-updater.sh" "$@"