#!/usr/bin/env bash
# Helpers for the Docker servers in docker/<name>/. Anything not here is plain
# "docker compose" run inside docker/<name>/.
#
#   ./docker.sh <name> stop    stop the server cleanly, then make a full backup
#                              (Distant Horizons included, since nothing is running)
#
# No backup is made if the server was not running or did not stop cleanly: a crashed
# server may have left a broken save.
set -uo pipefail

repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)

usage() {
    local names=() f
    for f in "$repo_root"/docker/*/compose.yaml; do
        [[ -f "$f" ]] && names+=("$(basename -- "$(dirname -- "$f")")")
    done
    local IFS='|'
    printf 'Usage: %s {%s} stop\n' "$0" "${names[*]:-name}"
}

if [[ $# -ne 2 ]]; then
    usage >&2
    exit 2
fi
name=$1
action=$2
compose_file="$repo_root/docker/$name/compose.yaml"
if [[ "$name" == */* || ! -f "$compose_file" ]]; then
    printf 'Error: no Docker server named %s (looked for %s)\n' "$name" "$compose_file" >&2
    usage >&2
    exit 2
fi
if [[ "$action" != stop ]]; then
    printf 'Error: unknown action: %s (for anything else use "docker compose" in docker/%s/)\n' \
        "$action" "$name" >&2
    exit 2
fi

compose() { docker compose -f "$compose_file" "$@"; }

mc_id=$(compose ps -q mc) || exit 1
if [[ -z "$mc_id" || "$(docker inspect -f '{{.State.Running}}' "$mc_id")" != true ]]; then
    printf 'Server %s is not running (stopped or crashed). No backup made.\n' "$name"
    compose down
    exit 0
fi

# Read the folders and live-backup excludes from compose.yaml itself, so this works
# whether or not the backups container is running and never drifts from EXCLUDES.
{
    IFS= read -r data_dir
    IFS= read -r backup_dir
    IFS= read -r excludes
} < <(compose config --format json | python3 -I -c '
import json, sys
services = json.load(sys.stdin)["services"]
def source(service, target):
    return next(v["source"] for v in services[service]["volumes"] if v["target"] == target)
print(source("mc", "/data"))
print(source("backups", "/backups"))
print(services["backups"].get("environment", {}).get("EXCLUDES", ""))
')
if [[ -z "${data_dir:-}" || -z "${backup_dir:-}" ]]; then
    printf 'Error: could not read the data or backup folder of %s from %s.\n' "$name" "$compose_file" >&2
    exit 1
fi
# The live excludes minus Distant Horizons: nothing is running, so its databases are safe to copy.
tar_excludes=()
IFS=',' read -ra patterns <<< "${excludes:-}"
for pattern in "${patterns[@]}"; do
    [[ -n "$pattern" && "$pattern" != DistantHorizons* ]] && tar_excludes+=("--exclude=$pattern")
done

# mc-backup holds this lock while it copies. Take it (waiting for a running live backup
# to finish) and keep it until the backups container is stopped, so none can start meanwhile.
exec 9<>"$backup_dir/.mc-backup-lock" || exit 1
if ! flock -n 9; then
    printf 'A live backup is running; waiting for it to finish...\n'
    flock 9
fi

started_at=$(docker inspect -f '{{.State.StartedAt}}' "$mc_id")
printf 'Stopping %s (the server saves first; this can take a minute)...\n' "$name"
compose stop backups
exec 9<&-
compose stop mc || exit 1
status=$(docker inspect -f '{{.State.ExitCode}}' "$mc_id")
if [[ "$status" != 0 ]]; then
    printf 'Server exited with code %s, not a clean stop. No backup made.\n' "$status" >&2
    compose down
    exit 1
fi
# A server can crash, write a crash report and still exit 0.
crash_report=$(find "$data_dir/crash-reports" -type f -newermt "$started_at" -print -quit 2>/dev/null)
if [[ -n "$crash_report" ]]; then
    printf 'Server wrote a crash report while running (%s). No backup made.\n' "$crash_report" >&2
    compose down
    exit 1
fi

archive="$backup_dir/$name-$(date +%Y%m%d-%H%M%S)-shutdown.tar.gz"
partial="$archive.partial"
trap 'rm -f -- "$partial"; exit 130' INT TERM HUP
printf 'Server stopped. Full backup to %s...\n' "$archive"
if tar -czf "$partial" -C "$data_dir" "${tar_excludes[@]}" . \
    && mv -- "$partial" "$archive"; then
    printf 'Backup done: %s (%s)\n' "$archive" "$(du -h -- "$archive" | cut -f1)"
    compose down
else
    rm -f -- "$partial"
    printf 'Error: backup failed. The server is stopped; the containers were left in place.\n' >&2
    exit 1
fi
