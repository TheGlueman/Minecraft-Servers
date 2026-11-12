#!/usr/bin/env bash
set -uo pipefail

# Resolve before any cd, so a relative invocation path still finds backup.sh later.
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)

usage() {
    printf 'Usage: %s SERVER_DIRECTORY [--no-backup] [--check]\n' "$0"
}

if [[ $# -lt 1 ]]; then
    usage >&2
    exit 2
fi

server_root=$(cd -- "$1" 2>/dev/null && pwd) || {
    printf 'Error: server directory not found: %s\n' "$1" >&2
    exit 2
}
shift

no_backup=false
check_only=false
for arg in "$@"; do
    case "${arg,,}" in
        --no-backup|--nb|-no-backup|-nb) no_backup=true ;;
        --check) check_only=true ;;
        *)
            printf 'Error: unknown argument: %s\n' "$arg" >&2
            usage >&2
            exit 2
            ;;
    esac
done

config_file="$server_root/server.conf"
if [[ ! -f "$config_file" ]]; then
    printf 'Error: server config not found: %s\n' "$config_file" >&2
    exit 1
fi
# server.conf is trusted shell-style configuration maintained alongside each server.
source "$config_file"

: "${JAVA_BIN:?JAVA_BIN must be set in server.conf}"
: "${JAVA_VERSION_MATCH:?JAVA_VERSION_MATCH must be set in server.conf}"
: "${JAR_FILE:?JAR_FILE must be set in server.conf}"
: "${RAM_ALLOCATION:?RAM_ALLOCATION must be set in server.conf}"
# Optional extra JVM flags, as a Bash array in server.conf: JAVA_ARGS=(-XX:+UseZGC)
[[ -v JAVA_ARGS ]] || JAVA_ARGS=()
if [[ "$(declare -p JAVA_ARGS)" != "declare -a"* ]]; then
    printf 'Error: JAVA_ARGS in server.conf must be an array, e.g. JAVA_ARGS=(-XX:+UseZGC)\n' >&2
    exit 1
fi

jar_path="$server_root/$JAR_FILE"
if [[ ! -f "$jar_path" ]]; then
    printf 'Error: server JAR not found: %s\n' "$jar_path" >&2
    exit 1
fi

if ! command -v -- "$JAVA_BIN" >/dev/null 2>&1; then
    printf 'Error: Java executable not found: %s\n' "$JAVA_BIN" >&2
    exit 1
fi

java_version=$("$JAVA_BIN" -version 2>&1) || {
    printf 'Error: unable to run Java executable: %s\n' "$JAVA_BIN" >&2
    exit 1
}
case "$java_version" in
    *"$JAVA_VERSION_MATCH"*) ;;
    *)
        printf 'Error: unexpected Java version from %s:\n%s\n' "$JAVA_BIN" "$java_version" >&2
        exit 1
        ;;
esac

if [[ "$check_only" == true ]]; then
    printf 'Configuration OK for %s\nJava: %s\nJAR: %s\nRAM: %s\nJava args: %s\n' \
        "$server_root" "$JAVA_BIN" "$jar_path" "$RAM_ALLOCATION" "${JAVA_ARGS[*]:-none}"
    exit 0
fi

cleanup() {
    local status=$?
    trap - EXIT

    if [[ "$no_backup" == true ]]; then
        printf '\nSkipping backup because --no-backup was specified.\n'
    else
        printf '\nServer stopped. Running backup...\n'
        "$script_dir/backup.sh" "$server_root" \
            || printf 'Warning: backup failed.\n' >&2
    fi

    exit "$status"
}
trap cleanup EXIT

java_pid=
stop_requested=false
# Pass stop signals on to Java and keep waiting, so the backup only starts once the
# server has finished saving and exited.
forward_stop() {
    stop_requested=true
    if [[ -n "$java_pid" ]] && kill -0 "$java_pid" 2>/dev/null; then
        printf '\nStop signal received; waiting for the server to save and exit...\n'
        kill -TERM "$java_pid" 2>/dev/null
    fi
}
trap forward_stop INT TERM HUP

cd -- "$server_root" || exit 1
printf 'Starting Minecraft server with %s RAM...\n' "$RAM_ALLOCATION"
printf 'Server root: %s\nJAR path: %s\n' "$server_root" "$jar_path"
# Run in the background so traps can fire; <&0 keeps the console usable for commands.
"$JAVA_BIN" "-Xmx$RAM_ALLOCATION" "-Xms$RAM_ALLOCATION" "${JAVA_ARGS[@]}" -jar "$jar_path" nogui <&0 &
java_pid=$!
# A stop signal that arrived before java_pid was set would otherwise be lost.
[[ "$stop_requested" == true ]] && kill -TERM "$java_pid" 2>/dev/null

# wait returns early when a trapped signal arrives; keep waiting until Java is gone.
while kill -0 "$java_pid" 2>/dev/null; do
    wait "$java_pid"
done
wait "$java_pid"
exit $?