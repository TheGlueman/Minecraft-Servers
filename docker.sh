#!/usr/bin/env bash
# The one way to run the Docker servers in docker/<name>/ (run it from anywhere).
#
#   ./docker.sh <name> up [-d] [--no-restart]
#         Start. Without -d, stay attached: the live log, and type server commands. Ctrl+C
#         or "stop" shuts down with a backup; Ctrl+D or closing the window detaches and
#         leaves the server running. -d starts it in the background. --no-restart: don't
#         restart it after a crash (by default Docker restarts it up to 3 times).
#   ./docker.sh <name> down [--now] [--no-backup]
#         Warn players 10 seconds ahead (only if anyone is on; a second Ctrl+C cancels),
#         stop cleanly, then make a full backup. --now skips the warning.
#   ./docker.sh <name> restart [--now]    down, then start again in the background
#   ./docker.sh <name> backup             live backup now; players stay on
#   ./docker.sh <name> logs               follow the log (Ctrl+C only stops watching)
#   ./docker.sh <name> console [COMMAND]  type server commands (no log), or send one
#   ./docker.sh <name> attach             the "up" view for a server already running
#   ./docker.sh <name> status             running?, players online, latest backups
#   ./docker.sh <name> sync [--dry-run]   update mods/configs from the launcher profile
#                                         (scripts/sync-mods.py; only while stopped)
#
# Each server is a folder docker/<name>/ with its settings in server.conf (see
# docker/server.conf.example) and its files in server/. All servers share
# docker/compose.yaml; nothing else is per server.
#
# Backups all go in one folder, named by kind; the newest KEEP_BACKUPS (10) of each kind are
# kept, pruned only right after a good backup of that kind:
#   <name>-<time>-auto.tar.gz      live, every 24h of uptime (without Distant Horizons)
#   <name>-<time>-manual.tar.gz    live, from "backup" (without Distant Horizons)
#   <name>-<time>-shutdown.tar.gz  full, from down/restart/Ctrl+C, made while the server is off
# A crash never triggers a backup: no shutdown backup is made if the server was not running
# or did not stop cleanly, and "restart" then leaves it down so you can look first.
set -uo pipefail

repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
commands='up|down|restart|backup|logs|console|attach|status|sync'

usage() {
    local names=() f
    for f in "$repo_root"/docker/*/server.conf; do
        [[ -f "$f" ]] && names+=("$(basename -- "$(dirname -- "$f")")")
    done
    local IFS='|'
    printf 'Usage: %s {%s} {%s} [options]\n' "$0" "${names[*]:-name}" "$commands"
    printf 'See the comments at the top of %s for what each command does.\n' "$0"
}

if [[ $# -lt 2 ]]; then
    usage >&2
    exit 2
fi
name=$1
action=$2
shift 2
server_folder="$repo_root/docker/$name"
conf_file="$server_folder/server.conf"
if [[ "$name" == */* || "$name" == .* || ! -f "$conf_file" ]]; then
    printf 'Error: no Docker server named %s (looked for %s)\n' "$name" "$conf_file" >&2
    usage >&2
    exit 2
fi
if [[ "|$commands|" != *"|$action|"* ]]; then
    printf 'Error: unknown command: %s\n' "$action" >&2
    usage >&2
    exit 2
fi

# The server's settings (trusted Bash, like the run.sh servers' server.conf), passed to the
# shared docker/compose.yaml as environment variables.
source "$conf_file" || exit 1
for var in NAME JAVA_IMAGE MC_VERSION JAR_FILE RAM_ALLOCATION GAME_PORT RCON_PORT; do
    if [[ -z "${!var:-}" ]]; then
        printf 'Error: %s must be set in %s\n' "$var" "$conf_file" >&2
        exit 1
    fi
done
if [[ "$NAME" != "$name" ]]; then
    printf 'Error: NAME=%s in %s must match its folder name, %s.\n' "$NAME" "$conf_file" "$name" >&2
    exit 1
fi
[[ -v JAVA_ARGS ]] || JAVA_ARGS=()
SERVER_NAME=$NAME
JVM_OPTS="${JAVA_ARGS[*]}"
# The port the server listens on inside the container: its own server.properties decides.
SERVER_PORT=$(sed -n 's/^server-port=\([0-9][0-9]*\).*/\1/p' "$server_folder/server/server.properties" 2>/dev/null | tail -n 1)
SERVER_PORT=${SERVER_PORT:-25565}
export SERVER_NAME JAVA_IMAGE MC_VERSION JAR_FILE RAM_ALLOCATION JVM_OPTS SERVER_PORT GAME_PORT RCON_PORT

