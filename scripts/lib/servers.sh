# Shared helpers for finding servers. Source this file; it defines functions only.
#
# A server is any folder next to scripts/ that contains a server.conf with a NAME=
# line. server.conf is trusted Bash configuration, so it is read by sourcing it in
# a subshell, which keeps its variables out of the caller.

# Print "NAME<TAB>DIRECTORY" for every server, sorted by name.
list_servers() {
    local repo_root=$1 conf name
    for conf in "$repo_root"/*/server.conf; do
        [[ -f "$conf" ]] || continue
        name=$(NAME='' && source "$conf" >/dev/null 2>&1 && printf '%s' "$NAME") || continue
        [[ -n "$name" ]] || continue
        printf '%s\t%s\n' "${name,,}" "$(dirname -- "$conf")"
    done | sort
}

# Print the directory of the server called NAME (case-insensitive); fail if none.
find_server_root() {
    local repo_root=$1 wanted=${2,,} name dir found=''
    while IFS=$'\t' read -r name dir; do
        if [[ "$name" == "$wanted" ]]; then
            if [[ -n "$found" ]]; then
                printf 'Error: two servers are named %s: %s and %s. Give each server.conf a different NAME=.\n' \
                    "$wanted" "$found" "$dir" >&2
                return 1
            fi
            found=$dir
        fi
    done < <(list_servers "$repo_root")
    [[ -n "$found" ]] || return 1
    printf '%s\n' "$found"
}

# Print the known server names separated by '|', for usage messages.
server_names() {
    list_servers "$1" | cut -f1 | paste -sd '|'
}
