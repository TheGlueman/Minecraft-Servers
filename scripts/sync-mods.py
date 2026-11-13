#!/usr/bin/env python3
"""Sync a server's mods and configs from its client profile (any launcher).

Usage: scripts/sync-mods.py SERVER_NAME [--dry-run]

The server's server.conf sets PROFILE_DIR (a launcher profile/instance folder with
mods/ and config/), SYNC_EXCLUDE (mods never to copy) and SYNC_CONFIG_SKIP (config
paths never to overwrite). The script prints the whole plan, asks one yes/no, backs
up the server's current mods/ and config/, then applies the plan. It never deletes
mods or configs that exist only on the server.
"""

from __future__ import annotations

import argparse
import difflib
import filecmp
import fnmatch
import json
import re
import shutil
import subprocess
import sys
import tomllib
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
# Generated or per-machine files that are never worth syncing.
CONFIG_IGNORE_SUFFIXES = (".lock", ".log", ".bak", ".old", ".tmp", ".zip", ".7z", ".jar")
DIFF_LINE_LIMIT = 40


# --------------------------------------------------------------------------- config

@dataclass
class Server:
    name: str
    root: Path
    jar_file: str
    profile_dir: Path | None
    exclude: list[str]
    config_skip: list[str]


def read_server_conf(conf: Path) -> Server | None:
    """Read server.conf the same way the Bash scripts do: by sourcing it.

    Values come back NUL-separated; each list is preceded by its length, so empty
    values and empty lists can't shift the fields after them.
    """
    script = (
        'source "$1" >/dev/null || exit 1; '
        'printf "%s\\0" "${NAME:-}" "${JAR_FILE:-}" "${PROFILE_DIR:-}"; '
        'printf "%s\\0" "${#SYNC_EXCLUDE[@]}" ${SYNC_EXCLUDE[@]+"${SYNC_EXCLUDE[@]}"}; '
        'printf "%s\\0" "${#SYNC_CONFIG_SKIP[@]}" ${SYNC_CONFIG_SKIP[@]+"${SYNC_CONFIG_SKIP[@]}"}'
    )
    result = subprocess.run(["bash", "-c", script, "bash", str(conf)], capture_output=True)
    if result.returncode != 0:
        return None
    fields = result.stdout.decode().split("\0")
    try:
        name, jar_file, profile = fields[0:3]
        n_exclude = int(fields[3])
        exclude = fields[4:4 + n_exclude]
        n_skip = int(fields[4 + n_exclude])
        config_skip = fields[5 + n_exclude:5 + n_exclude + n_skip]
    except (ValueError, IndexError):
        return None
    if not name:
        return None
    return Server(
        name=name.lower(),
        # Docker servers (docker/<name>/server.conf) keep their files in server/ next to
        # it; run.sh servers keep server.conf inside the server folder itself.
        root=conf.parent / "server" if conf.parent.parent == REPO_ROOT / "docker" else conf.parent,
        jar_file=jar_file,
        profile_dir=Path(profile) if profile else None,
        exclude=[e for e in exclude if e],
        config_skip=[c for c in config_skip if c],
    )


def find_servers() -> dict[str, Server]:
    servers: dict[str, Server] = {}
    # Legacy run.sh servers (legacy/<folder>/server.conf) and Docker servers
    # (docker/<name>/server.conf).
    confs = (sorted(REPO_ROOT.glob("legacy/*/server.conf"))
             + sorted(REPO_ROOT.glob("docker/*/server.conf")))
    for conf in confs:
        server = read_server_conf(conf)
        if not server:
            continue
        if server.name in servers:
            sys.exit(f"Two servers are named {server.name!r}: {servers[server.name].root} and "
                     f"{server.root}. Give each server.conf a different NAME=.")
        servers[server.name] = server
    return servers


# --------------------------------------------------------------------- mod reading

@dataclass
class ModInfo:
    path: Path
    rel: str              # path inside mods/, e.g. "1.7.10/CodeChickenLib.jar"
    mod_id: str           # lower-case ID used to match profile and server copies
    version: str | None   # None when the file doesn't say
    loader: str           # fabric, forge-legacy, neoforge, forge, unknown
    client_only: bool


# Forge 1.7.10 also loads mods packaged as .zip, so both are opened for metadata.
ARCHIVE_SUFFIXES = (".jar", ".zip")