# RCON password for this server, made on first use (outside git, readable only by you).
rcon_env="$HOME/.config/minecraft-servers/$NAME-rcon.env"
if [[ ! -f "$rcon_env" ]]; then
    if [[ ! -d "$(dirname -- "$rcon_env")" ]]; then
        mkdir -p -- "$(dirname -- "$rcon_env")" && chmod 700 -- "$(dirname -- "$rcon_env")" || exit 1
    fi
    (umask 077 && printf 'RCON_PASSWORD=%s\n' "$(head -c 32 /dev/urandom | base64 | tr -dc 'A-Za-z0-9')" > "$rcon_env") || exit 1
    printf 'Made an RCON password for %s in %s\n' "$NAME" "$rcon_env"
fi

if [[ ! -d "$server_folder/server" ]]; then
    printf 'Error: %s has no server/ folder with the server files in it.\n' "$server_folder" >&2
    exit 1
fi
# Make the backup folder ourselves: if Docker had to create it for the mount it would be
# owned by root, and so would the backups and the lock file in it.
mkdir -p -- "$server_folder/server/backups-docker" || exit 1

compose() {
    docker compose -f "$repo_root/docker/compose.yaml" --project-directory "$server_folder" \
        -p "$NAME" "$@"
}

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

# The mc container's id if it is running, else nothing (and a non-zero status).
running_id() {
    local id
    id=$(compose ps -q mc 2>/dev/null) && [[ -n "$id" ]] \
        && [[ "$(docker inspect -f '{{.State.Running}}' "$id" 2>/dev/null)" == true ]] \
        && printf '%s\n' "$id"
}

bad_option() {
    printf 'Error: unknown option for %s: %s\n' "$action" "$1" >&2
    exit 2
}

