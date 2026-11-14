"""scripts/sync-mods.py: mod metadata, version order, server discovery and the sync plan.

Run with: python3 -I -m unittest discover -s tests -p 'test_*.py'   (or tests/run.sh)
Everything happens in throwaway folders; the real servers and profiles are not read.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("sync_mods", REPO_ROOT / "scripts" / "sync-mods.py")
if _spec is None or _spec.loader is None:
    raise ImportError(f"cannot load {REPO_ROOT / 'scripts' / 'sync-mods.py'}")
# Loaded from a file path (its name has a dash), so type checkers can't see its contents.
sync: Any = importlib.util.module_from_spec(_spec)
sys.modules["sync_mods"] = sync  # dataclasses look the module up while it loads
_spec.loader.exec_module(sync)


def _no_network(sha1s: list[str]) -> dict[str, str]:
    raise OSError("no network in tests")


# No test asks the real Modrinth; the ones about it put in their own answer.
real_server_sides = sync.modrinth_server_sides
sync.modrinth_server_sides = _no_network


def make_jar(path: Path, files: dict[str, str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as jar:
        for name, text in files.items():
            jar.writestr(name, text)
    return path


def fabric_jar(path: Path, mod_id: str, version: str, client: bool = False) -> Path:
    meta = {"id": mod_id, "version": version}
    if client:
        meta["environment"] = "client"
    return make_jar(path, {"fabric.mod.json": json.dumps(meta)})


class TempDirTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()


class CompareVersions(unittest.TestCase):
    def test_order(self) -> None:
        cases = [
            ("1.2", "1.2.1", -1),
            ("1.2.1", "1.2", 1),
            ("1.10", "1.9", 1),
            ("2.0", "2.0", 0),
            ("1.0-beta", "1.0", -1),
            ("1.0-rc1", "1.0-beta2", 1),
            ("1.0-alpha", "1.0-beta", -1),
            ("3.2a", "3.2", 1),  # a single letter is a patch, not an alpha
            ("0.7.7+fix2", "0.7.7+fix3", -1),
            ("1.1+mc26.2", "1.0+mc26.3", 1),  # the part before + decides first
        ]
        for a, b, expected in cases:
            with self.subTest(a=a, b=b):
                self.assertEqual(sync.compare_versions(a, b), expected)

    def test_unknown(self) -> None:
        self.assertIsNone(sync.compare_versions(None, "1.0"))
        self.assertIsNone(sync.compare_versions("1.0", ""))
        self.assertIsNone(sync.compare_versions("abc", "1.0"))


class ReadMod(TempDirTest):
    def test_fabric(self) -> None:
        info = sync.read_mod(fabric_jar(self.root / "a.jar", "Sodium", "0.6"), "a.jar")
        self.assertEqual(
            (info.mod_id, info.version, info.loader, info.client_only),
            ("sodium", "0.6", "fabric", False),
        )

    def test_fabric_client_only(self) -> None:
        info = sync.read_mod(fabric_jar(self.root / "a.jar", "zoom", "1", client=True), "a.jar")
        self.assertTrue(info.client_only)

    def test_placeholder_version_falls_back_to_manifest(self) -> None:
        jar = make_jar(
            self.root / "a.jar",
            {
                "fabric.mod.json": json.dumps({"id": "x", "version": "${version}"}),
                "META-INF/MANIFEST.MF": "Manifest-Version: 1.0\nImplementation-Version: 4.5.6\n",
            },
        )
        self.assertEqual(sync.read_mod(jar, "a.jar").version, "4.5.6")

    def test_forge_toml(self) -> None:
        jar = make_jar(
            self.root / "f.jar", {"META-INF/mods.toml": '[[mods]]\nmodId="Create"\nversion="6.0"\n'}
        )
        info = sync.read_mod(jar, "f.jar")
        self.assertEqual((info.mod_id, info.version, info.loader), ("create", "6.0", "forge"))

    def test_neoforge_toml(self) -> None:
        jar = make_jar(
            self.root / "n.jar",
            {"META-INF/neoforge.mods.toml": '[[mods]]\nmodId="jei"\nversion="19"\n'},
        )
        self.assertEqual(sync.read_mod(jar, "n.jar").loader, "neoforge")

    def test_toml_client_side_dependency(self) -> None:
        toml = (
            '[[mods]]\nmodId="sodium"\nversion="0.8"\n'
            '[[dependencies.sodium]]\nmodId="minecraft"\nside="CLIENT"\n'
        )
        both = toml.replace('side="CLIENT"', 'side="BOTH"')
        client = make_jar(self.root / "c.jar", {"META-INF/neoforge.mods.toml": toml})
        either = make_jar(self.root / "b.jar", {"META-INF/neoforge.mods.toml": both})
        self.assertTrue(sync.read_mod(client, "c.jar").client_only)
        self.assertFalse(sync.read_mod(either, "b.jar").client_only)

    def test_mcmod_info_list_and_dict_forms(self) -> None:
        listed = make_jar(
            self.root / "l.zip", {"mcmod.info": '[{"modid": "NotEnoughItems", "version": "1.0.5"}]'}
        )
        wrapped = make_jar(
            self.root / "w.jar",
            {"mcmod.info": '// a comment\n{"modList": [{"modid": "hbm", "version": "X5"}]}'},
        )
        self.assertEqual(sync.read_mod(listed, "l.zip").mod_id, "notenoughitems")
        info = sync.read_mod(wrapped, "w.jar")
        self.assertEqual((info.mod_id, info.loader), ("hbm", "forge-legacy"))

    def test_unreadable_files_are_matched_by_path(self) -> None:
        (self.root / "broken.jar").write_text("not a zip")
        (self.root / "readme.txt").write_text("hi")
        empty = make_jar(self.root / "empty.jar", {"a.class": ""})
        for path, rel in (
            (self.root / "broken.jar", "Broken.jar"),
            (self.root / "readme.txt", "readme.txt"),
            (empty, "sub/empty.jar"),
        ):
            with self.subTest(rel=rel):
                info = sync.read_mod(path, rel)
                self.assertEqual((info.mod_id, info.loader), (rel.lower(), "unknown"))


class FindServers(TempDirTest):
    def conf(self, folder: str, name: str) -> None:
        path = self.root / folder / "server.conf"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"NAME={name}\nJAR_FILE=s.jar\nPROFILE_DIR=/p\n"
            "SYNC_EXCLUDE=(one 'two words')\nSYNC_CONFIG_SKIP=()\n"
        )

    def setUp(self) -> None:
        super().setUp()
        self._old_root = sync.REPO_ROOT
        sync.REPO_ROOT = self.root

    def tearDown(self) -> None:
        sync.REPO_ROOT = self._old_root
        super().tearDown()

    def test_legacy_and_docker_servers(self) -> None:
        self.conf("legacy/Old-Server", "Old")
        self.conf("docker/servers/new-server", "new-server")
        self.conf("docker/old-layout", "ignored-too")
        self.conf("not-a-server-place/x", "ignored")
        servers = sync.find_servers()
        self.assertEqual(sorted(servers), ["new-server", "old"])
        self.assertEqual(servers["old"].root, self.root / "legacy/Old-Server")
        self.assertEqual(servers["new-server"].root, self.root / "docker/servers/new-server/data")
        self.assertEqual(
            servers["new-server"].backup_dir, self.root / "docker/servers/new-server/backups"
        )
        self.assertEqual(servers["old"].backup_dir, self.root / "legacy/Old-Server/backups")
        self.assertEqual(servers["old"].exclude, ["one", "two words"])
        self.assertEqual(servers["old"].config_skip, [])

    def test_duplicate_names_stop(self) -> None:
        self.conf("legacy/A", "same")
        self.conf("docker/servers/same", "Same")
        with self.assertRaises(SystemExit):
            sync.find_servers()


class Plan(TempDirTest):
    """One profile and one server covering every kind of mod and config change."""

    def setUp(self) -> None:
        super().setUp()
        p, s = self.root / "profile", self.root / "server"
        self.p, self.s = p, s
        pm, sm = p / "mods", s / "mods"
        fabric_jar(pm / "updated-2.jar", "updated", "2.0")
        fabric_jar(sm / "updated-1.jar", "updated", "1.0")
        fabric_jar(pm / "older-1.jar", "older", "1.0")
        fabric_jar(sm / "older-2.jar", "older", "2.0")
        fabric_jar(pm / "new.jar", "newmod", "1.0")
        fabric_jar(pm / "zoom.jar", "zoom", "1.0", client=True)
        fabric_jar(pm / "shaders.jar", "fancyshaders", "1.0")
        fabric_jar(sm / "shaders.jar", "fancyshaders", "0.9")
        same = fabric_jar(pm / "same.jar", "same", "1.0")
        (sm / "same.jar").write_bytes(same.read_bytes())
        fabric_jar(sm / "serveronly.jar", "serveronly", "1.0")
        (pm / "off.jar.disabled").write_text("x")

        pc, sc = p / "config", s / "config"
        for folder in (pc, sc, pc / "skipme", sc / "skipme"):
            folder.mkdir(parents=True, exist_ok=True)
        (pc / "updated.toml").write_text("a = 2\n")
        (sc / "updated.toml").write_text("a = 1\n")
        (pc / "same.cfg").write_text("# saved at 10:00\nx = 1\n")
        (sc / "same.cfg").write_text("# saved at 09:00\nx = 1\n")
        (pc / "newmod.json").write_text("{}")
        (pc / "unrelated-client-mod.json").write_text("{}")
        (pc / "skipme" / "secret.toml").write_text("id = 2\n")
        (sc / "skipme" / "secret.toml").write_text("id = 1\n")
        (pc / "debug.log").write_text("log")

        self.server = sync.Server(
            name="t",
            root=s,
            backup_dir=self.root / "bk",
            jar_file="s.jar",
            profile_dir=p,
            exclude=["shader"],
            config_skip=["skipme"],
        )
        self.plan = sync.build_plan(self.server)

    def changes(self, kind: str) -> list[str]:
        return sorted(c.new.mod_id for c in self.plan.mod_changes if c.kind == kind)

    def test_mods(self) -> None:
        plan = self.plan
        self.assertEqual(self.changes("add"), ["newmod"])
        self.assertEqual(self.changes("update"), ["updated"])
        self.assertEqual(self.changes("downgrade"), ["older"])
        self.assertEqual([m.mod_id for m in plan.client_only], ["zoom"])
        self.assertEqual([m.mod_id for m in plan.excluded], ["fancyshaders"])
        self.assertEqual([m.rel for m in plan.skipped_but_installed], ["shaders.jar"])
        self.assertEqual([m.mod_id for m in plan.unchanged], ["same"])
        self.assertEqual([m.mod_id for m in plan.server_only], ["serveronly"])
        self.assertEqual(plan.disabled, ["off.jar.disabled"])

    def test_configs(self) -> None:
        got = sorted((c.kind, c.rel.as_posix()) for c in self.plan.config_changes)
        # same.cfg differs only in a comment; the unrelated client mod's config and the
        # log file are not copied; skipme/ is reported but left alone.
        self.assertEqual(got, [("add", "newmod.json"), ("update", "updated.toml")])
        self.assertEqual([p.as_posix() for p in self.plan.config_skipped], ["skipme/secret.toml"])

    def test_apply(self) -> None:
        sync.apply_plan(self.server, self.plan)
        mods = sorted(p.name for p in (self.s / "mods").iterdir())
        # Updated and downgraded files replace the old ones; server-only and skipped
        # mods stay; nothing client-only or excluded is copied in.
        self.assertEqual(
            mods,
            [
                "new.jar",
                "older-1.jar",
                "same.jar",
                "serveronly.jar",
                "shaders.jar",
                "updated-2.jar",
            ],
        )
        self.assertEqual((self.s / "config" / "updated.toml").read_text(), "a = 2\n")
        self.assertEqual((self.s / "config" / "skipme" / "secret.toml").read_text(), "id = 1\n")
        self.assertEqual((self.s / "mods.txt").read_text().split(), mods)
        self.assertEqual(sync.build_plan(self.server).mod_changes, [])

    def test_backup_current(self) -> None:
        backup = sync.backup_current(self.server)
        self.assertEqual(backup.parent, self.root / "bk")
        self.assertTrue((backup / "mods" / "updated-1.jar").is_file())
        self.assertTrue((backup / "config" / "updated.toml").is_file())
        self.assertNotEqual(sync.backup_current(self.server), backup)


class Modrinth(TempDirTest):
    def setUp(self) -> None:
        super().setUp()
        self.mods = self.root / "mods"
        self.client = make_jar(self.mods / "fancy.jar", {"mcmod.info": '[{"modid": "fancy"}]'})
        self.server = make_jar(self.mods / "tech.jar", {"mcmod.info": '[{"modid": "tech"}]'})
        make_jar(self.mods / "unknown.jar", {"mcmod.info": '[{"modid": "unknown"}]'})
        self.asked: list[list[str]] = []

    def tearDown(self) -> None:
        sync.modrinth_server_sides = _no_network
        super().tearDown()

    def answer(self, sides: dict[Path, str]) -> None:
        by_sha = {hashlib.sha1(path.read_bytes()).hexdigest(): side for path, side in sides.items()}

        def fake(sha1s: list[str]) -> dict[str, str]:
            self.asked.append(sha1s)
            return {sha: side for sha, side in by_sha.items() if sha in sha1s}

        sync.modrinth_server_sides = fake

    def test_unsupported_on_servers_is_client_only(self) -> None:
        self.answer({self.client: "unsupported", self.server: "required"})
        mods = sync.read_mods(self.mods)
        note = sync.mark_modrinth_client_only(mods)
        self.assertEqual(len(self.asked[0]), 3)
        self.assertEqual(
            {k: m.client_only for k, m in mods.items()},
            {"fancy": True, "tech": False, "unknown": False},
        )
        self.assertIn("Modrinth knows 2 of 3 mod files; 1 more are client-only", note)

    def test_unreachable_carries_on(self) -> None:
        mods = sync.read_mods(self.mods)
        note = sync.mark_modrinth_client_only(mods)
        self.assertIn("could not be asked (no network in tests)", note)
        self.assertFalse(any(m.client_only for m in mods.values()))

    def test_api_replies_are_joined_by_project(self) -> None:
        replies = {
            "version_files": {"aa": {"project_id": "P1"}, "bb": {"project_id": "P2"}},
            "projects": [{"id": "P1", "server_side": "unsupported"}, {"id": "P2"}],
        }
        real_api = sync._api
        sync._api = lambda path, body=None: replies[path.split("?")[0]]
        try:
            self.assertEqual(
                real_server_sides(["aa", "bb", "cc"]),
                {"aa": "unsupported", "bb": "unknown"},
            )
        finally:
            sync._api = real_api


if __name__ == "__main__":
    unittest.main()
