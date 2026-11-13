# Legacy run.sh servers

The original Emily and HBM servers, run directly on this PC with Java by the
scripts in `scripts/`. The commands below are run from the repo root, though run.sh
works from any folder. The Docker copies in `../docker/` are separate.

## Run and back up

Run either server through the single case-insensitive dispatcher:
`./legacy/scripts/run.sh emily` or `./legacy/scripts/run.sh HBM`. Both use the shared
`server.sh`, then run the shared backup script when the server exits. Pass
`--no-backup` to skip that backup, or `--check` to verify Java and the JAR
without launching. For a manual backup, run `./legacy/scripts/backup.sh <server
directory>`, for example `./legacy/scripts/backup.sh legacy/Emily-Server`.

`server.properties` remains the source of truth for each listening port: Emily
uses `25566`, and HBM uses `25565`. Connecting to Emily through the shared
hostname therefore requires `TFSMinecraft.chickenkiller.com:25566` unless a
separate DNS SRV name is configured.

## Server names

Each `server.conf` starts with `NAME=` (`emily`, `hbm`). The scripts find servers by
looking for folders in `legacy/` (next to `scripts/`) that contain a `server.conf`, so
a new server works with `run.sh` and `sync-mods.py` as soon as its folder and config
exist. Names must be unique.

## server.conf

`server.conf` files are trusted Bash configuration. Keep their values as shell
assignments. Extra JVM flags go in an optional `JAVA_ARGS` array, for example
`JAVA_ARGS=(-XX:+UseZGC)`.