# Sets data_dir, backup_dir, keep and excludes from the Compose settings themselves, so
# they work whether or not the containers are running and never drift from them.
read_config() {
    {
        IFS= read -r data_dir
        IFS= read -r backup_dir
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
    if [[ -z "${data_dir:-}" || -z "${backup_dir:-}" ]]; then
        printf 'Error: could not read the data or backup folder of %s.\n' "$name" >&2
        exit 1
    fi
    # mc-backup holds this lock while it copies.
    lock="$backup_dir/.mc-backup-lock"
}

# shutdown {down|restart} NOW NO_BACKUP: warn, stop cleanly, full backup, then remove the
# containers (down) or start them again (restart). Returns 0 when done, 1 on failure, and
# 2 if a second Ctrl+C cancelled it during the warning (the server keeps running).
shutdown() {
    local how=$1 now=$2 no_backup=$3 id
    if ! id=$(running_id); then
        printf 'Server %s is not running (stopped or crashed). No backup made.\n' "$name"
        if [[ "$how" == down ]]; then
            compose down
            return 0
        fi
        printf 'Not starting it: check "./docker.sh %s logs" first, then use up.\n' "$name" >&2
        return 1
    fi
    read_config
    # Closing the window must not cut a shutdown or its backup short. An ignored signal is
    # also ignored by the commands this runs (docker, tar).
    trap '' HUP
    # Ctrl+C while waiting below gives up on the shutdown; nothing has been stopped yet.
    trap 'printf "\nShutdown cancelled; the server is still running.\n"; exit 130' INT

    # A stop sent while the container is still booting is lost (the image's start-up
    # scripts ignore it) and Docker kills the server after the grace period. So wait
    # until the server answers, up to 5 minutes.
    if ! compose exec -T mc mc-health >/dev/null 2>&1; then
        printf 'The server is still starting; waiting for it to finish loading before stopping it...\n'
        local tries
        for (( tries = 0; tries < 100; tries++ )); do
            compose exec -T mc mc-health >/dev/null 2>&1 && break
            running_id >/dev/null || break
            sleep 3
        done
    fi

    # The live excludes minus Distant Horizons: nothing will be running, so its databases
    # are safe to copy.
    local tar_excludes=() patterns pattern
    IFS=',' read -ra patterns <<< "${excludes:-}"
    for pattern in "${patterns[@]}"; do
        [[ -n "$pattern" && "$pattern" != DistantHorizons* ]] && tar_excludes+=("--exclude=$pattern")
    done

    # Take the backup lock first (waiting for a running live backup to finish), so the
    # warning's 10 seconds are true, and keep it until the backups container is stopped, so
    # no live backup can start meanwhile.
    exec 9<>"$lock" || return 1
    if ! flock -n 9; then
        printf 'A live backup is running; waiting for it to finish...\n'
        flock 9
    fi

    trap - INT
    local online cancelled=false
    [[ "$now" == true ]] || online=$(players_online)
    if [[ "$now" != true && "${online:-}" != 0 ]]; then
        if [[ "$how" == restart ]]; then
            say yellow "Restarting in 10 seconds for a backup. You will be disconnected; back in about 2 minutes."
        else
            say yellow "Stopping in 10 seconds for a backup. You will be disconnected."
        fi
        printf '%s player(s) online: warned them, stopping in 10 seconds (Ctrl+C to cancel)...\n' \
            "${online:-An unknown number of}"
        trap 'cancelled=true' INT
        sleep 10
        trap - INT
        if [[ "$cancelled" == true ]]; then
            exec 9<&-
            say green "Shutdown cancelled, carry on."
            printf '\nShutdown cancelled; the server is still running.\n'
            return 2
        fi
    fi

    local started_at status clean=true crash_report=
    # Docker gives nanoseconds, which find -newermt may reject; whole seconds are enough.
    started_at=$(date -u -d "$(docker inspect -f '{{.State.StartedAt}}' "$id")" +%Y-%m-%dT%H:%M:%SZ) || return 1
    printf 'Stopping %s (the server saves first; this can take a minute)...\n' "$name"
    compose stop backups
    exec 9<&-
    compose stop mc || return 1

    if [[ "$no_backup" == true ]]; then
        printf 'Stopped. No backup made (--no-backup).\n'
        compose down
        return 0
    fi

    status=$(docker inspect -f '{{.State.ExitCode}}' "$id")
    if [[ "$status" != 0 ]]; then
        printf 'Server exited with code %s, not a clean stop. No backup made.\n' "$status" >&2
        clean=false
    elif [[ -d "$data_dir/crash-reports" ]] && ! crash_report=$(find "$data_dir/crash-reports" \
            -type f -newermt "$started_at" -print -quit); then
        printf 'Could not check for crash reports, so no backup made.\n' >&2
        clean=false
    elif [[ -n "$crash_report" ]]; then
        # A server can crash, write a crash report and still exit 0.
        printf 'Server wrote a crash report while running (%s). No backup made.\n' "$crash_report" >&2
        clean=false
    fi
    if [[ "$clean" != true ]]; then
        if [[ "$how" == restart ]]; then
            printf 'Not restarting: check "./docker.sh %s logs" before starting it again.\n' "$name" >&2
        else
            compose down
        fi
        return 1
    fi

    local archive partial backup_ok=false
    archive="$backup_dir/$name-$(date +%Y%m%d-%H%M%S)-shutdown.tar.gz"
    partial="$archive.partial"
    trap 'rm -f -- "$partial"; exit 130' INT TERM
    printf 'Server stopped. Full backup to %s...\n' "$archive"
    if tar -czf "$partial" -C "$data_dir" "${tar_excludes[@]}" . \
        && mv -- "$partial" "$archive"; then
        backup_ok=true
        printf 'Backup done: %s (%s)\n' "$archive" "$(du -h -- "$archive" | cut -f1)"
        "$repo_root/docker/backup-hooks/prune.sh" "$backup_dir" "$keep" shutdown \
            || printf 'Warning: pruning old shutdown backups failed.\n' >&2
    else
        rm -f -- "$partial"
        printf 'Error: shutdown backup failed.\n' >&2
    fi
    trap - INT TERM

    if [[ "$how" == down ]]; then
        if [[ "$backup_ok" != true ]]; then
            printf 'The server is stopped; the containers were left in place.\n' >&2
            return 1
        fi
        compose down
        return 0
    fi

    # restart: start the same containers again (keeps the restart policy they were created with).
    printf 'Starting %s again...\n' "$name"
    compose start || return 1
    if [[ "$backup_ok" != true ]]; then
        # Tell the players once the server answers, up to 5 minutes. (Docker's health status
        # still says "healthy" from before the stop, so ask the server itself.)
        printf 'Waiting for the server to load to announce the failed backup...\n'
        for _ in $(seq 60); do
            compose exec -T mc mc-health >/dev/null 2>&1 && break
            sleep 5
        done
        say red "The shutdown backup FAILED. The world was not backed up. Tell Louis."
        return 1
    fi
    printf 'Started in the background: ./docker.sh %s attach to watch it.\n' "$name"
    return 0
}

# attach_console LOGS: run the console (log: all|tail|none) until you detach or shut down.
attach_console() {
    local logs=$1 id rc
    while :; do
        # Closing the window detaches; the console handles that and the server keeps running.
        trap 'exit 0' HUP
        id=$(running_id) || {
            printf 'Error: server %s is not running.\n' "$name" >&2
            return 1
        }
        python3 -I "$repo_root/docker/console.py" "$id" "$logs"
        rc=$?
        case $rc in
            0)
                printf '\nDetached; the server is still running (./docker.sh %s attach to come back).\n' "$name"
                return 0
                ;;
            10)
                printf '\n'
                shutdown down false false
                rc=$?
                (( rc == 2 )) || return "$rc"
                # Cancelled: back to the console, showing only recent log lines.
                [[ "$logs" == all ]] && logs=tail
                ;;
            11)
                printf '\nThe server was stopped outside this window. If that was docker.sh down or\n'
                printf 'restart, it made its own backup; otherwise no backup was made.\n'
                return 0
                ;;
            12)
                printf '\nThe server crashed and Docker is not restarting it. No backup made.\n' >&2
                printf 'Check "./docker.sh %s logs", then start it again with up.\n' "$name" >&2
                return 1
                ;;
            *)
                return "$rc"
                ;;
        esac
    done
}

