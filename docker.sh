#!/usr/bin/env bash
# Helpers for the Docker servers in docker/<name>/. Anything not here is plain
# "docker compose" run inside docker/<name>/.
#
#   ./docker.sh <name> backup     live backup now (same as the 24h one) into backups/manual/
#   ./docker.sh <name> stop       warn players, stop cleanly, then a full backup (Distant
#                                 Horizons included, since nothing is running) into backups/shutdown/
#   ./docker.sh <name> restart    the same as stop, then start the server again
#
# No shutdown backup is made if the server was not running or did not stop cleanly: a
# crashed server may have left a broken save. "restart" then leaves it down so you can look.
set -uo pipefail

repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)

usage() {
    local names=() f
    for f in "$repo_root"/docker/*/compose.yaml; do
        [[ -f "$f" ]] && names+=("$(basename -- "$(dirname -- "$f")")")
    done
    local IFS='|'
    printf 'Usage: %s {%s} {backup|stop|restart}\n' "$0" "${names[*]:-name}"
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
case "$action" in
    backup|stop|restart) ;;
    *)
        printf 'Error: unknown action: %s (for anything else use "docker compose" in docker/%s/)\n' \
            "$action" "$name" >&2
        usage >&2
        exit 2
        ;;
esac

compose() { docker compose -f "$compose_file" "$@"; }

source "$repo_root/docker/backup-hooks/chat.sh"
# say COLOUR TEXT: coloured chat message to everyone.
say() {
    compose exec -T mc rcon-cli tellraw @a "$(tellraw_json Server "$1" "$2")" >/dev/null 2>&1
}

# Number of players online; prints nothing if the server can't be asked.
players_online() {
    local reply
    reply=$(compose exec -T mc rcon-cli list 2>/dev/null) || return
    [[ "$reply" =~ There\ are\ ([0-9]+) ]] && printf '%s\n' "${BASH_REMATCH[1]}"
}

mc_id=$(compose ps -q mc) || exit 1
if [[ -z "$mc_id" || "$(docker inspect -f '{{.State.Running}}' "$mc_id")" != true ]]; then
    if [[ "$action" == backup ]]; then
        printf 'Error: server %s is not running.\n' "$name" >&2
        exit 1
    fi
    printf 'Server %s is not running (stopped or crashed). No backup made.\n' "$name"
    [[ "$action" == stop ]] && compose down
    exit 0
fi

# Read the folders, keep count and live-backup excludes from compose.yaml itself, so this
# works whether or not the backups container is running and never drifts from it.
{
    IFS= read -r data_dir
    IFS= read -r backup_root
    IFS= read -r keep
    IFS= read -r excludes
} < <(compose config --format json | python3 -I -c '
import json, sys
services = json.load(sys.stdin)["services"]
def source(service, target):
    return next(v["source"] for v in services[service]["volumes"] if v["target"] == target)
env = services["backups"].get("environment", {})
print(source("mc", "/data"))
print(source("backups", "/backups"))
print(env.get("KEEP_BACKUPS") or 10)
print(env.get("EXCLUDES", ""))
')
if [[ -z "${data_dir:-}" || -z "${backup_root:-}" ]]; then
    printf 'Error: could not read the data or backup folder of %s from %s.\n' "$name" "$compose_file" >&2
    exit 1
fi
# mc-backup holds this lock (in its DEST_DIR, backups/auto) while it copies.
mkdir -p -- "$backup_root/auto" "$backup_root/shutdown" || exit 1
lock="$backup_root/auto/.mc-backup-lock"

if [[ "$action" == backup ]]; then
    # The backup takes the lock itself; wait for a running one first, because its
    # readiness check turns saving back on, which would disturb a copy in progress.
    if ! flock -n "$lock" true; then
        printf 'A live backup is running; waiting for it to finish...\n'
        flock "$lock" true
    fi
    printf 'Making a live backup of %s (it goes to %s)...\n' "$name" "$backup_root/manual"
    compose exec -T -e BACKUP_KIND=manual backups backup now
    exit $?
fi

# The live excludes minus Distant Horizons: nothing is running, so its databases are safe to copy.
tar_excludes=()
IFS=',' read -ra patterns <<< "${excludes:-}"
for pattern in "${patterns[@]}"; do
    [[ -n "$pattern" && "$pattern" != DistantHorizons* ]] && tar_excludes+=("--exclude=$pattern")
done

# Take the backup lock first (waiting for a running live backup to finish), so the 10-second
# warning is true, and keep it until the backups container is stopped, so no live backup
# can start meanwhile.
exec 9<>"$lock" || exit 1
if ! flock -n 9; then
    printf 'A live backup is running; waiting for it to finish...\n'
    flock 9
fi

online=$(players_online)
if [[ "$online" != 0 ]]; then
    if [[ "$action" == restart ]]; then
        say yellow "Restarting in 10 seconds for a backup. You will be disconnected; back in about 2 minutes."
    else
        say yellow "Stopping in 10 seconds for a backup. You will be disconnected."
    fi
    printf '%s player(s) online; warned them, stopping in 10 seconds (Ctrl+C to cancel)...\n' "${online:-Unknown number of}"
    sleep 10
fi

# Docker gives nanoseconds, which find -newermt may reject; whole seconds are enough.
started_at=$(date -u -d "$(docker inspect -f '{{.State.StartedAt}}' "$mc_id")" +%Y-%m-%dT%H:%M:%SZ) || exit 1
printf 'Stopping %s (the server saves first; this can take a minute)...\n' "$name"
compose stop backups
exec 9<&-
compose stop mc || exit 1

clean=true
status=$(docker inspect -f '{{.State.ExitCode}}' "$mc_id")
if [[ "$status" != 0 ]]; then
    printf 'Server exited with code %s, not a clean stop. No backup made.\n' "$status" >&2
    clean=false
else
    # A server can crash, write a crash report and still exit 0.
    crash_report=
    if [[ -d "$data_dir/crash-reports" ]] && ! crash_report=$(find "$data_dir/crash-reports" \
            -type f -newermt "$started_at" -print -quit); then
        printf 'Could not check for crash reports, so no backup made.\n' >&2
        clean=false
    elif [[ -n "$crash_report" ]]; then
        printf 'Server wrote a crash report while running (%s). No backup made.\n' "$crash_report" >&2
        clean=false
    fi
fi
if [[ "$clean" != true ]]; then
    if [[ "$action" == restart ]]; then
        printf 'Not restarting: check the log (docker compose logs mc) before starting it again.\n' >&2
        exit 1
    fi
    compose down
    exit 1
fi

archive="$backup_root/shutdown/$name-$(date +%Y%m%d-%H%M%S)-shutdown.tar.gz"
partial="$archive.partial"
trap 'rm -f -- "$partial"; exit 130' INT TERM HUP
printf 'Server stopped. Full backup to %s...\n' "$archive"
backup_ok=false
if tar -czf "$partial" -C "$data_dir" "${tar_excludes[@]}" . \
    && mv -- "$partial" "$archive"; then
    backup_ok=true
    printf 'Backup done: %s (%s)\n' "$archive" "$(du -h -- "$archive" | cut -f1)"
    "$repo_root/docker/backup-hooks/prune.sh" "$backup_root/shutdown" "$keep" \
        || printf 'Warning: pruning old shutdown backups failed.\n' >&2
else
    rm -f -- "$partial"
    printf 'Error: shutdown backup failed.\n' >&2
fi
trap - INT TERM HUP

if [[ "$action" == stop ]]; then
    if [[ "$backup_ok" == true ]]; then
        compose down
    else
        printf 'The server is stopped; the containers were left in place.\n' >&2
        exit 1
    fi
    exit 0
fi

# restart: start the same containers again (keeps the restart policy they were created with).
printf 'Starting %s again...\n' "$name"
compose start || exit 1
if [[ "$backup_ok" != true ]]; then
    # Tell the players once the server answers, up to 5 minutes. (Docker's health status
    # still says "healthy" from before the stop, so ask the server itself.)
    printf 'Waiting for the server to load to announce the failed backup...\n'
    for _ in $(seq 60); do
        compose exec -T mc mc-health >/dev/null 2>&1 && break
        sleep 5
    done
    say red "The shutdown backup FAILED. The world was not backed up. Tell Louis."
    exit 1
fi
printf 'Started. Players can reconnect once it has loaded (docker compose logs -f mc).\n'