def _load_json(text: str):
    try:
        return json.loads(text, strict=False)
    except json.JSONDecodeError:
        # Some mods ship JSON with // comments.
        return json.loads(re.sub(r"(?m)^\s*//.*$", "", text), strict=False)


def _real_version(version: str | None, manifest: str | None) -> str | None:
    """Drop build placeholders like ${version}; fall back to the jar manifest."""
    if version and "${" not in version:
        return version
    if manifest:
        match = re.search(r"(?m)^Implementation-Version:\s*(\S+)", manifest)
        if match:
            return match.group(1)
    return None


def read_mod(path: Path, rel: str) -> ModInfo:
    """Work out a file's mod ID, version and side from whatever metadata it has.

    Files without readable metadata get their path inside mods/ as their ID, so they
    are matched between profile and server by file name.
    """
    unknown = ModInfo(path, rel, rel.lower(), None, "unknown", False)
    if not path.name.lower().endswith(ARCHIVE_SUFFIXES):
        return unknown
    try:
        with zipfile.ZipFile(path) as jar:
            names = set(jar.namelist())

            def read(name: str) -> str:
                return jar.read(name).decode("utf-8", "replace")

            manifest = read("META-INF/MANIFEST.MF") if "META-INF/MANIFEST.MF" in names else None

            if "fabric.mod.json" in names:
                data = _load_json(read("fabric.mod.json"))
                if data.get("id"):
                    return ModInfo(path, rel, str(data["id"]).lower(),
                                   _real_version(str(data.get("version", "")), manifest),
                                   "fabric", data.get("environment") == "client")

            for toml_name, loader in (("META-INF/neoforge.mods.toml", "neoforge"),
                                      ("META-INF/mods.toml", "forge")):
                if toml_name in names:
                    first = (tomllib.loads(read(toml_name)).get("mods") or [{}])[0]
                    if first.get("modId"):
                        return ModInfo(path, rel, str(first["modId"]).lower(),
                                       _real_version(first.get("version"), manifest),
                                       loader, False)

            if "mcmod.info" in names:
                data = _load_json(read("mcmod.info"))
                entries = data.get("modList", []) if isinstance(data, dict) else data
                first = entries[0] if entries else {}
                if first.get("modid"):
                    return ModInfo(path, rel, str(first["modid"]).lower(),
                                   _real_version(first.get("version"), manifest),
                                   "forge-legacy", False)
    except (zipfile.BadZipFile, OSError, ValueError, tomllib.TOMLDecodeError, KeyError,
            IndexError, AttributeError, TypeError):
        pass
    return unknown


def mod_files(folder: Path) -> list[tuple[Path, str]]:
    """Every file under mods/ (subfolders included), skipping hidden and .disabled ones."""
    files = []
    for path in folder.rglob("*"):
        rel = path.relative_to(folder)
        if not path.is_file() or any(part.startswith(".") for part in rel.parts):
            continue
        if path.name.lower().endswith(".disabled"):
            continue
        files.append((path, rel.as_posix()))
    return sorted(files, key=lambda item: item[1].casefold())


def disabled_files(folder: Path) -> list[str]:
    """Mods the launcher has switched off (name ends in .disabled)."""
    return sorted((p.relative_to(folder).as_posix() for p in folder.rglob("*.disabled")
                   if p.is_file()), key=str.casefold)


def read_mods(folder: Path) -> dict[str, ModInfo]:
    mods: dict[str, ModInfo] = {}
    for path, rel in mod_files(folder):
        info = read_mod(path, rel)
        key = info.mod_id
        if key in mods:  # two files claim one ID: keep both, keyed apart by path
            key = f"{key} ({rel})"
        mods[key] = info
    return mods


# ------------------------------------------------------------------ version compare

# Single letters are left out on purpose: "3.2a" is usually a patch after 3.2, not an alpha.
_PRE_RELEASE = {"alpha": -3, "beta": -2, "pre": -1, "rc": -1}


def _version_key(version: str) -> list[tuple[int, int | str]]:
    key: list[tuple[int, int | str]] = []
    for token in re.findall(r"\d+|[a-zA-Z]+", version):
        if token.isdigit():
            key.append((2, int(token)))
        elif token.lower() in _PRE_RELEASE:
            key.append((0, _PRE_RELEASE[token.lower()]))
        else:
            key.append((1, token.lower()))
    return key


