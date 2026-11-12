# Minecraft Servers

Two modded Minecraft servers that share one set of launch, backup and DNS scripts.

| Server | Folder | Loader | Java | Port | Connect with |
|---|---|---|---|---|---|
| Emily | `Emily-Server/` | Fabric 26.3 | 25 | 25566 | `TFSMinecraft.chickenkiller.com:25566` |
| HBM Space | `HBM-Space-Server-3/` | Forge 1.7.10 | 8 | 25565 | `TFSMinecraft.chickenkiller.com` |

## Quick start

```sh
./scripts/run.sh emily            # start Emily; backs up the world when it stops (not after a crash)
./scripts/run.sh hbm              # start HBM
./scripts/run.sh emily --check    # check Java and the JAR without starting
./scripts/run.sh emily --no-backup
./scripts/backup.sh Emily-Server  # manual world backup
./scripts/sync-mods.py emily --dry-run   # what a mod/config sync would change
./scripts/sync-mods.py emily      # sync mods and configs from the client profile
```

A Docker test of Emily lives in `docker/emily-test/` and runs on a copy of the world
in `Emily-Docker-Test/` (port 25567, this PC only). See the comments at the top of
its `compose.yaml` for the commands. Stop it with `./docker.sh emily-test stop` to get
a full backup (Distant Horizons included) once it has shut down cleanly; live backups
every 24h leave Distant Horizons out, and a crash never triggers a backup.

Type `stop` in the server console to shut down cleanly. Ctrl+C also works: the
script waits for the server to save before it backs up.

See [scripts/README.md](scripts/README.md) for script details and the DNS
updater setup.

## Layout

```
Minecraft-Servers/
├── docker.sh             stop a Docker server and back it up (./docker.sh <name> stop)
├── scripts/              shared scripts (run, server, backup, sync-mods, DNS updater)
├── docker/emily-test/    Docker test setup for Emily (compose.yaml)
├── Emily-Server/
│   ├── server.conf       Java path, JAR, RAM, extra JVM flags, backup settings
│   ├── server.properties
│   ├── config/           mod configs
│   ├── mods/             mod jars (not in git; see mods.txt)
│   ├── world/            (not in git)
│   └── backups/          world backups, cleared by hand (not in git)
└── HBM-Space-Server-3/   same layout, plus server-scripts/: backup.sh and
                          DNS-updater.sh wrappers that call scripts/, and the
                          old sync_server_mods.py (to be archived)
```

## What's in git

Only small text files you edit by hand: the scripts, each server's
`server.conf`, `server.properties`, `config/`, `mods.txt`, `eula.txt`,
`whitelist.json` and `ops.json`. Worlds, backups, logs, crash reports, jars and
libraries are ignored; `.gitignore` lists what is allowed rather than what is
excluded, so nothing large gets added by accident.

`mods.txt` records which mod files a server has, so mod updates show up in the
history. `sync-mods.py` rewrites it after every sync.

Keep the repo private: `ops.json` and `whitelist.json` contain player names and
UUIDs. The DNS update token lives in `~/.config/minecraft-servers/dns.env`,
outside this folder.

## Adding a server

1. Create its folder with a `server.conf` (copy an existing one and change `NAME=`).
2. Add a block for it in `.gitignore`.
