# Sourced by backup-hook.sh and docker.sh.
# tellraw_json TAG COLOUR TEXT: a chat line "[TAG] TEXT" with a gold tag and coloured text,
# for "tellraw @a <json>" (accepted by 1.7.10 up to current versions).
# TAG and TEXT must not contain double quotes or backslashes.
tellraw_json() {
    printf '{"text":"[%s] ","color":"gold","extra":[{"text":"%s","color":"%s"}]}' "$1" "$3" "$2"
}
