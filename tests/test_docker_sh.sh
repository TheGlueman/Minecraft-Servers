#!/usr/bin/env bash
# docker.sh argument and server.conf checks, on a throwaway copy of the repo with a fake
# server "selftest-t1" and a fake HOME. No containers are started: the fake docker on PATH only
# records the settings docker.sh passes it. The last checks use the real docker, if
# there is one, to render compose.yaml for selftest-t1 (read-only; nothing is created).
set -uo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/lib.sh"

repo="$tmp/repo"
copy_tooling "$repo" || exit 1
mkdir -p "$repo/docker/servers/selftest-t1/data" "$tmp/home" "$tmp/bin"
conf="$repo/docker/servers/selftest-t1/server.conf"
write_conf() {
    cat >"$conf" <<'EOF'
NAME=selftest-t1
JAVA_IMAGE=itzg/minecraft-server:java25
MC_VERSION=26.3
JAR_FILE=server.jar
RAM_ALLOCATION=2G
JAVA_ARGS=(-XX:+UseZGC -Dfoo=bar)
GAME_PORT=25590
RCON_PORT=25591
EOF
}
write_conf

real_docker=$(command -v docker || true)
cat >"$tmp/bin/docker" <<EOF
#!/usr/bin/env bash
printf 'SERVER_PORT=%s JVM_OPTS=%s SERVER_NAME=%s GAME_PORT=%s\n' \
    "\$SERVER_PORT" "\$JVM_OPTS" "\$SERVER_NAME" "\$GAME_PORT" > "$tmp/docker-env"
exit 1
EOF
chmod +x "$tmp/bin/docker"

# ds ARGS...: run the copied docker.sh; sets out (stdout+stderr) and rc.
ds() {
    out=$(HOME="$tmp/home" PATH="$tmp/bin:$PATH" "$repo/docker.sh" "$@" 2>&1)
    rc=$?
}

ds
check "no arguments: usage, exit 2" eq "$rc" 2
check "usage lists the servers" contains "$out" "{selftest-t1}"
ds selftest-t1
check "no command: exit 2" eq "$rc" 2
ds nope up
check "unknown server: exit 2" eq "$rc" 2
check "unknown server: says so" contains "$out" "no Docker server named nope"
# not-a-server: a folder in docker/servers/ without a server.conf.
mkdir -p "$repo/docker/servers/not-a-server"
for bad in ../selftest-t1 selftest-t1/ .selftest-t1 not-a-server; do
    ds "$bad" status
    check "rejects server name '$bad'" eq "$rc" 2
done
ds selftest-t1 explode
check "unknown command: exit 2" eq "$rc" 2

for opt in "up --bogus" "restart --no-backup" "down --later" "logs x" "attach x" \
    "status x" "backup x" "sync --apply"; do
    # shellcheck disable=SC2086 # split into command and option on purpose
    ds selftest-t1 $opt
    check "rejects '$opt' (exit 2)" eq "$rc" 2
done
check "options are checked before Docker is called" test ! -f "$tmp/docker-env"

for var in NAME JAVA_IMAGE MC_VERSION JAR_FILE RAM_ALLOCATION GAME_PORT RCON_PORT; do
    write_conf
    sed -i "/^$var=/d" "$conf"
    ds selftest-t1 status
    check "missing $var: exit 1 with message" eq "$rc:$(contains "$out" "$var must be set" && echo y)" "1:y"
done
write_conf
sed -i 's/^NAME=selftest-t1/NAME=other/' "$conf"
ds selftest-t1 status
check "NAME must match the folder" contains "$out" "must match its folder name"
write_conf

rm -rf "$repo/docker/servers/selftest-t1/data"
ds selftest-t1 status
check "no data/ folder: exit 1" eq "$rc" 1
check "no data/ folder: says so" contains "$out" "has no data/ folder"
mkdir -p "$repo/docker/servers/selftest-t1/data"

rcon="$tmp/home/.config/minecraft-servers/selftest-t1-rcon.env"
check "makes an RCON password file" test -f "$rcon"
check "...readable only by you" eq "$(stat -c %a "$rcon")" 600
check "...in a folder only you can open" eq "$(stat -c %a "$(dirname "$rcon")")" 700
check "...with a long random password" grep -qE '^RCON_PASSWORD=[A-Za-z0-9]{20,}$' "$rcon"
before=$(cat "$rcon")
ds selftest-t1 status
check "keeps the same password on later runs" eq "$(cat "$rcon")" "$before"
check "makes backups/ itself (not Docker as root)" test -d "$repo/docker/servers/selftest-t1/backups"

check "passes JAVA_ARGS to Docker as JVM_OPTS" contains "$(cat "$tmp/docker-env")" "JVM_OPTS=-XX:+UseZGC -Dfoo=bar"
check "SERVER_PORT defaults to 25565" contains "$(cat "$tmp/docker-env")" "SERVER_PORT=25565 "
printf 'motd=x\nserver-port=25601\n' >"$repo/docker/servers/selftest-t1/data/server.properties"
ds selftest-t1 status
check "SERVER_PORT comes from server.properties" contains "$(cat "$tmp/docker-env")" "SERVER_PORT=25601 "

if [[ -n "$real_docker" ]] && "$real_docker" compose version >/dev/null 2>&1; then
    ln -sf -- "$real_docker" "$tmp/bin/docker"
    ds selftest-t1 status
    check "real docker: compose.yaml renders and status works" eq "$rc" 0
    check "real docker: not running" contains "$out" "selftest-t1: not running"
    check "real docker: lists the backup kinds" contains "$out" "shutdown  backups: none"
else
    printf '  skip  real docker checks (docker compose not available)\n'
fi

finish
