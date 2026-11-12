#!/usr/bin/env bash
# prune.sh DIR KEEP: keep the newest KEEP backups (*.tar.gz, by modification time) in
# DIR and delete the rest. Only call it after a backup in DIR succeeded.
# Shared by backup-hook.sh (inside the backup container) and docker.sh (on the host).
set -uo pipefail

dir=${1:?usage: prune.sh DIR KEEP}
keep=${2:?usage: prune.sh DIR KEEP}
if ! [[ "$keep" =~ ^[0-9]+$ ]] || (( keep < 1 )); then
    printf 'prune.sh: KEEP must be a whole number of at least 1, got %s\n' "$keep" >&2
    exit 2
fi

shopt -s nullglob
files=("$dir"/*.tar.gz)
(( ${#files[@]} > keep )) || exit 0
# Backup names have no tabs or newlines, so one "mtime<TAB>path" line per file is safe.
stat -c '%Y	%n' -- "${files[@]}" | sort -rn | tail -n +"$((keep + 1))" | cut -f2- |
    while IFS= read -r old; do
        rm -f -- "$old" && printf 'Removed old backup %s (keeping the newest %s)\n' "$old" "$keep"
    done
