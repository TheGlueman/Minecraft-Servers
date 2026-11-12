# Minecraft Servers

Two modded Minecraft servers that share one set of launch, backup and DNS scripts.

| Server | Folder | Loader | Java | Port | Connect with |
|---|---|---|---|---|---|
| Emily | `Emily-Server/` | Fabric 26.3 | 25 | 25566 | `TFSMinecraft.chickenkiller.com:25566` |
| HBM Space | `HBM-Space-Server-3/` | Forge 1.7.10 | 8 | 25565 | `TFSMinecraft.chickenkiller.com` |

## Quick start

```sh
./scripts/run.sh emily            # start Emily; backs up the world when it stops
./scripts/run.sh hbm              # start HBM
./scripts/run.sh emily --check    # check Java and the JAR without starting
./scripts/run.sh emily --no-backup
./scripts/backup.sh Emily-Server  # manual world backup
```

Type `stop` in the server console to shut down cleanly. Ctrl+C also works: the
script waits for the server to save before it backs up.

See [scripts/README.md](scripts/README.md) for script details and the DNS
updater setup.

## Layout

```
Minecraft-Servers/
├── scripts/              shared scripts (run, server, backup, DNS updater)
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

`mods.txt` records which mod jars a server has, so mod updates show up in the
history. Refresh it after changing mods:

```sh
ls Emily-Server/mods > Emily-Server/mods.txt
ls HBM-Space-Server-3/mods > HBM-Space-Server-3/mods.txt
```

Keep the repo private: `ops.json` and `whitelist.json` contain player names and
UUIDs. The DNS update token lives in `~/.config/minecraft-servers/dns.env`,
outside this folder.

## Adding a server

1. Create its folder with a `server.conf` (copy an existing one).
2. Add its name in `scripts/run.sh`.
3. Add a block for it in `.gitignore`.
