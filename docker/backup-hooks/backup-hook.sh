#!/usr/bin/env bash
# Hooks for itzg/mc-backup, shared by every Docker server (mounted at /hooks).
#
#   backup-hook.sh start                  PRE_SAVE_ALL_SCRIPT: announce the backup in chat
#   backup-hook.sh done STATUS LOGFILE    POST_BACKUP_SCRIPT: file it, prune, announce result
#
# Live backups are written to DEST_DIR (backups/auto). A manual backup is the same
# backup run with BACKUP_KIND=manual (docker.sh <name> backup); it is moved to
# backups/manual afterwards. After a good backup the newest KEEP_BACKUPS (default 10)
# in its folder are kept. A failed backup's broken archive is deleted and nothing is pruned.
#
# The backup loop runs with "set -e", so this script must never fail: it always exits 0.

kind=${BACKUP_KIND:-auto}
keep=${KEEP_BACKUPS:-10}
dest=${DEST_DIR:-/backups}
marker=/tmp/backup-hook-started
hooks_dir=$(dirname -- "${BASH_SOURCE[0]}")

log() { printf '%s backup-hook: %s\n' "$(date -Iseconds)" "$*"; }

source "$hooks_dir/chat.sh"
# say COLOUR TEXT: coloured chat message to everyone.
say() {
    rcon-cli tellraw @a "$(tellraw_json Backup "$1" "$2")" >/dev/null 2>&1 \
        || log "could not post to chat: $2"
}

if [[ $kind == manual ]]; then label=Manual; else label=Daily; fi

case "${1:-}" in
start)
    touch "$marker"
    say yellow "$label backup starting, you may notice a short lag."
    ;;
done)
    status=${2:-1}
    backup_log=${3:-}
    # The archive this run wrote: the newest one created since "start".
    archive=$(find "$dest" -maxdepth 1 -name '*.tar.gz' -newer "$marker" 2>/dev/null | sort | tail -n 1)
    rm -f "$marker"

    # tar exit 1 = "file changed as we read it": the archive is complete and usable, and
    # the world itself was frozen by save-off, so it counts as a good backup.
    if [[ $status == 1 && -n "$archive" ]]; then
        log "the 'Backup failed with exit code 1' above is only tar warning that files" \
            "changed while copying; the archive is kept as a good backup. Changed files:"
        [[ -f "$backup_log" ]] && grep -i 'changed as we read' "$backup_log" | sed 's/^/  /'
        status=0
    fi

    if [[ $status != 0 || -z "$archive" ]]; then
        log "backup failed (exit $status); see the tar output above"
        if [[ -n "$archive" ]]; then
            rm -f -- "$archive" && log "deleted the incomplete archive $archive"
        fi
        say red "$label backup FAILED. The world was not backed up. Tell Louis."
        exit 0
    fi

    folder=$dest
    if [[ $kind == manual ]]; then
        folder=$(dirname -- "$dest")/manual
        if mkdir -p "$folder" && mv -- "$archive" "$folder/"; then
            archive="$folder/$(basename -- "$archive")"
        else
            log "could not move $archive to $folder; left it in $dest"
            folder=$dest
        fi
    fi
    log "backup saved: $archive"
    "$hooks_dir/prune.sh" "$folder" "$keep" || log "pruning $folder failed"
    size=$(du -h -- "$archive" 2>/dev/null | cut -f1)
    say green "$label backup finished (${size:-?}B)."
    ;;
*)
    log "unknown hook call: $*"
    ;;
esac
exit 0
