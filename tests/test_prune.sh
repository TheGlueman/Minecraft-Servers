#!/usr/bin/env bash
# docker/backup-hooks/prune.sh: keeps the newest KEEP of one kind, never touches others.
set -uo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/lib.sh"
prune="$repo_root/docker/backup-hooks/prune.sh"

dir="$tmp/backups"
mkdir -p "$dir"
# 12 auto backups, one per day (day 01 is the oldest), plus other kinds.
for day in 01 02 03 04 05 06 07 08 09 10 11 12; do
    touch -d "2026-01-$day 12:00" "$dir/srv-202601${day}-120000-auto.tar.gz"
done
for day in 01 02 03; do
    touch -d "2026-01-$day 13:00" "$dir/srv-202601${day}-130000-manual.tar.gz"
done
touch -d "2026-01-01" "$dir/notes.txt" "$dir/srv-20260101-000000.tar.gz"

out=$("$prune" "$dir" 10 auto)
check "exits 0 after pruning" eq "$?" 0
check "keeps 10 auto backups" eq "$(find "$dir" -name '*-auto.tar.gz' | wc -l)" 10
check "removes the two oldest" eq "$(find "$dir" -name '*2026010[12]-120000-auto*' | wc -l)" 0
check "keeps the newest" test -f "$dir/srv-20260112-120000-auto.tar.gz"
check "says what it removed" contains "$out" "srv-20260101-120000-auto.tar.gz"
check "leaves other kinds alone" eq "$(find "$dir" -name '*-manual.tar.gz' | wc -l)" 3
check "leaves unrenamed archives and other files alone" \
    test -f "$dir/notes.txt" -a -f "$dir/srv-20260101-000000.tar.gz"

# The order comes from the file times, not the names.
touch -d "2026-02-01" "$dir/srv-20260103-130000-manual.tar.gz"
"$prune" "$dir" 2 manual >/dev/null
check "orders by modification time" test -f "$dir/srv-20260103-130000-manual.tar.gz" \
    -a -f "$dir/srv-20260102-130000-manual.tar.gz" -a ! -f "$dir/srv-20260101-130000-manual.tar.gz"

out=$("$prune" "$dir" 10 shutdown)
check "no backups of a kind is fine" eq "$?$out" 0

"$prune" "$dir" 50 auto >/dev/null
check "fewer than KEEP deletes nothing" eq "$(find "$dir" -name '*-auto.tar.gz' | wc -l)" 10

for bad in "0 auto" "abc auto" "-1 auto" "10 Auto" "10 ../x" "10 a*"; do
    # shellcheck disable=SC2086 # split into KEEP and KIND on purpose
    "$prune" "$dir" $bad >/dev/null 2>&1
    check "rejects KEEP/KIND '$bad'" eq "$?" 2
done
"$prune" "$dir" >/dev/null 2>&1
check "rejects missing arguments" test $? -ne 0
check "nothing deleted by rejected calls" eq "$(find "$dir" -name '*-auto.tar.gz' | wc -l)" 10

finish