def compare_versions(a: str | None, b: str | None) -> int | None:
    """-1 if a < b, 0 if equal, 1 if a > b, None if it can't tell."""
    if not a or not b:
        return None
    if a == b:
        return 0
    # Compare the part before "+" first: "+mc26.3" / "+fabric-26.3" is usually build
    # metadata. Only when that part is equal does the rest decide (0.7.7+fix2 < +fix3).
    core = _compare_keys(_version_key(a.split("+")[0]), _version_key(b.split("+")[0]))
    if core != 0:
        return core
    return _compare_keys(_version_key(a), _version_key(b))


def _compare_keys(ka: list, kb: list) -> int | None:
    if not ka or not kb:
        return None
    for x, y in zip(ka, kb):
        if x == y:
            continue
        if x[0] != y[0] or type(x[1]) is not type(y[1]):
            return None
        return -1 if x[1] < y[1] else 1
    if len(ka) == len(kb):
        return 0
    # 1.2 vs 1.2.1: the longer is newer, unless the extra part is a pre-release tag.
    longer, sign = (ka, 1) if len(ka) > len(kb) else (kb, -1)
    extra = longer[min(len(ka), len(kb))]
    return -sign if extra[0] == 0 else sign


# --------------------------------------------------------------------------- plan

@dataclass
class ModChange:
    kind: str                 # add, update, downgrade, replace
    new: ModInfo
    old: ModInfo | None = None


@dataclass
class ConfigChange:
    kind: str                 # add, update
    rel: Path
    source: Path
    target: Path


@dataclass
class Plan:
    mod_changes: list[ModChange]
    client_only: list[ModInfo]
    excluded: list[ModInfo]
    unchanged: list[ModInfo]
    server_only: list[ModInfo]
    config_changes: list[ConfigChange]
    config_skipped: list[Path]
    disabled: list[str]
    skipped_but_installed: list[ModInfo]


def is_excluded(info: ModInfo, patterns: list[str]) -> bool:
    haystacks = (info.mod_id.lower(), info.rel.lower())
    return any(p.lower() in h for p in patterns for h in haystacks)


def plan_mods(server: Server, plan: Plan, profile_mods: dict[str, ModInfo],
              server_mods: dict[str, ModInfo]) -> list[str]:
    """Fill in the mod part of the plan; return the IDs the server will have."""
    kept_ids = []
    for key, new in profile_mods.items():
        skip_list = (plan.client_only if new.client_only
                     else plan.excluded if is_excluded(new, server.exclude) else None)
        if skip_list is not None:
            skip_list.append(new)
            if key in server_mods:  # never removed, but say it's there
                plan.skipped_but_installed.append(server_mods[key])
            continue
        kept_ids.append(new.mod_id)
        old = server_mods.get(key)
        if old is None:
            plan.mod_changes.append(ModChange("add", new))
        elif filecmp.cmp(old.path, new.path, shallow=False):
            plan.unchanged.append(new)
        else:
            order = compare_versions(new.version, old.version)
            kind = {1: "update", -1: "downgrade", 0: "replace"}.get(order, "update")
            plan.mod_changes.append(ModChange(kind, new, old))
    for key, old in server_mods.items():
        if key not in profile_mods:
            plan.server_only.append(old)
            kept_ids.append(old.mod_id)
    return kept_ids


_COMMENT = re.compile(rb"^\s*(#|//|;)")


def same_config(a: Path, b: Path) -> bool:
    """Equal, ignoring comment lines (many mods stamp the save time in a comment)."""
    if filecmp.cmp(a, b, shallow=False):
        return True
    try:
        lines_a, lines_b = a.read_bytes().splitlines(), b.read_bytes().splitlines()
    except OSError:
        return False
    return ([l for l in lines_a if not _COMMENT.match(l)]
            == [l for l in lines_b if not _COMMENT.match(l)])


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def plan_configs(server: Server, plan: Plan, kept_ids: list[str]) -> None:
    """Copy a profile config when the server already has it, or it belongs to a server mod."""
    source_root = server.profile_dir / "config"
    target_root = server.root / "config"
    if not source_root.is_dir():
        return
    mod_names = {_norm(m) for m in kept_ids if len(_norm(m)) >= 3}
    def skipped(rel: Path) -> bool:
        """SYNC_CONFIG_SKIP entries are paths or wildcards; a folder skips all inside it."""
        candidates = [rel.as_posix()] + [p.as_posix() for p in rel.parents if p != Path(".")]
        return any(fnmatch.fnmatchcase(c.lower(), pattern.lower())
                   for pattern in server.config_skip for c in candidates)

    for source in sorted(p for p in source_root.rglob("*") if p.is_file()):
        rel = source.relative_to(source_root)
        if source.name.endswith(CONFIG_IGNORE_SUFFIXES):
            continue
        target = target_root / rel
        if skipped(rel):
            if target.exists() and not same_config(source, target):
                plan.config_skipped.append(rel)
            continue
        if target.exists():
            if not same_config(source, target):
                plan.config_changes.append(ConfigChange("update", rel, source, target))
            continue
        top = _norm(Path(rel.parts[0]).stem)
        if top and any(m in top or top in m for m in mod_names if len(top) >= 3):
            plan.config_changes.append(ConfigChange("add", rel, source, target))


