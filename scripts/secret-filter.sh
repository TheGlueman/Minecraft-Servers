#!/usr/bin/env bash
# Keeps passwords and secrets in server.properties out of git, while the files themselves
# stay tracked.
#
#   secret-filter.sh             the git "clean" filter: copies stdin to stdout with each
#                                secret's value blanked (key=)
#   secret-filter.sh --install   set up that filter in this repo's .git/config (docker.sh
#                                does this on every run; run it by hand after cloning)
#   secret-filter.sh --check     fail if a staged server.properties still has a secret
#                                (a pre-commit hook, for a clone without the filter)
#
# .gitattributes sends every server.properties through the filter, so git stores the
# blanked copy and compares against it: your files on disk keep their values, and a change
# only to a secret is not a change to git. A file checked out from git has the secrets
# blank: Docker sets rcon.password again on every start, and a blank
# management-server-secret only matters if the management server is turned on (set a new
# one then).
set -uo pipefail

# The keys whose values never go into git, as an extended regex.
keys='rcon\.password|management-server-secret|management-server-tls-keystore-password'
filter_name=server-secrets
# key=value, key:value or "key value" (all valid in .properties files) become key= etc.
blank="s/^([[:space:]]*($keys)([[:space:]]*[=:]|[[:space:]]))[[:space:]]*.*/\\1/"
# The filter as git runs it (from the top of the work tree). It is a plain sed, not this
# script, so it also works on commits from before this script existed.
clean="sed -E '$blank'"

case "${1:-}" in
    "")
        exec sed -E "$blank"
        ;;
    --install)
        # -P: the real path, as git reports it, even if you came in through a symlink.
        repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
        # Only in this repo's own checkout (not in a copy of the scripts elsewhere).
        [[ "$(git -C "$repo" rev-parse --show-toplevel 2>/dev/null)" == "$repo" ]] || exit 0
        if [[ "$(git -C "$repo" config --local "filter.$filter_name.clean")" != "$clean" ]]; then
            git -C "$repo" config --local "filter.$filter_name.clean" "$clean" || exit 1
            printf 'Set up the git filter that keeps server.properties secrets out of git.\n'
        fi
        # A filter that fails stops the commit instead of storing the file unfiltered.
        if [[ "$(git -C "$repo" config --local "filter.$filter_name.required")" != true ]]; then
            git -C "$repo" config --local "filter.$filter_name.required" true || exit 1
        fi
        ;;
    --check)
        bad=()
        while IFS= read -r -d '' file; do
            # Anything the filter would change is a secret that got past it.
            staged=$(git show ":$file") || continue
            [[ "$(sed -E "$blank" <<<"$staged")" == "$staged" ]] || bad+=("$file")
        done < <(git diff --cached --name-only -z --diff-filter=ACMR -- \
            'server.properties' '*/server.properties')
        if ((${#bad[@]})); then
            printf 'These staged files still have a password or secret in them:\n' >&2
            printf '  %s\n' "${bad[@]}" >&2
            printf 'Set up the filter that blanks them, then stage them again:\n' >&2
            printf '  scripts/secret-filter.sh --install && git add --renormalize --' >&2
            printf ' %q' "${bad[@]}" >&2
            printf '\n' >&2
            exit 1
        fi
        ;;
    *)
        printf 'Usage: %s [--install | --check]\n' "$0" >&2
        exit 2
        ;;
esac
