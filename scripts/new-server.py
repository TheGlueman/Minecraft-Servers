#!/usr/bin/env python3
"""Make a new Docker server from a launcher instance (Modrinth or CurseForge).

Usage: ./docker.sh new NAME INSTANCE [--no-mods] [--ram 8G]
                       [--mc-version X --loader Y [--loader-version Z]]

INSTANCE is a path to an instance folder, or the name of a Modrinth or CurseForge
instance (its folder name or its own name). The Minecraft version and loader are read
from the launcher (or given with --mc-version/--loader for other launchers). This makes
docker/servers/NAME/ with a filled-in server.conf, the right server jar downloaded from
its official source, and the instance's mods and configs copied with the sync logic
(client-only mods are left out). It does not start the server. If anything fails, the
half-made folder is removed.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shlex
import shutil
import socket
import sqlite3
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
HOME = Path.home()
# Where the launchers keep their instances (the Flatpak and the normal Modrinth App).
MODRINTH_DIRS = [
    HOME / ".var/app/com.modrinth.ModrinthApp/data/ModrinthApp/profiles",
    HOME / ".local/share/ModrinthApp/profiles",
]
CURSEFORGE_DIRS = [HOME / "Documents/curseforge/minecraft/Instances"]
# Ports the tests use (tests/test_docker_sh.sh, tests/e2e_docker.sh); never handed out.
RESERVED_PORTS = {25590, 25591, 25596, 25597}
FIRST_GAME_PORT, FIRST_RCON_PORT = 25567, 25576
# NeoForge's launcher jar for servers that install a run.sh instead of a server jar
# (Forge 1.17+ and every NeoForge). Pinned: a new release is picked up only by changing this.
SERVER_STARTER_VERSION = "0.1.35"
SERVER_STARTER_URL = (
    "https://github.com/NeoForged/ServerStarterJar/releases/download/"
    f"{SERVER_STARTER_VERSION}/server.jar"
)
USER_AGENT = "Minecraft-Servers new-server"
SAFE_VERSION = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.+_-]*$")
LOADERS = ("vanilla", "fabric", "forge", "neoforge")
# Copied once from the instance when present: modpacks need them on the server too.
EXTRA_FOLDERS = ("defaultconfigs", "kubejs", "scripts")

_spec = importlib.util.spec_from_file_location(
    "sync_mods", Path(__file__).with_name("sync-mods.py")
)
if _spec is None or _spec.loader is None:
    raise ImportError("cannot load sync-mods.py next to this script")
# Loaded from a file path (its name has a dash), so type checkers can't see its contents.
sync: Any = importlib.util.module_from_spec(_spec)
sys.modules["sync_mods"] = sync  # dataclasses look the module up while it loads
_spec.loader.exec_module(sync)


class SetupError(Exception):
    """A problem to report to the user (no traceback)."""


class NotFoundError(SetupError):
    """The server answered 404: there is no such file."""


# ------------------------------------------------------------------------ instance


@dataclass
class Instance:
    path: Path
    name: str
    launcher: str  # "Modrinth", "CurseForge" or "folder"


@dataclass
class GameVersion:
    mc_version: str
    loader: str  # one of LOADERS
    loader_version: str | None  # None for vanilla


def _modrinth_db(profiles_dir: Path) -> sqlite3.Connection | None:
    db = profiles_dir.parent / "app.db"
    if not db.is_file():
        return None
    # Read-only: the app may be running, and it owns this database.
    return sqlite3.connect(f"{db.as_uri()}?mode=ro", uri=True)


def _modrinth_names(profiles_dir: Path) -> dict[str, str]:
    """Folder name -> instance name, from the Modrinth App's database."""
    connection = _modrinth_db(profiles_dir)
    if connection is None:
        return {}
    try:
        for table in ("instances", "profiles"):  # current and older app versions
            try:
                return dict(connection.execute(f"SELECT path, name FROM {table}").fetchall())
            except sqlite3.Error:
                continue
        return {}
    finally:
        connection.close()


