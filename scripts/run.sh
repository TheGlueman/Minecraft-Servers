#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_root=$(dirname -- "$script_dir")

usage() {
    printf 'Usage: %s {hbm|emily} [--no-backup] [--check]\n' "$0"
}

if [[ $# -lt 1 ]]; then
    usage >&2
    exit 2
fi

server_name=${1,,}
shift

case "$server_name" in
    hbm) server_root="$repo_root/HBM-Space-Server-3" ;;
    emily) server_root="$repo_root/Emily-Server" ;;
    *)
        printf 'Error: unknown server: %s\n' "$server_name" >&2
        usage >&2
        exit 2
        ;;
esac

exec "$script_dir/server.sh" "$server_root" "$@"