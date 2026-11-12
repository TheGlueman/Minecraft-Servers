# Shared Minecraft server scripts

The scripts in this directory are shared by the server folders. Each server keeps
its Java, JAR, memory, and backup settings in its own `server.conf`.

## Run and back up

Run either server through the single case-insensitive dispatcher:
`./scripts/run.sh emily` or `./scripts/run.sh HBM`. Both use the shared
`server.sh`, then run the shared backup script when the server exits. Pass
`--no-backup` to skip that backup, or `--check` to verify Java and the JAR
without launching. For a manual backup, run `./scripts/backup.sh <server
directory>`, for example `./scripts/backup.sh Emily-Server`.

`server.properties` remains the source of truth for each listening port: Emily
uses `25566`, and HBM uses `25565`. Connecting to Emily through the shared
hostname therefore requires `TFSMinecraft.chickenkiller.com:25566` unless a
separate DNS SRV name is configured.

## Shared DNS updater

The DNS updater is independent of both server processes and should run once.
It checks the public IPv4 address, stores the last successfully updated address
under `~/.local/state/minecraft-servers/`, and calls the provider only when that
address changes.

1. Copy `scripts/dns.env.example` to `~/.config/minecraft-servers/dns.env`.
2. Set `DNS_UPDATE_URL` to the private FreeDNS update URL from the existing
   updater configuration. Keep the value quoted and do not commit this file.
3. Install and enable the user service:

   ```sh
   mkdir -p ~/.config/systemd/user
   cp scripts/systemd/minecraft-dns-updater.service ~/.config/systemd/user/
   chmod 600 ~/.config/minecraft-servers/dns.env
   systemctl --user daemon-reload
   systemctl --user enable --now minecraft-dns-updater.service
   ```

The service file assumes this workspace is at `~/Minecraft-Servers`. Update its
`ExecStart` path if the workspace is elsewhere. Check its output with
`systemctl --user status minecraft-dns-updater.service` and
`journalctl --user -u minecraft-dns-updater.service`.

To keep the user service running after logging out, enable systemd lingering for
the account with `loginctl enable-linger louis`.

`server.conf` files are trusted Bash configuration. Keep their values as shell
assignments. Extra JVM flags go in an optional `JAVA_ARGS` array, for example
`JAVA_ARGS=(-XX:+UseZGC)`.

## Server names

Each `server.conf` starts with `NAME=` (`emily`, `hbm`). The scripts find servers by
looking for folders next to `scripts/` that contain a `server.conf`, so a new server
works with `run.sh` and `sync-mods.py` as soon as its folder and config exist. Names
must be unique.

## Mod sync

`./scripts/sync-mods.py emily` updates a server's mods and configs from its client
profile (Modrinth, CurseForge or any launcher folder with `mods/` and `config/`):

- `PROFILE_DIR` in `server.conf` is the full path to the profile folder.
- `SYNC_EXCLUDE=(...)` lists mods never to copy, matched against mod ID and file name.
  Fabric mods marked client-only are skipped automatically; Forge and NeoForge mods
  don't say, so their exclude list does the work.
- `SYNC_CONFIG_SKIP=(...)` lists config files or folders (relative to `config/`,
  wildcards allowed) never to overwrite.

It prints the whole plan first (downgrades flagged at the top), then asks one yes/no.
`--dry-run` only prints the plan. It refuses to run while that server is running,
backs up the current `mods/` and `config/` to `backups/mods-config_<date>/` before
changing anything, never deletes anything that exists only on the server, and
rewrites `mods.txt`.