def list_instances() -> list[Instance]:
    found = []
    for profiles in MODRINTH_DIRS:
        if not profiles.is_dir():
            continue
        names = _modrinth_names(profiles)
        found.extend(
            Instance(folder, names.get(folder.name, folder.name), "Modrinth")
            for folder in sorted(profiles.iterdir())
            if folder.is_dir()
        )
    for instances in CURSEFORGE_DIRS:
        if not instances.is_dir():
            continue
        for folder in sorted(instances.iterdir()):
            meta = folder / "minecraftinstance.json"
            if not meta.is_file():
                continue
            try:
                name = json.loads(meta.read_text()).get("name") or folder.name
            except (OSError, ValueError):
                name = folder.name
            found.append(Instance(folder, name, "CurseForge"))
    return found


def find_instance(arg: str) -> Instance:
    """A path, or a Modrinth/CurseForge instance by folder name or its own name."""
    # A path has a slash in it (use ./name for a folder here); anything else is a name.
    path = Path(arg).expanduser()
    if "/" in arg or arg.startswith("~"):
        if not path.is_dir():
            raise SetupError(f"No such folder: {path}")
        path = path.resolve()
        for instance in list_instances():
            if instance.path.resolve() == path:
                return instance
        return Instance(path, path.name, "folder")
    wanted = arg.casefold()
    matches = [i for i in list_instances() if wanted in (i.path.name.casefold(), i.name.casefold())]
    if not matches:
        known = ", ".join(sorted({repr(i.name) for i in list_instances()})) or "none found"
        raise SetupError(f"No instance called {arg!r}. Known instances: {known}")
    if len(matches) > 1:
        listed = "\n".join(f"  {i.launcher}: {i.name!r} in {i.path}" for i in matches)
        raise SetupError(
            f"More than one instance is called {arg!r}:\n{listed}\n"
            "Which one did you mean? Give its folder path instead of the name."
        )
    return matches[0]


def _normal_loader(loader: str | None) -> str:
    loader = (loader or "vanilla").strip().lower()
    return "vanilla" if loader in ("", "none", "minecraft") else loader


def _read_modrinth(instance: Instance) -> GameVersion | None:
    connection = _modrinth_db(instance.path.parent)
    if connection is None:
        return None
    folder = instance.path.name
    queries = (
        # Current app: the instance's applied content set (else any of its sets).
        """SELECT s.game_version, s.loader, s.loader_version FROM instances i
           JOIN instance_content_sets s ON s.instance_id = i.id
           WHERE i.path = ? ORDER BY s.id = i.applied_content_set_id DESC LIMIT 1""",
        # Older app versions.
        "SELECT game_version, mod_loader, mod_loader_version FROM profiles WHERE path = ?",
    )
    try:
        for query in queries:
            try:
                row = connection.execute(query, (folder,)).fetchone()
            except sqlite3.Error:
                continue
            if row and row[0]:
                return GameVersion(row[0], _normal_loader(row[1]), row[2] or None)
        return None
    finally:
        connection.close()


def _read_curseforge(instance: Instance) -> GameVersion | None:
    try:
        meta = json.loads((instance.path / "minecraftinstance.json").read_text())
    except (OSError, ValueError):
        return None
    mc_version = meta.get("gameVersion")
    if not mc_version:
        return None
    base = meta.get("baseModLoader") or {}
    # name is like "neoforge-21.1.264", "forge-47.2.0" or "fabric-0.16.9-1.21.1".
    name = base.get("name") or ""
    loader, _, rest = name.partition("-")
    version = base.get("forgeVersion") or rest.split("-")[0] or None
    return GameVersion(mc_version, _normal_loader(loader), version if loader else None)


