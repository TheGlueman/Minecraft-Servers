#!/usr/bin/env bash
# End-to-end test of docker.sh with a real server (a few minutes; run via tests/run.sh --e2e).
#
# Runs a throwaway copy of emily-server, called e2e-test, on ports 25596/25597, from a copy
# of the repo's Docker tooling in tests/work/ (ignored by git). The copy is a btrfs reflink,
# so it is instant and takes no extra space until it changes. The real emily-server is
# only read, and only while it is stopped. Checks: up, console, live backup, restart, down
# (with and without players' warning), a crash with and without Docker's auto-restart.
# Everything is removed at the end, including e2e-test's RCON password file.
set -uo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/lib.sh"

source_server="$repo_root/docker/servers/emily-server"
name=e2e-test
work="$repo_root/tests/work"
server="$work/docker/servers/$name"
backups="$server/backups"
rcon_env="$HOME/.config/minecraft-servers/$name-rcon.env"
log="$tmp/e2e.log"

if docker compose -p emily-server ps -q 2>/dev/null | grep -q .; then
    printf 'emily-server is running: stop it first (its world is copied for this test).\n' >&2
    exit 1
fi

cleanup() {
    # Found by their Compose label, so this works even if the test stopped half way.
    docker ps -aq --filter "label=com.docker.compose.project=$name" | xargs -r docker rm -f >/dev/null
    docker network rm "${name}_default" >/dev/null 2>&1
    # Anything the containers wrote as root can't be removed by you; remove it from one.
    if [[ -d "$work" ]] && ! rm -rf -- "$work" 2>/dev/null; then
        docker run --rm -v "$repo_root/tests:/t" --entrypoint rm \
            "$(sed -n 's/^ *image: \(itzg\/mc-backup.*\)/\1/p' "$repo_root/docker/compose.yaml")" \
            -rf /t/work
    fi
    rm -f -- "$rcon_env"
    rm -rf -- "$tmp"
}
trap cleanup EXIT

printf '  ..    copying emily-server (reflink) into tests/work\n'
rm -rf -- "$work"
copy_tooling "$work" || exit 1
mkdir -p "$server"
cp -a --reflink=auto -- "$source_server/data" "$server/" || exit 1
rm -rf -- "$server/data/logs" "$server/data/crash-reports"
sed -e "s/^NAME=.*/NAME=$name/" -e 's/^GAME_PORT=.*/GAME_PORT=25596/' \
    -e 's/^RCON_PORT=.*/RCON_PORT=25597/' "$source_server/server.conf" >"$server/server.conf"

# ds ARGS...: run the copy's docker.sh for e2e-test; sets out and rc, keeps a log.
ds() {
    out=$("$work/docker.sh" "$name" "$@" 2>&1)
    rc=$?
    printf '\n$ docker.sh %s %s  (exit %s)\n%s\n' "$name" "$*" "$rc" "$out" >>"$log"
}
mc_id() { docker compose -p "$name" ps -aq mc; }
count() { find "$backups" -name "*-$1.tar.gz" 2>/dev/null | wc -l; }

# wait_up: until the server answers commands (up to 3 minutes).
wait_up() {
    local i
    for ((i = 0; i < 90; i++)); do
        "$work/docker.sh" "$name" console list 2>/dev/null | grep -q 'There are' && return 0
        sleep 2
    done
    return 1
}
# wait_exited: until the mc container has stopped (up to 1 minute).
wait_exited() {
    local i
    for ((i = 0; i < 30; i++)); do
        [[ "$(docker inspect -f '{{.State.Running}}' "$(mc_id)" 2>/dev/null)" == false ]] && return 0
        sleep 2
    done
    return 1
}
crash() { docker exec "$(mc_id)" pkill -9 java; }

ds up -d
check "up -d starts it" eq "$rc" 0
check "the server finishes loading" wait_up
ds status
check "status: running" contains "$out" "$name: running since"
ds console say e2e test
check "console sends a command" eq "$rc" 0
ds console list
check "console gets the reply" contains "$out" "There are 0"

ds backup
check "live backup succeeds" eq "$rc" 0
check "makes one -manual backup" eq "$(count manual)" 1
manual=$(find "$backups" -name '*-manual.tar.gz')
contents=$(tar -tzf "$manual" 2>/dev/null)
check "the live backup has the world" contains "$contents" "level.dat"
check "the live backup leaves out Distant Horizons" test "$(grep -c 'DistantHorizons.sqlite' <<<"$contents")" -eq 0

ds restart --now
check "restart succeeds" eq "$rc" 0
check "restart makes a -shutdown backup" eq "$(count shutdown)" 1
shutdown_backup=$(find "$backups" -name '*-shutdown.tar.gz')
check "the shutdown backup includes Distant Horizons" grep -q 'DistantHorizons.sqlite' <<<"$(tar -tzf "$shutdown_backup")"
check "the server is back after restart" wait_up

ds down
check "down succeeds" eq "$rc" 0
check "nobody online: no warning wait" eq "$(grep -c 'warned them' <<<"$out")" 0
check "down makes a second -shutdown backup" eq "$(count shutdown)" 2
check "down removes the containers" test -z "$(mc_id)"

# A crash with --no-restart: it stays down, and "down" makes no backup.
ds up -d --no-restart
check "up -d --no-restart starts it" wait_up
crash
check "crash: the server stops" wait_exited
check "crash: Docker does not restart it" test "$(docker inspect -f '{{.State.Running}} {{.RestartCount}}' "$(mc_id)")" = "false 0"
ds down
check "down after a crash: no backup" contains "$out" "is not running (stopped or crashed). No backup made."
check "down after a crash: backup count unchanged" eq "$(count shutdown)" 2

# A crash with the default policy: Docker brings it back.
ds up -d
check "up -d starts it again" wait_up
crash
sleep 5
check "crash: Docker restarts it" wait_up
ds status
check "status counts the crash restart" contains "$out" "1 restart(s) after crashes"
ds down --now
check "down --now after a recovered crash backs up" eq "$(count shutdown)" 3

if ((fails)); then
    printf '\nLog of every docker.sh call:\n'
    cat -- "$log"
fi
finish
