#!/usr/bin/env bash
set -euo pipefail

server_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
# The shared backup script is in legacy/scripts/, next to this server folder.
legacy_root=$(dirname -- "$server_root")
exec "$legacy_root/scripts/backup.sh" "$server_root"