def read_version(instance: Instance, args: argparse.Namespace) -> GameVersion:
    if args.mc_version or args.loader:
        if not (args.mc_version and args.loader):
            raise SetupError("Give both --mc-version and --loader (or neither).")
        version = GameVersion(args.mc_version, _normal_loader(args.loader), args.loader_version)
    else:
        reader = {"Modrinth": _read_modrinth, "CurseForge": _read_curseforge}.get(instance.launcher)
        found = reader(instance) if reader else None
        if found is None:
            raise SetupError(
                f"Could not read the Minecraft version and loader of {instance.path} "
                f"({instance.launcher}). Give them with --mc-version and --loader "
                "(and --loader-version)."
            )
        version = found
    if version.loader not in LOADERS:
        raise SetupError(
            f"The {version.loader} loader is not supported (only {', '.join(LOADERS)})."
        )
    if version.loader != "vanilla" and not version.loader_version:
        raise SetupError(f"No {version.loader} version known: give it with --loader-version.")
    for value in (version.mc_version, version.loader_version):
        if value is not None and not SAFE_VERSION.match(value):
            raise SetupError(f"Unexpected version string: {value!r}")
    return version


# ------------------------------------------------------------------- Java and ports


def java_for(mc_version: str) -> int:
    """The Java version a Minecraft version's server needs."""
    # Releases (and their -pre/-rc builds): 1.21.1, 26.3. Snapshots like 24w14a are not.
    match = re.match(r"^(\d+)\.(\d+)(?:\.(\d+))?(?:-|$)", mc_version)
    if not match:
        raise SetupError(
            f"Minecraft {mc_version!r} is not a release version, so its Java is unknown."
        )
    if int(match[1]) >= 26:  # 26.x and later
        return 25
    minor, patch = int(match[2]), int(match[3] or 0)
    if minor <= 16:
        return 8
    if minor < 20 or (minor == 20 and patch <= 4):
        return 17
    return 21


def conf_values(conf: Path, names: list[str]) -> dict[str, str]:
    """Variables from a server.conf, read by sourcing it (as docker.sh does)."""
    script = 'source "$1" >/dev/null || exit 1; shift; for v; do printf "%s\\0" "${!v:-}"; done'
    result = subprocess.run(
        ["bash", "-c", script, "bash", str(conf), *names], capture_output=True, check=False
    )
    if result.returncode != 0:
        return {}
    return dict(zip(names, result.stdout.decode().split("\0"), strict=False))


def docker_confs() -> list[Path]:
    return sorted((REPO_ROOT / "docker" / "servers").glob("*/server.conf"))


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, check=False)


def java_image(java: int) -> str:
    """itzg/minecraft-server:javaN pinned by digest: another server's pin, else pulled now."""
    tag = f"itzg/minecraft-server:java{java}"
    for conf in docker_confs():
        image = conf_values(conf, ["JAVA_IMAGE"]).get("JAVA_IMAGE", "")
        if image.startswith(f"{tag}@sha256:"):
            print(f"Java {java}: using the image {conf.parent.name} already pins.")
            return image
    print(f"Java {java}: no server pins {tag} yet; pulling it...")
    pulled = _run(["docker", "pull", tag])
    if pulled.returncode != 0:
        raise SetupError(f"docker pull {tag} failed:\n{pulled.stderr.strip()}")
    digest = _run(["docker", "image", "inspect", tag, "--format", "{{index .RepoDigests 0}}"])
    _, _, sha = digest.stdout.strip().partition("@")
    if digest.returncode != 0 or not sha.startswith("sha256:"):
        raise SetupError(f"Could not read the digest of {tag}.")
    print(f"Pinned {tag}@{sha}")
    return f"{tag}@{sha}"


def free_ports() -> tuple[int, int]:
    """The next game and RCON ports no other Docker server uses (legacy is ignored)."""
    used = set(RESERVED_PORTS)
    for conf in docker_confs():
        for value in conf_values(conf, ["GAME_PORT", "RCON_PORT"]).values():
            if value.isdigit():
                used.add(int(value))

    def next_free(port: int) -> int:
        while port in used or port_busy(port):
            port += 1
        used.add(port)
        return port

    return next_free(FIRST_GAME_PORT), next_free(FIRST_RCON_PORT)