def build_plan(server: Server) -> Plan:
    plan = Plan([], [], [], [], [], [], [], disabled_files(server.profile_dir / "mods"), [])
    profile_mods = read_mods(server.profile_dir / "mods")
    server_mods = read_mods(server.root / "mods")
    kept_ids = plan_mods(server, plan, profile_mods, server_mods)
    plan_configs(server, plan, kept_ids)
    return plan


# ------------------------------------------------------------------------- output

def _ver(info: ModInfo | None) -> str:
    return (info.version or "version unknown") if info else "-"


def _diff(change: ConfigChange) -> list[str]:
    try:
        old = change.target.read_text(errors="replace").splitlines()
        new = change.source.read_text(errors="replace").splitlines()
    except OSError:
        return ["    (could not read for a diff)"]
    lines = list(difflib.unified_diff(old, new, "server", "profile", lineterm="", n=1))[2:]
    if not lines:
        return ["    (only whitespace or line endings differ)"]
    shown = [f"    {line}" for line in lines[:DIFF_LINE_LIMIT]]
    if len(lines) > DIFF_LINE_LIMIT:
        shown.append(f"    ... {len(lines) - DIFF_LINE_LIMIT} more diff lines")
    return shown


def print_plan(server: Server, plan: Plan) -> None:
    print(f"Sync plan for {server.name}: {server.profile_dir}  ->  {server.root}\n")

    downgrades = [c for c in plan.mod_changes if c.kind == "downgrade"]
    if downgrades:
        print("!! WARNING: these mods would be DOWNGRADED (profile is older than server):")
        for c in downgrades:
            print(f"!!   {c.new.mod_id}: {_ver(c.old)} -> {_ver(c.new)}")
        print()

    sections = [
        ("Add", [c for c in plan.mod_changes if c.kind == "add"],
         lambda c: f"{c.new.rel}  ({_ver(c.new)})"),
        ("Update", [c for c in plan.mod_changes if c.kind == "update"],
         lambda c: f"{c.new.mod_id}: {_ver(c.old)} -> {_ver(c.new)}  ({c.new.rel})"),
        ("Downgrade", downgrades,
         lambda c: f"{c.new.mod_id}: {_ver(c.old)} -> {_ver(c.new)}  ({c.new.rel})"),
        ("Replace (same version, different file)", [c for c in plan.mod_changes if c.kind == "replace"],
         lambda c: f"{c.old.rel} -> {c.new.rel}"),
    ]
    for title, items, fmt in sections:
        if items:
            print(f"{title} ({len(items)}):")
            for item in items:
                print(f"  {fmt(item)}")
            print()

    for title, items in (("Kept, only on the server (not touched)", plan.server_only),
                         ("Skipped, excluded in server.conf", plan.excluded),
                         ("Skipped, client-only", plan.client_only)):
        if items:
            names = ", ".join(sorted(i.mod_id for i in items))
            print(f"{title} ({len(items)}): {names}\n")
    if plan.skipped_but_installed:
        print("!! Installed on the server although client-only or excluded (left in place; remove "
              "by hand if unwanted):")
        for info in plan.skipped_but_installed:
            print(f"!!   {info.rel}")
        print()
    if plan.disabled:
        print(f"Skipped, disabled in the profile ({len(plan.disabled)}): "
              f"{', '.join(plan.disabled)}\n")
    print(f"Unchanged mods: {len(plan.unchanged)}\n")

    if plan.config_changes:
        print(f"Config changes ({len(plan.config_changes)}):")
        for c in plan.config_changes:
            if c.kind == "add":
                print(f"  + config/{c.rel}  (new)")
            else:
                print(f"  ~ config/{c.rel}")
                print("\n".join(_diff(c)))
        print()
    else:
        print("Config changes: none\n")
    if plan.config_skipped:
        names = ", ".join(str(p) for p in plan.config_skipped)
        print(f"Config differences left alone (SYNC_CONFIG_SKIP): {names}\n")