case "$action" in
up)
    detach=false
    for opt in "$@"; do
        case "$opt" in
            -d|--detach) detach=true ;;
            --no-restart) export AUTO_RESTART=no ;;
            *) bad_option "$opt" ;;
        esac
    done
    if running_id >/dev/null; then
        printf 'Server %s is already running.\n' "$name"
        [[ -v AUTO_RESTART ]] && printf '(--no-restart only applies when starting it.)\n'
        [[ "$detach" == true ]] && exit 0
        attach_console tail
        exit $?
    fi
    compose up -d || exit 1
    if [[ "$detach" == true ]]; then
        printf 'Started in the background: ./docker.sh %s attach to watch it.\n' "$name"
        exit 0
    fi
    attach_console all
    exit $?
    ;;
down|restart)
    now=false
    no_backup=false
    for opt in "$@"; do
        case "$opt" in
            --now) now=true ;;
            --no-backup) [[ "$action" == down ]] || bad_option "$opt"; no_backup=true ;;
            *) bad_option "$opt" ;;
        esac
    done
    shutdown "$action" "$now" "$no_backup"
    rc=$?
    (( rc == 2 )) && rc=0
    exit "$rc"
    ;;
backup)
    (( $# == 0 )) || bad_option "$1"
    running_id >/dev/null || { printf 'Error: server %s is not running.\n' "$name" >&2; exit 1; }
    read_config
    # The backup takes the lock itself; wait for a running one first, because its
    # readiness check turns saving back on, which would disturb a copy in progress.
    if ! flock -n "$lock" true; then
        printf 'A live backup is running; waiting for it to finish...\n'
        flock "$lock" true
    fi
    printf 'Making a live backup of %s into %s...\n' "$name" "$backup_dir"
    compose exec -T -e BACKUP_KIND=manual backups backup now
    exit $?
    ;;
logs)
    (( $# == 0 )) || bad_option "$1"
    compose logs -f --tail 100 --no-log-prefix mc
    ;;
console)
    if (( $# == 0 )); then
        attach_console none
        exit $?
    fi
    running_id >/dev/null || { printf 'Error: server %s is not running.\n' "$name" >&2; exit 1; }
    command="$*"
    command=${command#/}
    if [[ "${command,,}" == stop ]]; then
        shutdown down false false
        rc=$?
        (( rc == 2 )) && rc=0
        exit "$rc"
    fi
    compose exec -T mc rcon-cli "$command"
    exit $?
    ;;
attach)
    (( $# == 0 )) || bad_option "$1"
    attach_console tail
    exit $?
    ;;
sync)
    for opt in "$@"; do [[ "$opt" == --dry-run ]] || bad_option "$opt"; done
    exec python3 -I "$repo_root/scripts/sync-mods.py" "$NAME" "$@"
    ;;
status)
    (( $# == 0 )) || bad_option "$1"
    read_config
    if id=$(running_id); then
        printf '%s: running since %s, %s restart(s) after crashes\n' "$name" \
            "$(date -d "$(docker inspect -f '{{.State.StartedAt}}' "$id")" '+%Y-%m-%d %H:%M')" \
            "$(docker inspect -f '{{.RestartCount}}' "$id")"
        printf 'Players online: %s\n' "$(players_online || true)"
    else
        printf '%s: not running\n' "$name"
    fi
    shopt -s nullglob
    for kind in auto manual shutdown; do
        files=("$backup_dir"/*-"$kind".tar.gz)
        if (( ${#files[@]} == 0 )); then
            printf '%-9s backups: none\n' "$kind"
            continue
        fi
        newest=$(ls -t -- "${files[@]}" | head -n 1)
        printf '%-9s backups: %2d, newest %s (%s)\n' "$kind" "${#files[@]}" \
            "$(basename -- "$newest")" "$(du -h -- "$newest" | cut -f1)"
    done
    ;;
esac
