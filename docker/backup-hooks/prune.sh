#!/usr/bin/env bash
# prune.sh DIR KEEP KIND: keep the newest KEEP backups of one kind (*-KIND.tar.gz, by
# modification time) in DIR and delete older ones of that kind. Other kinds are untouched.
# Only call it after a backup of that kind succeeded.
# Shared by backup-hook.sh (inside the backup container) and docker.sh (on the host).
set -uo pipefail

usage='usage: prune.sh DIR KEEP KIND'
dir=${1:?$usage}
keep=${2:?$usage}
kind=${3:?$usage}
if ! [[ "$keep" =~ ^[0-9]+$ ]] || (( keep < 1 )); then
    printf 'prune.sh: KEEP must be a whole number of at least 1, got %s\n' "$keep" >&2
    exit 2
fi
if ! [[ "$kind" =~ ^[a-z]+$ ]]; then
    printf 'prune.sh: KIND must be a lower-case word, got %s\n' "$kind" >&2
    exit 2
fi

shopt -s nullglob
files=("$dir"/*-"$kind".tar.gz)
(( ${#files[@]} > keep )) || exit 0
# Backup names have no tabs or newlines, so one "mtime<TAB>path" line per file is safe.
stat -c '%Y	%n' -- "${files[@]}" | sort -rn | tail -n +"$((keep + 1))" | cut -f2- |
    while IFS= read -r old; do
        rm -f -- "$old" && printf 'Removed old backup %s (keeping the newest %s %s backups)\n' "$old" "$keep" "$kind"
    done
