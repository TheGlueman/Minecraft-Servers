#!/usr/bin/env bash
# scripts/secret-filter.sh: the filter itself, --install and --check, in throwaway git
# repos (your git settings and hooks are not used).
set -uo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/lib.sh"
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1 GIT_AUTHOR_NAME=t \
    GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t

props='motd=A server
rcon.password=hunter2
rcon.port=25575
management-server-secret=s3cret
management-server-tls-keystore-password=pw
level-name=world'
blanked='motd=A server
rcon.password=
rcon.port=25575
management-server-secret=
management-server-tls-keystore-password=
level-name=world'

# new_repo DIR: a git repo in DIR with a copy of the script and the .gitattributes.
new_repo() {
    mkdir -p "$1/scripts" && git init -q "$1" \
        && cp -- "$repo_root/scripts/secret-filter.sh" "$1/scripts/" \
        && cp -- "$repo_root/.gitattributes" "$1/"
}

check "blanks the secrets, keeps the rest" eq "$("$repo_root/scripts/secret-filter.sh" <<<"$props")" "$blanked"
check "leaves keys that only start like a secret" \
    eq "$("$repo_root/scripts/secret-filter.sh" <<<'rcon.password.old=x')" 'rcon.password.old=x'
check "blanks key:value and 'key value' too" \
    eq "$("$repo_root/scripts/secret-filter.sh" <<<$'rcon.password: a\nrcon.password b\n rcon.password = c')" \
    $'rcon.password:\nrcon.password \n rcon.password ='

repo="$tmp/repo"
new_repo "$repo"
out=$("$repo/scripts/secret-filter.sh" --install)
check "--install sets up the filter" contains "$out" "Set up the git filter"
check "...as a required filter" eq "$(git -C "$repo" config filter.server-secrets.required)" true
check "--install again is quiet" eq "$("$repo/scripts/secret-filter.sh" --install)" ""

mkdir -p "$repo/servers/a"
printf '%s\n' "$props" >"$repo/servers/a/server.properties"
git -C "$repo" add -A
check "git stores the blanked copy" eq "$(git -C "$repo" show :servers/a/server.properties)" "$blanked"
check "the file on disk keeps its values" grep -q '^rcon.password=hunter2$' "$repo/servers/a/server.properties"
(cd "$repo" && scripts/secret-filter.sh --check)
check "--check passes the filtered file" eq "$?" 0
git -C "$repo" commit -qm one
sed -i 's/hunter2/changed/' "$repo/servers/a/server.properties"
check "a new password alone is no change to git" test -z "$(git -C "$repo" status --porcelain)"

# A clone without the filter: --check stops the commit.
plain="$tmp/plain"
new_repo "$plain"
mkdir -p "$plain/deep/er"
printf '%s\n' "$props" >"$plain/deep/er/server.properties"
printf 'rcon.password=\n' >"$plain/server.properties"
git -C "$plain" add -A
out=$(cd "$plain" && scripts/secret-filter.sh --check 2>&1)
check "--check fails without the filter" eq "$?" 1
check "...naming the file" contains "$out" "deep/er/server.properties"
check "...not the blank one" test "$(grep -c '^  server.properties' <<<"$out")" -eq 0
printf 'rcon.password hunter2\n' >"$plain/deep/er/server.properties"
git -C "$plain" add -A
(cd "$plain" && scripts/secret-filter.sh --check 2>/dev/null)
check "--check catches the 'key value' form" eq "$?" 1
printf 'rcon.password=\n' >"$plain/deep/er/server.properties"
git -C "$plain" add -A
(cd "$plain" && scripts/secret-filter.sh --check 2>/dev/null)
check "--check passes once it is blank" eq "$?" 0
mkdir -p "$plain/My Server"
printf 'rcon.password=x\n' >"$plain/My Server/server.properties"
git -C "$plain" add -A
out=$(cd "$plain" && scripts/secret-filter.sh --check 2>&1)
check "--check quotes paths with spaces in its fix command" contains "$out" 'My\ Server/server.properties'
git -C "$plain" rm -q --cached -- "My Server/server.properties"

# --install only touches the repo the script belongs to.
ln -s -- "$repo" "$tmp/link"
git -C "$repo" config --unset filter.server-secrets.clean
"$tmp/link/scripts/secret-filter.sh" --install >/dev/null
check "--install works through a symlinked path" test -n "$(git -C "$repo" config filter.server-secrets.clean)"
mkdir -p "$tmp/norepo/scripts" "$repo/vendor/copy/scripts"
cp -- "$repo_root/scripts/secret-filter.sh" "$tmp/norepo/scripts/"
cp -- "$repo_root/scripts/secret-filter.sh" "$repo/vendor/copy/scripts/"
"$tmp/norepo/scripts/secret-filter.sh" --install
check "--install outside git does nothing" eq "$?" 0
git -C "$repo" config --unset filter.server-secrets.clean
"$repo/vendor/copy/scripts/secret-filter.sh" --install
check "--install from a copy inside another repo does nothing" \
    test -z "$(git -C "$repo" config filter.server-secrets.clean)"

"$repo_root/scripts/secret-filter.sh" --bogus 2>/dev/null
check "unknown option: exit 2" eq "$?" 2

finish