def port_busy(port: int) -> bool:
    """Something on this PC already listens on the port (another program, a legacy server)."""
    with socket.socket() as probe:
        try:
            probe.bind(("", port))
        except OSError:
            return True
    return False


def admin_name() -> str | None:
    """The ADMIN_NAME the other Docker servers use, if they agree on one."""
    names = {conf_values(conf, ["ADMIN_NAME"]).get("ADMIN_NAME", "") for conf in docker_confs()}
    names.discard("")
    return names.pop() if len(names) == 1 else None


# ------------------------------------------------------------------------ downloads


def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return response.read()
    except urllib.error.HTTPError as error:
        kind = NotFoundError if error.code == 404 else SetupError
        raise kind(f"Download failed ({error.code}): {url}") from error
    except (urllib.error.URLError, TimeoutError) as error:
        raise SetupError(f"Download failed ({error}): {url}") from error


def download(url: str, target: Path, sha1: str | None = None) -> None:
    """Save url to target; if sha1 is given (the published checksum), it must match."""
    print(f"Downloading {url}")
    data = fetch(url)
    if sha1 is not None and hashlib.sha1(data).hexdigest() != sha1.strip().lower():
        raise SetupError(f"Checksum mismatch for {url}: the download is not the published file.")
    target.write_bytes(data)


def _maven_sha1(url: str) -> str:
    return fetch(url + ".sha1").decode().split()[0]


def get_vanilla(mc_version: str, data: Path) -> str:
    manifest = json.loads(fetch("https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"))
    entry = next((v for v in manifest["versions"] if v["id"] == mc_version), None)
    if entry is None:
        raise SetupError(f"Mojang has no Minecraft version {mc_version}.")
    meta_data = fetch(entry["url"])
    if hashlib.sha1(meta_data).hexdigest() != entry["sha1"]:
        raise SetupError(f"Checksum mismatch for {entry['url']}.")
    server = json.loads(meta_data)["downloads"].get("server")
    if not server:
        raise SetupError(f"Minecraft {mc_version} has no server download.")
    jar = f"minecraft_server.{mc_version}.jar"
    download(server["url"], data / jar, server["sha1"])
    return jar


def get_fabric(mc_version: str, loader_version: str, data: Path) -> str:
    installers = json.loads(fetch("https://meta.fabricmc.net/v2/versions/installer"))
    installer = next((i["version"] for i in installers if i.get("stable")), None)
    if installer is None:
        raise SetupError("Fabric lists no stable installer.")
    jar = f"fabric-server-mc.{mc_version}-loader.{loader_version}-launcher.{installer}.jar"
    # Fabric publishes no checksum for this; it comes over HTTPS from Fabric's own server.
    download(
        f"https://meta.fabricmc.net/v2/versions/loader/{mc_version}/{loader_version}/"
        f"{installer}/server/jar",
        data / jar,
    )
    return jar


def _installer_urls(version: GameVersion) -> list[str]:
    mc, loader_version = version.mc_version, version.loader_version
    if version.loader == "forge":
        base = "https://maven.minecraftforge.net/net/minecraftforge/forge"
        # Old versions (e.g. 1.7.10) carry the Minecraft version at the end as well.
        return [
            f"{base}/{v}/forge-{v}-installer.jar"
            for v in (f"{mc}-{loader_version}", f"{mc}-{loader_version}-{mc}")
        ]
    if mc == "1.20.1":  # NeoForge for 1.20.1 was still published as "forge"
        base = "https://maven.neoforged.net/releases/net/neoforged/forge"
        return [f"{base}/{mc}-{loader_version}/forge-{mc}-{loader_version}-installer.jar"]
    base = "https://maven.neoforged.net/releases/net/neoforged/neoforge"
    return [f"{base}/{loader_version}/neoforge-{loader_version}-installer.jar"]


