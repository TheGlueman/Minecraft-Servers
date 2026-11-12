# Minecraft Servers

Two modded Minecraft servers that share one set of launch, backup and DNS scripts.

| Server | Folder | Loader | Java | Port | Connect with |
| --- | --- | --- | --- | --- | --- |
| Emily | `Emily-Server/` | Fabric 26.3 | 25 | 25566 | `TFSMinecraft.chickenkiller.com:25566` |
| HBM Space | `HBM-Space-Server-3/` | Forge 1.7.10 | 8 | 25565 | `TFSMinecraft.chickenkiller.com` |

## Servers started with run.sh (Emily, HBM)

The server runs in your terminal, so that window is both the log and the console.

| To | Do this |
| --- | --- |
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

A Docker test of Emily lives in `docker/emily-test/`: `compose.yaml` says how to run
it, and `server/` next to it holds its files (a copy of Emily's world, mods and jar,
plus its backups; not in git). Port 25567, this PC only. Everything goes through
`docker.sh` in the repo root (from another folder, give its path, e.g. `../../docker.sh`).

| To | Do this |
|---|---|
| Start | `./docker.sh emily-test up`: shows the live log, and you type server commands into it, like `run.sh` |
| Start in the background | `./docker.sh emily-test up -d` |
| Shut down | Ctrl+C or type `stop` in the `up` view, or `./docker.sh emily-test down`: warns players 10 seconds ahead (only if anyone is on; Ctrl+C again cancels), stops, then makes a full backup |
| Leave it running | Ctrl+D in the `up` view, or just close the window |
| Get back to it | `./docker.sh emily-test attach` |
| See the logs | `./docker.sh emily-test logs` (Ctrl+C only stops watching) |
| Run a console command | Type it in the `up`/`attach` view, or `./docker.sh emily-test console` (commands, no log), or one command: `./docker.sh emily-test console say hi` |
| Back up while running | `./docker.sh emily-test backup` (players stay on) |
| Restart | `./docker.sh emily-test restart`: shut down with a backup, then start in the background |
| Check on it | `./docker.sh emily-test status`: running or not, players, latest backups |

Options: `up --no-restart` (don't restart after a crash), `down --now` (skip the
warning), `down --no-backup`.

After a crash Docker restarts the server, up to 3 times. Stopping it yourself never
triggers a restart.

Backups all go in `docker/emily-test/server/backups-docker/`, named by kind, keeping the
newest 10 of each kind:

- `…-auto.tar.gz`: live, every 24h of uptime (without Distant Horizons)
- `…-manual.tar.gz`: live, from `backup` (without Distant Horizons)
- `…-shutdown.tar.gz`: full, made while the server is off after a shutdown

Players see backup start/finish/failure messages in chat. A crash never triggers a
backup.

## Layout

```bash
Minecraft-Servers/
├── docker.sh             runs the Docker servers: up, down, backup, logs, console... (see its top)
├── scripts/              shared scripts (run, server, backup, sync-mods, DNS updater)
├── docker/emily-test/    Docker test of Emily: compose.yaml (how to run it) and
│                         server/ (its world, mods, jar and backups; not in git)
├── docker/backup-hooks/  chat announcements and pruning for Docker backups
├── docker/console.py     the live log + command console behind docker.sh up/attach
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
