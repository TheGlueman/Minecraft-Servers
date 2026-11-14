#!/usr/bin/env bash
# docker/backup-hooks/backup-hook.sh, run on the host with a fake rcon-cli that records
# what it would have posted to chat.
set -uo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/lib.sh"
hook="$repo_root/docker/backup-hooks/backup-hook.sh"

mkdir -p "$tmp/bin" "$tmp/backups"
chat="$tmp/chat.log"
cat >"$tmp/bin/rcon-cli" <<EOF
#!/usr/bin/env bash
[[ -f "$tmp/rcon-broken" ]] && exit 1
printf '%s\n' "\$*" >> "$chat"
EOF
chmod +x "$tmp/bin/rcon-cli"
export PATH="$tmp/bin:$PATH" DEST_DIR="$tmp/backups" BACKUP_HOOK_MARKER="$tmp/marker" \
    KEEP_BACKUPS=3
dest=$DEST_DIR

# One backup run: start, then mc-backup writes an archive, then done STATUS.
# run_backup STATUS [write-archive: yes|no]
run_backup() {
    : >"$chat"
    "$hook" start >/dev/null
    if [[ "${2:-yes}" == yes ]]; then
        # Named like mc-backup's (a six-digit time), unique per run; strictly newer than
        # the marker, as a real archive is.
        runs=$((runs + 1))
        archive="$dest/srv-20260101-$(printf '%06d' "$runs").tar.gz"
        echo data >"$archive"
        touch -d "@$(($(date +%s) + 2))" "$archive"
    fi
    printf 'tar: world/region/r.0.0.mca: file changed as we read it\n' >"$tmp/backup.log"
    out=$("$hook" "done" "$1" "$tmp/backup.log")
    rc=$?
}
runs=0

"$hook" start >/dev/null
check "start leaves a marker" test -f "$BACKUP_HOOK_MARKER"
check "start announces a daily backup" contains "$(cat "$chat")" '"Daily backup starting'
check "...in yellow" contains "$(cat "$chat")" '"color":"yellow"'
rm -f "$BACKUP_HOOK_MARKER"

run_backup 0
check "good backup: exits 0" eq "$rc" 0
check "good backup: renamed to -auto" test -f "${archive%.tar.gz}-auto.tar.gz" -a ! -f "$archive"
check "good backup: marker removed" test ! -f "$BACKUP_HOOK_MARKER"
check "good backup: green message with size" contains "$(cat "$chat")" 'Daily backup finished ('

BACKUP_KIND=manual run_backup 0
check "manual backup: renamed to -manual" test -f "${archive%.tar.gz}-manual.tar.gz"
check "manual backup: says Manual" contains "$(cat "$chat")" 'Manual backup finished'

run_backup 1
check "tar exit 1 (file changed): kept as good" test -f "${archive%.tar.gz}-auto.tar.gz"
check "tar exit 1: logs the changed files" contains "$out" "file changed as we read it"
check "tar exit 1: green message" contains "$(cat "$chat")" 'finished'

run_backup 2
check "failed backup: exits 0 anyway" eq "$rc" 0
check "failed backup: broken archive deleted" test ! -e "$archive" -a ! -e "${archive%.tar.gz}-auto.tar.gz"
check "failed backup: red message" contains "$(cat "$chat")" '"color":"red"'

run_backup 0 no
check "no archive written: counts as failed" contains "$(cat "$chat")" 'FAILED'

# Pruning: KEEP_BACKUPS=3, only after a good backup, only that kind.
for i in 1 2 3 4 5; do touch -d "2026-01-0$i" "$dest/old-2026010$i-000000-auto.tar.gz"; done
run_backup 2
check "failed backup prunes nothing" eq "$(find "$dest" -name '*-auto.tar.gz' | wc -l)" 7
manual_before=$(find "$dest" -name '*-manual.tar.gz' | wc -l)
run_backup 0
check "good backup prunes to KEEP_BACKUPS" eq "$(find "$dest" -name '*-auto.tar.gz' | wc -l)" 3
check "the new backup is kept" test -f "${archive%.tar.gz}-auto.tar.gz"
check "other kinds are not pruned" eq "$(find "$dest" -name '*-manual.tar.gz' | wc -l)" "$manual_before"

touch "$tmp/rcon-broken"
run_backup 0
check "chat down: still exits 0" eq "$rc" 0
check "chat down: logs that it could not post" contains "$out" "could not post to chat"
rm -f "$tmp/rcon-broken"

"$hook" bogus >/dev/null
check "unknown hook call exits 0" eq "$?" 0
"$hook" >/dev/null
check "no arguments exits 0" eq "$?" 0

finish
