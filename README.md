# Minecraft Servers

Two modded Minecraft servers that share one set of launch, backup and DNS scripts.

| Server | Folder | Loader | Java | Port | Connect with |
|---|---|---|---|---|---|
| Emily | `Emily-Server/` | Fabric 26.3 | 25 | 25566 | `TFSMinecraft.chickenkiller.com:25566` |
| HBM Space | `HBM-Space-Server-3/` | Forge 1.7.10 | 8 | 25565 | `TFSMinecraft.chickenkiller.com` |

## Servers started with run.sh (Emily, HBM)

The server runs in your terminal, so that window is both the log and the console.

| To | Do this |
|---|---|
| Start | `./scripts/run.sh emily` (or `hbm`) |
| See the logs | Watch that terminal, or `tail -f Emily-Server/logs/latest.log` from elsewhere |
| Run a console command | Type it into that terminal, without a `/` (e.g. `list`, `say hi`) |
| Shut down | Type `stop`, or press Ctrl+C. It saves, stops, then backs up the world |
| Back up | Happens on every shutdown you ask for (never after a crash). For a stopped server: `./scripts/backup.sh Emily-Server` |

Other options:

```sh
./scripts/run.sh emily --check       # check Java and the JAR without starting
./scripts/run.sh emily --no-backup   # don't back up when it stops
./scripts/sync-mods.py emily --dry-run   # what a mod/config sync would change
./scripts/sync-mods.py emily         # sync mods and configs from the client profile
```

See [scripts/README.md](scripts/README.md) for script details and the DNS
updater setup.

## Docker servers (emily-test)

A Docker test of Emily lives in `docker/emily-test/` and runs on a copy of the world
in `Emily-Docker-Test/` (port 25567, this PC only). Run `docker compose` commands
from that folder (`cd docker/emily-test`); run `./docker.sh` from the repo root
(from `docker/emily-test` it is `../../docker.sh`).

| To | Do this |
|---|---|
| Start | `docker compose up -d` |
| See the logs | `docker compose logs -f mc` (Ctrl+C stops watching, not the server); backup messages: `docker compose logs -f backups` |
| Run a console command | `docker compose exec mc rcon-cli` opens a console (Ctrl+D to leave); one command: `docker compose exec mc rcon-cli say hi` |
| Shut down | `./docker.sh emily-test stop`: warns players, stops, then makes a full backup |
| Restart | `./docker.sh emily-test restart`: the same as stop, then starts it again |
| Back up while running | `./docker.sh emily-test backup` (players stay on) |
| Shut down without a backup | `docker compose down` |

After a crash the server restarts itself, up to 3 times. Stopping it yourself (any of
the ways above, or `stop` in the console) never triggers a restart. To start it
without auto-restart: `AUTO_RESTART=no docker compose up -d`.

Backups go into `Emily-Docker-Test/backups-docker/`, three folders that each keep
their newest 10:

- `auto/`: live, every 24h of uptime (without Distant Horizons)
- `manual/`: live, from `./docker.sh emily-test backup`
- `shutdown/`: full, from `./docker.sh emily-test stop` or `restart`, made while the
  server is off (players get a 10-second warning first, unless nobody is on)

Players see backup start/finish/failure messages in chat. A crash never triggers a
backup. More detail is in the comments at the top of `docker/emily-test/compose.yaml`.

## Layout

```
Minecraft-Servers/
├── docker.sh             back up, stop or restart a Docker server (./docker.sh <name> backup|stop|restart)
├── scripts/              shared scripts (run, server, backup, sync-mods, DNS updater)
├── docker/emily-test/    Docker test setup for Emily (compose.yaml)
├── docker/backup-hooks/  chat announcements and pruning for Docker backups
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