def get_forge(version: GameVersion, image: str, data: Path) -> str:
    """Run the official (Neo)Forge installer in the Java image; return the jar to run."""
    installer = data / "installer.jar"
    last_error: SetupError | None = None
    for url in _installer_urls(version):
        try:
            sha1 = _maven_sha1(url)
        except NotFoundError as error:  # try the next naming; other errors stop here
            last_error = error
            continue
        download(url, installer, sha1)
        break
    else:
        raise SetupError(
            f"No {version.loader} installer found for {version.loader_version}."
        ) from last_error

    print(f"Running the {version.loader} installer (it downloads the server's libraries)...")
    # Kept as flag-and-value pairs on a few lines; the formatter would put each word on its own.
    # fmt: off
    result = _run([
        "docker", "run", "--rm", "--user", f"{os.getuid()}:{os.getgid()}",
        "-e", "HOME=/tmp", "-v", f"{data}:/data", "-w", "/data",
        "--entrypoint", "java", image, "-jar", "installer.jar", "--installServer",
    ])
    # fmt: on
    installer.unlink(missing_ok=True)
    for log in data.glob("installer.jar*.log"):
        log.unlink()
    if result.returncode != 0:
        tail = "\n".join(result.stdout.splitlines()[-15:] + result.stderr.splitlines()[-15:])
        raise SetupError(f"The {version.loader} installer failed:\n{tail}")

    if (data / "run.sh").is_file():
        # Forge 1.17+ and NeoForge install a run.sh, not a jar: add NeoForge's launcher jar,
        # which starts what run.sh would.
        download(SERVER_STARTER_URL, data / "server.jar")
        return "server.jar"
    jars = sorted(p.name for p in data.glob("forge-*.jar") if not p.name.endswith("-installer.jar"))
    if not jars:
        raise SetupError("The Forge installer finished but left no server jar.")
    # 1.7.10-1.12 name it ...-universal.jar; prefer that if there are several.
    return next((j for j in jars if "universal" in j), jars[0])


# ---------------------------------------------------------------------- the server


NAME = re.compile(r"^[a-z][a-z0-9-]*$")


def check_name(name: str) -> Path:
    if not NAME.match(name):
        raise SetupError(
            f"Bad name {name!r}: use lowercase letters, digits and dashes, starting with a "
            "letter (like my-server)."
        )
    folder = REPO_ROOT / "docker" / "servers" / name
    if folder.exists():
        raise SetupError(f"{folder} already exists.")
    if name in sync.find_servers():
        raise SetupError(f"A server named {name} already exists.")
    return folder


def copy_mods(instance: Instance, folder: Path, name: str) -> None:
    if not (instance.path / "mods").is_dir():
        print("The instance has no mods/ folder: no mods copied.")
        return
    server = sync.Server(
        name=name,
        root=folder / "data",
        backup_dir=folder / "backups",
        jar_file="",
        profile_dir=instance.path,
        exclude=[],
        config_skip=[],
    )
    plan = sync.build_plan(server)
    if plan.modrinth_note:
        print(plan.modrinth_note)
    sync.apply_plan(server, plan)
    print(
        f"Copied {len(plan.mod_changes)} mod(s) and {len(plan.config_changes)} config file(s); "
        f"left out {len(plan.client_only)} client-only mod(s)."
    )
    if plan.client_only:
        print("  Left out: " + ", ".join(sorted(m.rel for m in plan.client_only)))
    for extra in EXTRA_FOLDERS:
        if (instance.path / extra).is_dir():
            shutil.copytree(instance.path / extra, folder / "data" / extra, symlinks=True)
            print(f"Copied {extra}/.")


def bash_path(path: Path) -> str:
    """The path as a Bash value, using $HOME when it is inside it."""
    try:
        rest = path.relative_to(HOME).as_posix()
    except ValueError:
        return shlex.quote(str(path))
    if re.search(r'["$`\\!]', rest):
        return shlex.quote(str(path))
    return f'"$HOME/{rest}"'


