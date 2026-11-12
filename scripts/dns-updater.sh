#!/usr/bin/env bash
set -euo pipefail

config_file=${DNS_ENV_FILE:-"$HOME/.config/minecraft-servers/dns.env"}
if [[ ! -f "$config_file" ]]; then
    printf 'DNS config not found: %s\n' "$config_file" >&2
    exit 1
fi
# This file contains a private update URL and must be readable only by its owner.
source "$config_file"
: "${DNS_HOSTNAME:?DNS_HOSTNAME must be set in the DNS config}"
: "${DNS_UPDATE_URL:?DNS_UPDATE_URL must be set in the DNS config}"
update_interval=${DNS_UPDATE_INTERVAL:-3600}

state_dir=${XDG_STATE_HOME:-"$HOME/.local/state"}/minecraft-servers
mkdir -p -- "$state_dir"
state_file="$state_dir/${DNS_HOSTNAME}.ip"
lock_file="$state_dir/${DNS_HOSTNAME}.lock"
exec 9>"$lock_file"
if ! flock -n 9; then
    printf 'DNS updater already running for %s.\n' "$DNS_HOSTNAME"
    exit 0
fi

while true; do
    timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    if ! current_ip=$(curl --ipv4 --fail --silent --show-error --max-time 30 https://api.ipify.org); then
        printf '[%s] Could not determine public IP; retrying in %s seconds.\n' "$timestamp" "$update_interval" >&2
    elif [[ ! "$current_ip" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]; then
        printf '[%s] IP lookup returned an unexpected response; retrying in %s seconds.\n' \
            "$timestamp" "$update_interval" >&2
    else
        last_ip=''
        if [[ -f "$state_file" ]]; then
            last_ip=$(<"$state_file")
        fi

        if [[ "$current_ip" != "$last_ip" ]]; then
            printf '[%s] Public IP changed from %s to %s; updating DNS.\n' \
                "$timestamp" "${last_ip:-unknown}" "$current_ip"
            # FreeDNS reports errors in the body with HTTP 200, so check the body too.
            # "has not changed" means the record already holds this address.
            if response=$(curl --fail --silent --show-error --max-time 30 "$DNS_UPDATE_URL") \
                && { [[ "$response" != ERROR* ]] || [[ "${response,,}" == *"has not changed"* ]]; }; then
                printf '%s\n' "$current_ip" > "$state_file"
                printf '[%s] DNS update succeeded: %s\n' "$timestamp" "$response"
            else
                printf '[%s] DNS update failed; will retry: %s\n' "$timestamp" "${response:-no response}" >&2
            fi
        else
            printf '[%s] Public IP unchanged.\n' "$timestamp"
        fi
    fi

    sleep "$update_interval"
done