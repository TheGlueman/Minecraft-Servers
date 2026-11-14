#!/usr/bin/env bash
# Run the tests:   tests/run.sh          fast tests (a few seconds, no servers started)
#                  tests/run.sh --e2e    also the Docker end-to-end test (a few minutes)
#
# Fast tests: the backup pruning and hook scripts, docker.sh's argument and server.conf
# checks, and sync-mods.py. None of them touch the real servers. (shellcheck and the
# formatters run in pre-commit: see .pre-commit-config.yaml.) The end-to-end test runs a throwaway copy of emily-server; see
# tests/e2e_docker.sh.
set -uo pipefail
tests_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_root=$(dirname -- "$tests_dir")

e2e=false
for arg in "$@"; do
    case "$arg" in
        --e2e) e2e=true ;;
        *)
            printf 'Usage: %s [--e2e]\n' "$0" >&2
            exit 2
            ;;
    esac
done

failed=()
step() {
    local what=$1
    shift
    printf '\n== %s\n' "$what"
    "$@" || failed+=("$what")
}

for test in "$tests_dir"/test_*.sh; do
    step "$(basename -- "$test")" bash "$test"
done
step test_sync_mods.py python3 -I -B -m unittest discover -s "$tests_dir" -p 'test_*.py'
if [[ "$e2e" == true ]]; then
    step e2e_docker.sh bash "$tests_dir/e2e_docker.sh"
fi

printf '\n'
if ((${#failed[@]})); then
    printf 'FAILED: %s\n' "${failed[*]}"
    exit 1
fi
printf 'All tests passed.\n'