def server_conf(
    *,
    name: str,
    instance: Instance,
    version: GameVersion,
    image: str,
    jar: str,
    ram: str,
    ports: tuple[int, int],
    admin: str | None,
) -> str:
    loader = (
        version.loader
        if version.loader == "vanilla"
        else (f"{version.loader} {version.loader_version}")
    )
    admin_line = f"ADMIN_NAME={shlex.quote(admin)}" if admin else "#ADMIN_NAME=Alex"
    return f"""\
# {instance.name} (Minecraft {version.mc_version}, {loader}).
# Made by ./docker.sh new on {date.today().isoformat()} from the {instance.launcher} \
instance {instance.path.name!r}.
# Field meanings: docker/server.conf.example.
NAME={name}

JAVA_IMAGE={image}
MC_VERSION={version.mc_version}
JAR_FILE={shlex.quote(jar)}
RAM_ALLOCATION={ram}
JAVA_ARGS=()

GAME_PORT={ports[0]}
RCON_PORT={ports[1]}

{admin_line}

PROFILE_DIR={bash_path(instance.path)}
SYNC_EXCLUDE=()
SYNC_CONFIG_SKIP=()
"""


def make_server(args: argparse.Namespace) -> Path:
    folder = check_name(args.name)
    if not re.match(r"^[1-9][0-9]*[MG]$", args.ram):
        raise SetupError(f"Bad --ram {args.ram!r}: use a size like 8G or 6144M.")
    instance = find_instance(args.instance)
    version = read_version(instance, args)
    print(
        f"Instance: {instance.name} ({instance.launcher}, {instance.path})\n"
        f"Minecraft {version.mc_version}, {version.loader} {version.loader_version or ''}".rstrip()
    )
    image = java_image(java_for(version.mc_version))
    ports = free_ports()

    data = folder / "data"
    folder.parent.mkdir(parents=True, exist_ok=True)
    try:
        folder.mkdir()  # outside the clean-up below: never remove a folder this didn't make
    except FileExistsError as error:
        raise SetupError(f"{folder} already exists.") from error
    try:
        data.mkdir()
        (folder / "backups").mkdir()
        if version.loader == "vanilla":
            jar = get_vanilla(version.mc_version, data)
        elif version.loader == "fabric":
            jar = get_fabric(version.mc_version, str(version.loader_version), data)
        else:
            jar = get_forge(version, image, data)
        if args.no_mods:
            print("--no-mods: no mods copied.")
        else:
            copy_mods(instance, folder, args.name)
        (folder / "server.conf").write_text(
            server_conf(
                name=args.name,
                instance=instance,
                version=version,
                image=image,
                jar=jar,
                ram=args.ram,
                ports=ports,
                admin=admin_name(),
            )
        )
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        print(f"Removed the half-made {folder}.", file=sys.stderr)
        raise
    return folder


def main() -> int:
    parser = argparse.ArgumentParser(prog="./docker.sh new", description=__doc__.splitlines()[0])
    parser.add_argument("name", help="the new server's name (lowercase, like my-server)")
    parser.add_argument("instance", help="instance folder, or a Modrinth/CurseForge instance name")
    parser.add_argument("--no-mods", action="store_true", help="copy no mods or configs")
    parser.add_argument("--ram", default="8G", help="heap size (default 8G)")
    parser.add_argument("--mc-version", help="Minecraft version, if it can't be read")
    parser.add_argument("--loader", help=f"loader ({', '.join(LOADERS)}), if it can't be read")
    parser.add_argument("--loader-version", help="loader version, with --loader")
    args = parser.parse_args()
    try:
        folder = make_server(args)
    except SetupError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    conf = folder / "server.conf"
    ports = conf_values(conf, ["GAME_PORT", "RCON_PORT"])
    print(
        f"\nMade {folder.relative_to(REPO_ROOT)}/ (not started, nothing committed).\n"
        f"  Game port {ports.get('GAME_PORT')}, RCON port {ports.get('RCON_PORT')}.\n"
        "Next:\n"
        f"  1. Check {conf.relative_to(REPO_ROOT)} (RAM, ports, ADMIN_NAME).\n"
        f"  2. ./docker.sh {args.name} up   (the first start writes server.properties)\n"
        f"  3. Later mod updates: ./docker.sh {args.name} sync"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
