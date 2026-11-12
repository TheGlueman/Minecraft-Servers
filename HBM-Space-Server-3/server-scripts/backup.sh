#!/usr/bin/env bash
set -euo pipefail

server_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
repo_root=$(dirname -- "$server_root")
exec "$repo_root/scripts/backup.sh" "$server_root"