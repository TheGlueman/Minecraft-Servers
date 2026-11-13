# Shared Minecraft server scripts

The scripts here are shared by the legacy run.sh servers (`legacy/`) and the Docker
servers (`docker/`): mod sync and the DNS updater. The run.sh launch and backup
scripts are in `legacy/scripts/` (see [legacy/README.md](../legacy/README.md)).

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

## Mod sync

`./scripts/sync-mods.py emily` updates a server's mods and configs from its client
profile. It finds servers by their `server.conf` NAME, in `legacy/*/` and
`docker/*/`; names must be unique. For the Docker servers (`./docker.sh emily-server
sync`, or `sync-mods.py emily-server`) it works on their `server/` folder. The profile
can be Modrinth, CurseForge or any launcher folder with `mods/` and `config/`:

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