# -------------------------------------------------------------------------- apply

def server_is_running(server: Server) -> str | None:
    """Return a description if the server is running natively or in Docker."""
    root = server.root.resolve()
    jar = str(root / server.jar_file) if server.jar_file else None
    for proc in Path("/proc").glob("[0-9]*"):
        try:
            cmdline = (proc / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
            if "java" not in cmdline:
                continue
            if (jar and jar in cmdline) or Path(proc / "cwd").resolve() == root:
                return f"Java process {proc.name}"
        except OSError:
            continue

    if shutil.which("docker"):
        result = subprocess.run(["docker", "ps", "-q"], capture_output=True, text=True)
        ids = result.stdout.split()
        if result.returncode == 0 and ids:
            inspect = subprocess.run(
                ["docker", "inspect", "--format",
                 "{{.Name}} {{range .Mounts}}{{.Source}}|{{end}}", *ids],
                capture_output=True, text=True)
            for line in inspect.stdout.splitlines():
                name, _, mounts = line.partition(" ")
                if any(Path(m).resolve() == root for m in mounts.split("|") if m):
                    return f"Docker container {name.lstrip('/')}"
    return None


def backup_current(server: Server) -> Path:
    stamp = datetime.now().strftime("%y-%m-%d_%H-%M")
    dest = server.root / "backups" / f"mods-config_{stamp}"
    suffix = 2
    while dest.exists():
        dest = server.root / "backups" / f"mods-config_{stamp}_{suffix}"
        suffix += 1
    dest.mkdir(parents=True)
    for folder in ("mods", "config"):
        if (server.root / folder).is_dir():
            shutil.copytree(server.root / folder, dest / folder, symlinks=True)
    return dest


def apply_plan(server: Server, plan: Plan) -> None:
    mods_dir = server.root / "mods"
    for change in plan.mod_changes:
        if change.old is not None and change.old.rel != change.new.rel:
            change.old.path.unlink()
        target = mods_dir / change.new.rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(change.new.path, target)
    for change in plan.config_changes:
        change.target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(change.source, change.target)
    write_mods_txt(server.root)


def write_mods_txt(root: Path) -> None:
    """Record the server's mod jars, one per line, so mod changes show up in git."""
    names = [rel for _, rel in mod_files(root / "mods")] + disabled_files(root / "mods")
    (root / "mods.txt").write_text("".join(f"{n}\n" for n in sorted(names, key=str.casefold)))


# --------------------------------------------------------------------------- main

def main() -> int:
    servers = find_servers()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("server", help="server name: " + ", ".join(servers))
    parser.add_argument("--dry-run", action="store_true", help="only show the plan")
    args = parser.parse_args()

    server = servers.get(args.server.lower())
    if server is None:
        print(f"Unknown server: {args.server} (known: {', '.join(servers)})", file=sys.stderr)
        return 2
    if server.profile_dir is None:
        print(f"PROFILE_DIR is not set in the server.conf of {server.name}", file=sys.stderr)
        return 1
    if not (server.profile_dir / "mods").is_dir():
        print(f"Profile has no mods folder: {server.profile_dir}", file=sys.stderr)
        return 1
    if not (server.root / "mods").is_dir():
        print(f"Server has no mods folder: {server.root}", file=sys.stderr)
        return 1

    plan = build_plan(server)
    print_plan(server, plan)

    if not plan.mod_changes and not plan.config_changes:
        print("Nothing to do: the server already matches the profile.")
        return 0
    if args.dry_run:
        print("Dry run: nothing was changed.")
        return 0

    running = server_is_running(server)
    if running:
        print(f"Refusing to sync: {server.name} is running ({running}). Stop it first.",
              file=sys.stderr)
        return 1

    try:
        answer = input("Apply this plan? [y/N] ").strip().lower()
    except EOFError:
        answer = ""
    if answer not in ("y", "yes"):
        print("Cancelled: nothing was changed.")
        return 1

    backup = backup_current(server)
    print(f"Backed up current mods and config to {backup}")
    apply_plan(server, plan)
    print(f"Done: {len(plan.mod_changes)} mod change(s), {len(plan.config_changes)} config "
          f"change(s). mods.txt updated.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
