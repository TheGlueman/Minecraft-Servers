#!/usr/bin/env bash
set -uo pipefail

if [[ $# -ne 1 ]]; then
    printf 'Usage: %s SERVER_DIRECTORY\n' "$0" >&2
    exit 2
fi

server_root=$(cd -- "$1" 2>/dev/null && pwd) || {
    printf 'Backup failed: server directory not found: %s\n' "$1" >&2
    exit 1
}
config_file="$server_root/server.conf"
if [[ ! -f "$config_file" ]]; then
    printf 'Backup failed: server config not found: %s\n' "$config_file" >&2
    exit 1
fi
# server.conf is trusted shell-style configuration maintained alongside each server.
source "$config_file"

: "${BACKUP_SOURCE:=world}"
: "${BACKUP_DIR:=backups}"

if [[ "$BACKUP_SOURCE" = /* ]]; then
    source_path="$BACKUP_SOURCE"
else
    source_path="$server_root/$BACKUP_SOURCE"
fi
if [[ "$BACKUP_DIR" = /* ]]; then
    backup_dir="$BACKUP_DIR"
else
    backup_dir="$server_root/$BACKUP_DIR"
fi

if [[ ! -d "$source_path" ]]; then
    printf 'Backup failed: source directory not found: %s\n' "$source_path" >&2
    exit 1
fi

if command -v 7z >/dev/null 2>&1; then
    seven_zip=$(command -v 7z)
elif command -v 7zz >/dev/null 2>&1; then
    seven_zip=$(command -v 7zz)
else
    printf 'Backup failed: 7z or 7zz was not found.\n' >&2
    exit 1
fi

mkdir -p -- "$backup_dir" || {
    printf 'Backup failed: could not create directory: %s\n' "$backup_dir" >&2
    exit 1
}

timestamp=$(date '+%y-%m-%d_%H-%M')
archive_path="$backup_dir/Backup_$timestamp.7z"
# Never add to or delete an existing archive: pick a fresh name for same-minute backups.
suffix=2
while [[ -e "$archive_path" ]]; do
    archive_path="$backup_dir/Backup_${timestamp}_$suffix.7z"
    suffix=$((suffix + 1))
done
relative_source=${source_path#"$server_root"/}

if ! (cd -- "$server_root" && "$seven_zip" a -t7z -bso0 -bsp0 "$archive_path" "$relative_source"); then
    # -bso0/-bsp0: no progress output, so a closed terminal can't make 7-Zip fail.
    rm -f -- "$archive_path"
    printf 'Backup failed: 7-Zip returned a failure status.\n' >&2
    exit 1
fi

printf 'Backup completed: %s\n' "$archive_path"