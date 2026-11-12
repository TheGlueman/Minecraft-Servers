#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_root=$(dirname -- "$script_dir")
# shellcheck source=lib/servers.sh
source "$script_dir/lib/servers.sh"

usage() {
    printf 'Usage: %s {%s} [--no-backup] [--check]\n' "$0" "$(server_names "$repo_root")"
}

if [[ $# -lt 1 ]]; then
    usage >&2
    exit 2
fi

server_name=$1
shift

if ! server_root=$(find_server_root "$repo_root" "$server_name"); then
    printf 'Error: unknown server: %s\n' "$server_name" >&2
    usage >&2
    exit 2
fi

exec "$script_dir/server.sh" "$server_root" "$@"
