"""scripts/new-server.py: finding instances, reading versions, ports, jars and the new folder.

Run with: python3 -I -m unittest discover -s tests -p 'test_*.py'   (or tests/run.sh)
Everything happens in throwaway folders with fake launchers, downloads and Docker: no
network, no containers, and the real servers and instances are not read.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import io
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "new_server", REPO_ROOT / "scripts" / "new-server.py"
)
if _spec is None or _spec.loader is None:
    raise ImportError(f"cannot load {REPO_ROOT / 'scripts' / 'new-server.py'}")
# Loaded from a file path (its name has a dash), so type checkers can't see its contents.
ns: Any = importlib.util.module_from_spec(_spec)
sys.modules["new_server"] = ns
_spec.loader.exec_module(ns)

FABRIC_LIST = "https://meta.fabricmc.net/v2/versions/installer"
MOJANG_MANIFEST = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"


def fabric_jar(path: Path, mod_id: str, client: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    meta: dict[str, str] = {"id": mod_id, "version": "1.0"}
    if client:
        meta["environment"] = "client"
    with zipfile.ZipFile(path, "w") as jar:
        jar.writestr("fabric.mod.json", json.dumps(meta))


def sha1(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


class Fakes(unittest.TestCase):
    """A fake repo, home, Modrinth and CurseForge, network and Docker."""

    def setUp(self) -> None:
        # The script reports progress as it goes; keep the test output to the results.
        self.output = io.StringIO()
        self.enterContext(contextlib.redirect_stdout(self.output))
        self.enterContext(contextlib.redirect_stderr(self.output))
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.repo = self.root / "repo"
        self.servers = self.repo / "docker" / "servers"
        self.home = self.root / "home"
        self.modrinth = self.home / "modrinth" / "profiles"
        self.curseforge = self.home / "curseforge" / "Instances"
        for folder in (self.servers, self.repo / "legacy", self.modrinth, self.curseforge):
            folder.mkdir(parents=True)
        self.write_server("existing", game=25567, rcon=25576, image="java21@sha256:aaa")

        self._saved = {
            name: getattr(ns, name)
            for name in (
                "REPO_ROOT",
                "HOME",
                "MODRINTH_DIRS",
                "CURSEFORGE_DIRS",
                "fetch",
                "_run",
                "port_busy",
            )
        }
        self._saved_sync = (ns.sync.REPO_ROOT, ns.sync.modrinth_server_sides)
        # Modrinth says zoom.jar (by its content) is client-only; nothing else is known.
        ns.sync.modrinth_server_sides = lambda sha1s: {
            sha: "unsupported" for sha in sha1s if sha in self.client_only_shas
        }
        self.client_only_shas: set[str] = set()
        ns.REPO_ROOT = ns.sync.REPO_ROOT = self.repo
        ns.HOME = self.home
        ns.MODRINTH_DIRS = [self.modrinth]
        ns.CURSEFORGE_DIRS = [self.curseforge]
        self.urls: dict[str, bytes] = {}
        self.fetched: list[str] = []
        ns.fetch = self.fake_fetch
        self.busy_ports: set[int] = set()
        ns.port_busy = lambda port: port in self.busy_ports
        self.docker_calls: list[list[str]] = []
        self.installer_makes = "run.sh"  # what the fake installer leaves behind
        self.installer_fails = False
        ns._run = self.fake_run

    def tearDown(self) -> None:
        for name, value in self._saved.items():
            setattr(ns, name, value)
        ns.sync.REPO_ROOT, ns.sync.modrinth_server_sides = self._saved_sync
        self._tmp.cleanup()

    # ---- fakes

    def write_server(self, name: str, *, game: int, rcon: int, image: str) -> None:
        folder = self.servers / name
        folder.mkdir()
        (folder / "server.conf").write_text(
            f"NAME={name}\nJAVA_IMAGE=itzg/minecraft-server:{image}\nGAME_PORT={game}\n"
            f"RCON_PORT={rcon}\nADMIN_NAME=Louis\nJAR_FILE=s.jar\n"
        )

    def fake_fetch(self, url: str) -> bytes:
        self.fetched.append(url)
        if url.endswith(".sha1") and url[: -len(".sha1")] in self.urls:
            return sha1(self.urls[url[: -len(".sha1")]]).encode() + b"  file\n"
        if url not in self.urls:
            raise ns.NotFoundError(f"Download failed (404): {url}")
        return self.urls[url]

    def fake_run(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        self.docker_calls.append(command)
        if command[:2] == ["docker", "pull"]:
            return subprocess.CompletedProcess(command, 0, "", "")
        if command[:3] == ["docker", "image", "inspect"]:
            return subprocess.CompletedProcess(command, 0, "itzg/minecraft-server@sha256:new\n", "")
        if command[:2] == ["docker", "run"]:
            data = Path(command[command.index("-v") + 1].rsplit(":", 1)[0])
            if self.installer_fails:
                return subprocess.CompletedProcess(command, 1, "", "installer exploded")
            (data / self.installer_makes).write_text("x")
            (data / "installer.jar.log").write_text("log")
            return subprocess.CompletedProcess(command, 0, "", "")
        raise AssertionError(f"unexpected command {command}")

    # ---- instances

    def modrinth_instance(
        self, folder: str, name: str, mc: str, loader: str, version: str | None
    ) -> Path:
        path = self.modrinth / folder
        path.mkdir()
        db = sqlite3.connect(self.modrinth.parent / "app.db")
        db.execute(
            "CREATE TABLE IF NOT EXISTS instances (id TEXT, path TEXT, name TEXT, "
            "applied_content_set_id TEXT)"
        )
        db.execute(
            "CREATE TABLE IF NOT EXISTS instance_content_sets (id TEXT, instance_id TEXT, "
            "game_version TEXT, loader TEXT, loader_version TEXT)"
        )
        db.execute("INSERT INTO instances VALUES (?, ?, ?, ?)", (folder, folder, name, "set-2"))
        # An older content set that is not the applied one.
        db.execute(
            "INSERT INTO instance_content_sets VALUES (?, ?, ?, ?, ?)",
            ("set-1", folder, "1.0", "forge", "1"),
        )
        db.execute(
            "INSERT INTO instance_content_sets VALUES (?, ?, ?, ?, ?)",
            ("set-2", folder, mc, loader, version),
        )
        db.commit()
        db.close()
        return path

    def curseforge_instance(self, folder: str, name: str, mc: str, loader_name: str) -> Path:
        path = self.curseforge / folder
        path.mkdir()
        loader, _, rest = loader_name.partition("-")
        meta = {
            "name": name,
            "gameVersion": mc,
            "baseModLoader": {"name": loader_name, "forgeVersion": rest.split("-")[0]}
            if loader
            else None,
        }
        (path / "minecraftinstance.json").write_text(json.dumps(meta))
        return path

    def args(self, name: str, instance: str, **extra: Any) -> argparse.Namespace:
        values = {
            "no_mods": False,
            "ram": "8G",
            "mc_version": None,
            "loader": None,
            "loader_version": None,
        }
        values.update(extra)
        return argparse.Namespace(name=name, instance=instance, **values)

    def conf(self, name: str) -> dict[str, str]:
        keys = ["NAME", "JAVA_IMAGE", "MC_VERSION", "JAR_FILE", "RAM_ALLOCATION", "GAME_PORT"]
        keys += ["RCON_PORT", "ADMIN_NAME", "PROFILE_DIR"]
        return ns.conf_values(self.servers / name / "server.conf", keys)

    # ---- download fakes

    def fabric_downloads(self, mc: str, loader: str) -> None:
        self.urls[FABRIC_LIST] = json.dumps(
            [{"version": "1.2.0", "stable": False}, {"version": "1.1.2", "stable": True}]
        ).encode()
        self.urls[
            f"https://meta.fabricmc.net/v2/versions/loader/{mc}/{loader}/1.1.2/server/jar"
        ] = b"fabric-jar"

    def vanilla_downloads(self, mc: str, jar: bytes, published: bytes | None = None) -> None:
        meta = json.dumps(
            {
                "downloads": {
                    "server": {"url": "https://x/server.jar", "sha1": sha1(published or jar)}
                }
            }
        ).encode()
        self.urls["https://x/meta.json"] = meta
        self.urls[MOJANG_MANIFEST] = json.dumps(
            {"versions": [{"id": mc, "url": "https://x/meta.json", "sha1": sha1(meta)}]}
        ).encode()
        self.urls["https://x/server.jar"] = jar


class FindInstance(Fakes):
    def setUp(self) -> None:
        super().setUp()
        self.modrinth_instance("Vanilla 1.0.0", "Vanilla", "26.3", "fabric", "0.19.5")
        self.curseforge_instance("HBM Modernised", "HBM Continuum", "1.21.1", "neoforge-21.1.264")

    def test_by_folder_or_own_name_any_case(self) -> None:
        for arg, folder in (
            ("Vanilla 1.0.0", "Vanilla 1.0.0"),
            ("vanilla", "Vanilla 1.0.0"),
            ("HBM Modernised", "HBM Modernised"),
            ("hbm continuum", "HBM Modernised"),
        ):
            with self.subTest(arg=arg):
                self.assertEqual(ns.find_instance(arg).path.name, folder)

    def test_by_path(self) -> None:
        found = ns.find_instance(str(self.curseforge / "HBM Modernised"))
        self.assertEqual((found.launcher, found.name), ("CurseForge", "HBM Continuum"))
        other = self.root / "elsewhere"
        other.mkdir()
        self.assertEqual(ns.find_instance(str(other)).launcher, "folder")
        with self.assertRaisesRegex(ns.SetupError, "No such folder"):
            ns.find_instance(str(self.root / "missing"))

    def test_a_bare_name_is_never_a_folder_here(self) -> None:
        (self.root / "Vanilla").mkdir()
        with contextlib.chdir(self.root):
            self.assertEqual(ns.find_instance("Vanilla").launcher, "Modrinth")

    def test_unknown_lists_the_known(self) -> None:
        with self.assertRaisesRegex(ns.SetupError, "Known instances: 'HBM Continuum', 'Vanilla'"):
            ns.find_instance("NTM Continuum")

    def test_duplicates_stop_and_list_both(self) -> None:
        self.curseforge_instance("Vanilla", "Something", "1.20.1", "forge-47.2.0")
        with self.assertRaisesRegex(ns.SetupError, "(?s)More than one.*Modrinth.*CurseForge"):
            ns.find_instance("vanilla")


class ReadVersion(Fakes):
    def version(self, instance: str, **extra: Any) -> tuple[str, str, str | None]:
        found = ns.read_version(ns.find_instance(instance), self.args("x", instance, **extra))
        return found.mc_version, found.loader, found.loader_version

    def test_modrinth_applied_content_set(self) -> None:
        self.modrinth_instance("V", "V", "26.3", "fabric", "0.19.5")
        self.assertEqual(self.version("V"), ("26.3", "fabric", "0.19.5"))

    def test_modrinth_vanilla(self) -> None:
        self.modrinth_instance("P", "P", "1.21.1", "vanilla", None)
        self.assertEqual(self.version("P"), ("1.21.1", "vanilla", None))

    def test_modrinth_older_app(self) -> None:
        (self.modrinth / "Old").mkdir()
        db = sqlite3.connect(self.modrinth.parent / "app.db")
        db.execute(
            "CREATE TABLE profiles (path TEXT, name TEXT, game_version TEXT, mod_loader TEXT, "
            "mod_loader_version TEXT)"
        )
        db.execute("INSERT INTO profiles VALUES ('Old', 'Old', '1.20.1', 'forge', '47.2.0')")
        db.commit()
        db.close()
        self.assertEqual(self.version("Old"), ("1.20.1", "forge", "47.2.0"))

    def test_curseforge(self) -> None:
        self.curseforge_instance("N", "N", "1.21.1", "neoforge-21.1.264")
        self.curseforge_instance("F", "F", "1.20.1", "fabric-0.16.9-1.20.1")
        self.assertEqual(self.version("N"), ("1.21.1", "neoforge", "21.1.264"))
        self.assertEqual(self.version("F"), ("1.20.1", "fabric", "0.16.9"))

    def test_by_hand(self) -> None:
        other = self.root / "other"
        other.mkdir()
        with self.assertRaisesRegex(ns.SetupError, "--mc-version and --loader"):
            self.version(str(other))
        self.assertEqual(
            self.version(str(other), mc_version="1.7.10", loader="Forge", loader_version="10.1"),
            ("1.7.10", "forge", "10.1"),
        )
        with self.assertRaisesRegex(ns.SetupError, "Give both"):
            self.version(str(other), mc_version="1.7.10")

    def test_refuses(self) -> None:
        self.curseforge_instance("Q", "Q", "1.20.1", "quilt-0.20.0")
        with self.assertRaisesRegex(ns.SetupError, "quilt loader is not supported"):
            self.version("Q")
        other = self.root / "other"
        other.mkdir()
        with self.assertRaisesRegex(ns.SetupError, "--loader-version"):
            self.version(str(other), mc_version="1.21.1", loader="fabric")
        with self.assertRaisesRegex(ns.SetupError, "Unexpected version"):
            self.version(str(other), mc_version="1.21/../x", loader="vanilla")


class JavaAndPorts(Fakes):
    def test_java_for(self) -> None:
        cases = {
            "1.7.10": 8,
            "1.12.2": 8,
            "1.16.5": 8,
            "1.17.1": 17,
            "1.20.4": 17,
            "1.20.5": 21,
            "1.21.1": 21,
            "26.2": 25,
            "26.3": 25,
            "1.21.2-pre1": 21,
        }
        for mc, java in cases.items():
            with self.subTest(mc=mc):
                self.assertEqual(ns.java_for(mc), java)
        with self.assertRaisesRegex(ns.SetupError, "not a release"):
            ns.java_for("24w14a")

    def test_image_reused_from_another_server(self) -> None:
        self.assertEqual(ns.java_image(21), "itzg/minecraft-server:java21@sha256:aaa")
        self.assertEqual(self.docker_calls, [])

    def test_image_pulled_when_none_pins_it(self) -> None:
        self.assertEqual(ns.java_image(25), "itzg/minecraft-server:java25@sha256:new")
        self.assertEqual(self.docker_calls[0], ["docker", "pull", "itzg/minecraft-server:java25"])

    def test_ports_skip_used_and_test_ports(self) -> None:
        self.write_server("second", game=25568, rcon=25577, image="java8@sha256:b")
        self.assertEqual(ns.free_ports(), (25569, 25578))
        # Legacy servers are never counted.
        (self.repo / "legacy" / "Old").mkdir()
        (self.repo / "legacy" / "Old" / "server.conf").write_text("GAME_PORT=25569\n")
        self.assertEqual(ns.free_ports(), (25569, 25578))
        # Ports something else on this PC already uses are skipped.
        self.busy_ports = {25569, 25578}
        self.assertEqual(ns.free_ports(), (25570, 25579))
        self.busy_ports = set()
        # The tests' ports are skipped.
        ns.FIRST_GAME_PORT = 25590
        try:
            self.assertEqual(ns.free_ports()[0], 25592)
        finally:
            ns.FIRST_GAME_PORT = 25567


class MakeServer(Fakes):
    def make(self, name: str, instance: str, **extra: Any) -> Path:
        return ns.make_server(self.args(name, instance, **extra))

    def fabric_instance(self) -> Path:
        path = self.modrinth_instance("Emily 1.0.0", "Emily", "1.21.1", "fabric", "0.16.9")
        fabric_jar(path / "mods" / "lithium.jar", "lithium")
        fabric_jar(path / "mods" / "zoom.jar", "zoom", client=True)
        # Says nothing itself, but Modrinth knows it is client-only.
        fabric_jar(path / "mods" / "shaders.jar", "shaders")
        self.client_only_shas.add(sha1((path / "mods" / "shaders.jar").read_bytes()))
        (path / "config").mkdir()
        (path / "config" / "lithium.properties").write_text("a=1\n")
        (path / "kubejs" / "server_scripts").mkdir(parents=True)
        (path / "kubejs" / "server_scripts" / "x.js").write_text("//")
        self.fabric_downloads("1.21.1", "0.16.9")
        return path

    def test_fabric_server(self) -> None:
        instance = self.fabric_instance()
        folder = self.make("new-one", "Emily", ram="6G")
        data = folder / "data"
        jar = "fabric-server-mc.1.21.1-loader.0.16.9-launcher.1.1.2.jar"
        self.assertEqual(
            self.conf("new-one"),
            {
                "NAME": "new-one",
                "JAVA_IMAGE": "itzg/minecraft-server:java21@sha256:aaa",
                "MC_VERSION": "1.21.1",
                "JAR_FILE": jar,
                "RAM_ALLOCATION": "6G",
                "GAME_PORT": "25568",
                "RCON_PORT": "25577",
                "ADMIN_NAME": "Louis",
                # Written as "$HOME/...", which Bash fills in with the real home.
                "PROFILE_DIR": str(Path.home() / instance.relative_to(self.home)),
            },
        )
        self.assertIn(
            'PROFILE_DIR="$HOME/modrinth/profiles/Emily 1.0.0"',
            (folder / "server.conf").read_text(),
        )
        self.assertEqual((data / jar).read_bytes(), b"fabric-jar")
        self.assertEqual(sorted(p.name for p in (data / "mods").iterdir()), ["lithium.jar"])
        self.assertEqual((data / "mods.txt").read_text(), "lithium.jar\n")
        self.assertTrue((data / "config" / "lithium.properties").is_file())
        self.assertTrue((data / "kubejs" / "server_scripts" / "x.js").is_file())
        self.assertTrue((folder / "backups").is_dir())
        for made_by_the_server in ("eula.txt", "server.properties"):
            self.assertFalse((data / made_by_the_server).exists())
        # The new server is a normal server to sync-mods (and so to docker.sh sync).
        self.assertIn("new-one", ns.sync.find_servers())

    def test_no_mods(self) -> None:
        self.fabric_instance()
        folder = self.make("bare", "Emily", no_mods=True)
        self.assertFalse((folder / "data" / "mods").exists())
        self.assertFalse((folder / "data" / "kubejs").exists())

    def test_vanilla_checksum(self) -> None:
        self.modrinth_instance("P", "P", "1.21.1", "vanilla", None)
        self.vanilla_downloads("1.21.1", b"server")
        folder = self.make("plain", "P")
        self.assertEqual(self.conf("plain")["JAR_FILE"], "minecraft_server.1.21.1.jar")
        self.assertEqual((folder / "data" / "minecraft_server.1.21.1.jar").read_bytes(), b"server")

    def test_bad_checksum_removes_the_folder(self) -> None:
        self.modrinth_instance("P", "P", "1.21.1", "vanilla", None)
        self.vanilla_downloads("1.21.1", b"tampered", published=b"server")
        with self.assertRaisesRegex(ns.SetupError, "Checksum mismatch"):
            self.make("plain", "P")
        self.assertFalse((self.servers / "plain").exists())

    def test_neoforge_gets_the_starter_jar(self) -> None:
        self.curseforge_instance("HBM Modernised", "HBM Continuum", "1.21.1", "neoforge-21.1.264")
        url = (
            "https://maven.neoforged.net/releases/net/neoforged/neoforge/21.1.264/"
            "neoforge-21.1.264-installer.jar"
        )
        self.urls[url] = b"installer"
        self.urls[ns.SERVER_STARTER_URL] = b"starter"
        folder = self.make("hbm-new", "HBM Continuum")
        data = folder / "data"
        self.assertEqual(self.conf("hbm-new")["JAR_FILE"], "server.jar")
        self.assertEqual((data / "server.jar").read_bytes(), b"starter")
        self.assertIn(url + ".sha1", self.fetched)
        run = next(c for c in self.docker_calls if c[:2] == ["docker", "run"])
        self.assertIn("itzg/minecraft-server:java21@sha256:aaa", run)
        self.assertIn("--installServer", run)
        # The installer and its log are gone; what it installed stays.
        self.assertEqual(sorted(p.name for p in data.iterdir()), ["run.sh", "server.jar"])

    def test_old_forge_tries_the_second_url_and_finds_the_jar(self) -> None:
        self.curseforge_instance("Old", "Old", "1.7.10", "forge-10.13.4.1614")
        url = (
            "https://maven.minecraftforge.net/net/minecraftforge/forge/1.7.10-10.13.4.1614-1.7.10/"
            "forge-1.7.10-10.13.4.1614-1.7.10-installer.jar"
        )
        self.urls[url] = b"installer"
        self.installer_makes = "forge-1.7.10-10.13.4.1614-1.7.10-universal.jar"
        self.make("old-one", "Old")
        self.assertEqual(self.conf("old-one")["JAR_FILE"], self.installer_makes)
        self.assertEqual(
            self.conf("old-one")["JAVA_IMAGE"], "itzg/minecraft-server:java8@sha256:new"
        )

    def test_installer_server_down_is_not_not_found(self) -> None:
        self.curseforge_instance("N", "N", "1.21.1", "neoforge-21.1.264")

        def down(url: str) -> bytes:
            raise ns.SetupError(f"Download failed (503): {url}")

        ns.fetch = down
        with self.assertRaisesRegex(ns.SetupError, "503"):
            self.make("n", "N")

    def test_never_removes_a_folder_it_did_not_make(self) -> None:
        self.fabric_instance()
        real_check = ns.check_name

        def racing_check(name: str) -> Path:
            folder = real_check(name)
            (folder / "someone-elses").mkdir(parents=True)  # appears in between
            return folder

        ns.check_name = racing_check
        try:
            with self.assertRaisesRegex(ns.SetupError, "already exists"):
                self.make("race", "Emily")
        finally:
            ns.check_name = real_check
        self.assertTrue((self.servers / "race" / "someone-elses").is_dir())

    def test_installer_failure_removes_the_folder(self) -> None:
        self.curseforge_instance("N", "N", "1.21.1", "neoforge-21.1.264")
        self.urls[
            "https://maven.neoforged.net/releases/net/neoforged/neoforge/21.1.264/"
            "neoforge-21.1.264-installer.jar"
        ] = b"installer"
        self.installer_fails = True
        with self.assertRaisesRegex(ns.SetupError, "(?s)installer failed.*exploded"):
            self.make("n", "N")
        self.assertFalse((self.servers / "n").exists())

    def test_names(self) -> None:
        self.fabric_instance()
        for bad in ("Caps", "1st", "has space", "../up", "-dash", "under_score"):
            with self.subTest(name=bad), self.assertRaisesRegex(ns.SetupError, "Bad name"):
                self.make(bad, "Emily")
        with self.assertRaisesRegex(ns.SetupError, "already exists"):
            self.make("existing", "Emily")
        with self.assertRaisesRegex(ns.SetupError, "Bad --ram"):
            self.make("fine", "Emily", ram="lots")
        self.assertEqual(sorted(p.name for p in self.servers.iterdir()), ["existing"])

    def test_odd_profile_paths_survive_bash(self) -> None:
        for path in (self.home / 'a "quote" $x', self.root / "outside it's"):
            with self.subTest(path=path):
                conf = self.root / "t.conf"
                conf.write_text(f"PROFILE_DIR={ns.bash_path(path)}\n")
                got = ns.conf_values(conf, ["PROFILE_DIR"])["PROFILE_DIR"]
                self.assertEqual(got, str(path))


if __name__ == "__main__":
    unittest.main()
