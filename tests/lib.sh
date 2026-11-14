# shellcheck shell=bash disable=SC2034 # the variables here are used by the tests
# Tiny test helpers, sourced by the tests/test_*.sh scripts.
#
#   check "what it tests" COMMAND...   passes if COMMAND succeeds
#   eq ACTUAL EXPECTED                 a COMMAND for check: equal strings
#   contains TEXT PART                 a COMMAND for check: TEXT contains PART
#   finish                             prints the totals; returns non-zero on any failure
#   copy_tooling DIR                   a throwaway repo in DIR with docker.sh and the Docker
#                                      tooling, but no servers (add them in DIR/docker/servers/<name>/)
#
# Each test script gets its own empty folder in $tmp, removed when it exits.

repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
passes=0
fails=0
tmp=$(mktemp -d) || exit 1
trap 'rm -rf -- "$tmp"' EXIT

check() {
    local what=$1
    shift
    if "$@"; then
        passes=$((passes + 1))
        printf '  ok    %s\n' "$what"
    else
        fails=$((fails + 1))
        printf '  FAIL  %s\n' "$what"
    fi
}

eq() {
    [[ "$1" == "$2" ]] && return 0
    printf '        expected: %s\n        got:      %s\n' "$2" "$1"
    return 1
}

contains() {
    [[ "$1" == *"$2"* ]] && return 0
    printf '        expected to contain: %s\n        got: %s\n' "$2" "$1"
    return 1
}

copy_tooling() {
    mkdir -p -- "$1/docker" || return 1
    cp -- "$repo_root/docker.sh" "$1/" || return 1
    cp -r -- "$repo_root/docker/compose.yaml" "$repo_root/docker/console.py" \
        "$repo_root/docker/backup-hooks" "$1/docker/"
}

finish() {
    printf '%s: %d passed, %d failed\n' "$(basename -- "$0")" "$passes" "$fails"
    ((fails == 0))
}
