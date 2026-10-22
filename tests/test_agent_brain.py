#!/usr/bin/env python3
"""agent-brain install, update and connection: releases are checked against
their manifest, archives cannot write outside the binary, updates only move
forward, and agent-brain gets no credentials from the caller."""
from __future__ import annotations

import argparse
import hashlib
import http.server
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import threading
import unittest
from unittest import mock
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable
AGENTFLEET = ROOT / "bin" / "agentfleet"
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "scripts"))
import agent_brain  # noqa: E402

POSIX = os.name == "posix"


def fake_binary(version: str, log: Path | None = None, memory_state: str = "active") -> bytes:
    """An agent-brain stand-in: prints its version and records other calls."""
    lines = ["#!" + PYTHON, "import json, os, sys",
             "if sys.argv[1:] == ['--version']:",
             f"    print('agent-brain version {version}')",
             "    sys.exit(0)",
             "if sys.argv[1:] == ['status']:",
             f"    print('Memory:           {memory_state}; 280 entries for test')",
             "    sys.exit(0)"]
    if log is not None:
        lines += [f"with open({str(log)!r}, 'a', encoding='utf-8') as out:",
                  "    out.write(json.dumps({'argv': sys.argv[1:], 'env': sorted(os.environ)}) + '\\n')"]
    return ("\n".join(lines) + "\n").encode("utf-8")


def memory_binary(version: str, log: Path, backend: str = "sqlite", v2: bool = False, upgrade_fails: bool = False) -> bytes:
    """An agent-brain stand-in for the Memory V2 upgrade: `memory status`
    reports the store, and `upgrade-v2 --apply` records itself and turns V2 on."""
    state = log.with_suffix(".v2")
    lines = ["#!" + PYTHON, "import json, os, sys",
             f"log, state = {str(log)!r}, {str(state)!r}",
             "if sys.argv[1:] == ['--version']:",
             f"    print('agent-brain version {version}')",
             "    sys.exit(0)",
             "with open(log, 'a', encoding='utf-8') as out:",
             "    out.write(json.dumps(sys.argv[1:]) + '\\n')",
             "if sys.argv[1:] == ['memory', 'status']:",
             f"    print(json.dumps({{'schema': 16, 'backend': {backend!r}, 'v2_active': {v2!r} or os.path.exists(state)}}, indent=2))",
             "    sys.exit(0)",
             "if sys.argv[1:] == ['memory', 'upgrade-v2', '--apply']:"]
    if upgrade_fails:
        lines += ["    print('Error: database is locked', file=sys.stderr)", "    sys.exit(1)"]
    else:
        lines += ["    open(state, 'w').close()",
                  "    print('Memory V2 is on.')",
                  "    print('A copy of the store from before the upgrade is at /usr/local/backup.db.')",
                  "    sys.exit(0)"]
    return ("\n".join(lines) + "\n").encode("utf-8")


def tar_archive(members: list[tuple[str, bytes | None]]) -> bytes:
    """A .tar.gz with regular files, or symlinks where the content is None."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, content in members:
            info = tarfile.TarInfo(name)
            if content is None:
                info.type = tarfile.SYMTYPE
                info.linkname = "/etc/passwd"
                archive.addfile(info)
            else:
                info.size = len(content)
                info.mode = 0o755
                archive.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


def zip_archive(members: list[tuple[str, bytes]]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in members:
            archive.writestr(name, content)
    return buffer.getvalue()


class ReleaseServer:
    """Serves releases/latest.json and one archive on 127.0.0.1."""

    def __init__(self):
        self.files: dict[str, bytes] = {}
        self.redirects: dict[str, str] = {}
        handler_files, handler_redirects = self.files, self.redirects

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 - http.server API
                if self.path in handler_redirects:
                    self.send_response(302)
                    self.send_header("Location", handler_redirects[self.path])
                    self.end_headers()
                    return
                body = handler_files.get(self.path)
                if body is None:
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def publish(self, version: str, archive: bytes, *, digest: str | None = None, size: int | None = None) -> None:
        system, arch = agent_brain.platform_key()
        filename = f"agent-brain_{system}_{arch}.{'zip' if system == 'windows' else 'tar.gz'}"
        self.files.clear()
        self.files["/releases/latest.json"] = json.dumps({"version": version, "assets": [{
            "os": system, "arch": arch, "filename": filename,
            "sha256": digest or hashlib.sha256(archive).hexdigest(), "size": size or len(archive),
        }]}).encode("utf-8")
        self.files[f"/releases/{version}/{filename}"] = archive

    def publish_binary(self, version: str, binary: bytes) -> None:
        name = agent_brain.executable_name()
        if os.name == "nt":
            self.publish(version, zip_archive([(name, binary), ("README.md", b"docs")]))
        else:
            self.publish(version, tar_archive([(name, binary), ("README.md", b"docs")]))

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class AgentBrainTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="agentfleet-agent-brain-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / "home"
        (self.home / ".claude").mkdir(parents=True)
        self.server = ReleaseServer()
        self.addCleanup(self.server.close)
        env = {"PATH": os.defpath, "HOME": str(self.home), "USERPROFILE": str(self.home),
               agent_brain.BASE_URL_ENV: self.server.url}
        patcher = mock.patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.target = agent_brain.managed_binary(self.home)

    # -- install -----------------------------------------------------------------
    def test_install_puts_only_the_executable_in_the_standard_folder(self):
        binary = fake_binary("0.9.0")
        self.server.publish_binary("0.9.0", binary)
        version, path = agent_brain.install(self.home)
        self.assertEqual(version, "0.9.0")
        self.assertEqual(path, self.target)
        self.assertEqual(path.read_bytes(), binary)
        self.assertEqual(sorted(p.name for p in path.parent.iterdir()), [agent_brain.executable_name()])
        if POSIX:
            self.assertEqual(path.stat().st_mode & 0o777, 0o755)
            self.assertEqual(agent_brain.version_of(path, self.home), (0, 9, 0))

    def test_checksum_or_size_mismatch_installs_nothing(self):
        archive = tar_archive([(agent_brain.executable_name(), fake_binary("0.9.0"))])
        for kwargs in ({"digest": "0" * 64}, {"size": len(archive) + 1}):
            with self.subTest(**kwargs):
                self.server.publish("0.9.0", archive, **kwargs)
                with self.assertRaises(agent_brain.AgentBrainError):
                    agent_brain.install(self.home)
                self.assertFalse(self.target.exists())
                self.assertFalse(self.target.parent.exists() and any(self.target.parent.iterdir()))

    @unittest.skipUnless(POSIX, "tar archives are the POSIX release format")
    def test_archive_paths_never_become_file_paths(self):
        name = agent_brain.executable_name()
        outside = self.root / "escaped"
        for members in (
            [(f"../{name}", b"bad"), ("sub/" + name, b"bad")],
            [(name, None)],
            [("../../escaped", b"bad"), (str(outside), b"bad")],
        ):
            with self.subTest(members=[m[0] for m in members]):
                self.server.publish("0.9.0", tar_archive(members))
                with self.assertRaises(agent_brain.AgentBrainError):
                    agent_brain.install(self.home)
                self.assertFalse(self.target.exists())
                self.assertFalse(outside.exists())
        # Extra members next to the binary are ignored, wherever they point.
        self.server.publish("0.9.0", tar_archive([("../../escaped", b"bad"), (name, fake_binary("0.9.0"))]))
        agent_brain.install(self.home)
        self.assertTrue(self.target.is_file())
        self.assertFalse(outside.exists())

    def test_zip_archive_takes_only_the_top_level_executable(self):
        name = agent_brain.executable_name()
        data = zip_archive([("../" + name, b"bad"), (name, b"good"), ("LICENSE", b"text")])
        self.assertEqual(agent_brain._extract(data, "agent-brain_windows_amd64.zip"), b"good")
        with self.assertRaises(agent_brain.AgentBrainError):
            agent_brain._extract(zip_archive([("dir/" + name, b"bad")]), "agent-brain_windows_amd64.zip")

    def test_manifest_must_be_well_formed(self):
        bad = [
            {"version": "latest", "assets": []},
            {"version": "0.9.0", "assets": []},
            {"version": "0.9.0", "assets": [{"os": agent_brain.platform_key()[0], "arch": agent_brain.platform_key()[1],
                                            "filename": "../agent-brain.tar.gz", "sha256": "0" * 64, "size": 10}]},
        ]
        for manifest in bad:
            with self.subTest(manifest=manifest):
                self.server.files["/releases/latest.json"] = json.dumps(manifest).encode("utf-8")
                with self.assertRaises(agent_brain.AgentBrainError):
                    agent_brain.latest_release()

    def test_only_https_or_loopback_is_trusted(self):
        for url in ("http://agentbrain.example", "ftp://127.0.0.1", "file:///tmp"):
            with self.subTest(url=url), mock.patch.dict(os.environ, {agent_brain.BASE_URL_ENV: url}):
                with self.assertRaises(agent_brain.AgentBrainError):
                    agent_brain.latest_release()
        with mock.patch.dict(os.environ, {agent_brain.BASE_URL_ENV: ""}):
            os.environ.pop(agent_brain.BASE_URL_ENV)
            self.assertEqual(agent_brain.base_url(), "https://agentbrain.wecansync.com")

    def test_every_redirect_hop_must_stay_on_https(self):
        self.server.publish_binary("0.9.0", fake_binary("0.9.0"))
        manifest = self.server.files.pop("/releases/latest.json")
        # A hop through plain http is refused before it is followed, even
        # when a later hop would come back to a trusted address.
        self.server.redirects["/releases/latest.json"] = "http://agentbrain.example/back?to=" + self.server.url
        with self.assertRaisesRegex(agent_brain.AgentBrainError, "must use https"):
            agent_brain.latest_release()
        # Redirects that stay trusted are followed.
        self.server.files["/moved/latest.json"] = manifest
        self.server.redirects["/releases/latest.json"] = self.server.url + "/moved/latest.json"
        self.assertEqual(agent_brain.latest_release()["version"], "0.9.0")

    # -- update ------------------------------------------------------------------
    @unittest.skipUnless(POSIX, "the fake agent-brain is a script")
    def test_update_moves_forward_only(self):
        self.target.parent.mkdir(parents=True)
        self.target.write_bytes(fake_binary("0.4.0"))
        self.target.chmod(0o755)
        self.server.publish_binary("0.3.0", fake_binary("0.3.0"))
        self.assertIsNone(agent_brain.update(self.home))
        self.server.publish_binary("0.4.0", fake_binary("0.4.0"))
        self.assertIsNone(agent_brain.update(self.home))
        self.server.publish_binary("0.5.0", fake_binary("0.5.0"))
        self.assertEqual(agent_brain.update(self.home), "0.5.0")
        self.assertEqual(agent_brain.version_of(self.target, self.home), (0, 5, 0))

    @unittest.skipUnless(POSIX, "the fake agent-brain is a script")
    def test_update_leaves_dev_builds_symlinks_and_missing_binaries(self):
        self.server.publish_binary("0.9.0", fake_binary("0.9.0"))
        self.assertIsNone(agent_brain.update(self.home))
        self.assertFalse(self.target.exists())
        self.target.parent.mkdir(parents=True)
        self.target.write_bytes(fake_binary("dev"))
        self.target.chmod(0o755)
        self.assertIsNone(agent_brain.update(self.home))
        self.target.unlink()
        real = self.root / "elsewhere"
        real.write_bytes(fake_binary("0.1.0"))
        real.chmod(0o755)
        self.target.symlink_to(real)
        self.assertIsNone(agent_brain.update(self.home))
        self.assertEqual(real.read_bytes(), fake_binary("0.1.0"))

    # -- connection --------------------------------------------------------------
    @unittest.skipUnless(POSIX, "the fake agent-brain is a script")
    def test_connect_and_disconnect_pass_no_credentials(self):
        log = self.root / "calls.jsonl"
        binary = self.root / "agent-brain"
        binary.write_bytes(fake_binary("0.4.0", log))
        binary.chmod(0o755)
        secrets = {"ANTHROPIC_AUTH_TOKEN": "sk-" + "1234567890" * 2, "GATEWAY_TOKEN": "x", "AGENT_BRAIN_SERVER_URL": "https://brain.example"}
        with mock.patch.dict(os.environ, secrets):
            self.assertEqual(agent_brain.connect(self.home, binary)[0], 0)
            self.assertEqual(agent_brain.disconnect(self.home, binary)[0], 0)
        calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([call["argv"] for call in calls], [["install", "--assistant", "claude-code"], ["uninstall", "--assistant", "claude-code"]])
        for call in calls:
            self.assertNotIn("ANTHROPIC_AUTH_TOKEN", call["env"])
            self.assertNotIn("GATEWAY_TOKEN", call["env"])
            # agent-brain's own settings still reach it.
            self.assertIn("AGENT_BRAIN_SERVER_URL", call["env"])

    def test_another_home_gets_none_of_this_users_agent_brain_settings(self):
        mine = {"AGENT_BRAIN_SERVER_URL": "https://brain.example", "XDG_CONFIG_HOME": str(self.home / ".config"),
                "XDG_DATA_HOME": str(self.home / ".local" / "share")}
        with mock.patch.dict(os.environ, mine):
            own = agent_brain.environment(self.home)
            other = agent_brain.environment(self.root / "scratch")
        self.assertEqual(own["AGENT_BRAIN_SERVER_URL"], "https://brain.example")
        self.assertEqual(own["XDG_CONFIG_HOME"], mine["XDG_CONFIG_HOME"])
        self.assertEqual(other["HOME"], str(self.root / "scratch"))
        for name in mine:
            self.assertNotIn(name, other)

    def test_connected_means_agent_brain_wrote_its_own_hooks(self):
        settings = self.home / ".claude" / "settings.json"
        self.assertFalse(agent_brain.connected(self.home))
        def write(command):
            settings.write_text(json.dumps({"hooks": {"Stop": [{"hooks": [{"type": "command", "command": command}]}]}}), encoding="utf-8")
        write("/usr/local/bin/agent-brain hook stop # claude-agents-config:agent-brain")
        self.assertFalse(agent_brain.connected(self.home))
        write("echo agent-brain")
        self.assertFalse(agent_brain.connected(self.home))
        write("/usr/local/bin/agent-brain hook stop")
        self.assertTrue(agent_brain.connected(self.home))

    # -- Memory V2 ------------------------------------------------------------------
    def install_memory_binary(self, version: str, **kwargs) -> Path:
        log = self.root / "calls.log"
        self.target.parent.mkdir(parents=True, exist_ok=True)
        self.target.write_bytes(memory_binary(version, log, **kwargs))
        self.target.chmod(0o755)
        return log

    def calls(self, log: Path) -> list[list[str]]:
        if not log.exists():
            return []
        return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]

    @unittest.skipUnless(POSIX, "fake binary is a script")
    def test_memory_v2_upgrades_a_local_store_and_reports_the_backup(self):
        log = self.install_memory_binary("0.5.11")
        state, message = agent_brain.ensure_memory_v2(self.home)
        self.assertEqual(state, "upgraded")
        self.assertIn("/usr/local/backup.db", message)
        self.assertIn(["memory", "upgrade-v2", "--apply"], self.calls(log))
        # A second run finds V2 on and changes nothing.
        self.assertEqual(agent_brain.ensure_memory_v2(self.home), ("current", ""))
        self.assertEqual(self.calls(log).count(["memory", "upgrade-v2", "--apply"]), 1)

    @unittest.skipUnless(POSIX, "fake binary is a script")
    def test_memory_v2_leaves_a_shared_postgres_store_to_the_user(self):
        log = self.install_memory_binary("0.5.11", backend="postgres")
        state, message = agent_brain.ensure_memory_v2(self.home)
        self.assertEqual(state, "manual")
        self.assertIn("agent-brain memory upgrade-v2 --apply", message)
        self.assertNotIn(["memory", "upgrade-v2", "--apply"], self.calls(log))

    @unittest.skipUnless(POSIX, "fake binary is a script")
    def test_memory_v2_updates_an_old_managed_copy_before_upgrading(self):
        old_log = self.install_memory_binary("0.5.10")
        new_log = self.root / "new.log"
        self.server.publish_binary("0.5.11", memory_binary("0.5.11", new_log))
        state, _ = agent_brain.ensure_memory_v2(self.home)
        self.assertEqual(state, "upgraded")
        self.assertEqual(agent_brain.version_of(self.target, self.home), (0, 5, 11))
        self.assertNotIn(["memory", "upgrade-v2", "--apply"], self.calls(old_log))
        self.assertIn(["memory", "upgrade-v2", "--apply"], self.calls(new_log))

    @unittest.skipUnless(POSIX, "fake binary is a script")
    def test_memory_v2_never_upgrades_through_a_release_without_backups(self):
        log = self.install_memory_binary("0.5.10")
        self.server.publish_binary("0.5.10", memory_binary("0.5.10", log))
        state, _ = agent_brain.ensure_memory_v2(self.home)
        self.assertEqual(state, "failed")
        self.assertNotIn(["memory", "upgrade-v2", "--apply"], self.calls(log))

    @unittest.skipUnless(POSIX, "fake binary is a script")
    def test_memory_v2_leaves_an_old_unmanaged_copy_to_the_user(self):
        folder = self.root / "bin"
        folder.mkdir()
        log = self.root / "calls.log"
        other = folder / "agent-brain"
        other.write_bytes(memory_binary("0.5.10", log))
        other.chmod(0o755)
        with mock.patch.dict(os.environ, {"PATH": f"{folder}{os.pathsep}{os.defpath}"}):
            state, message = agent_brain.ensure_memory_v2(self.home)
        self.assertEqual(state, "manual")
        self.assertIn(str(other), message)
        self.assertEqual(self.calls(log), [])
        self.assertFalse(self.target.exists())

    @unittest.skipUnless(POSIX, "fake binary is a script")
    def test_memory_v2_reports_a_failed_upgrade(self):
        self.install_memory_binary("0.5.11", upgrade_fails=True)
        state, message = agent_brain.ensure_memory_v2(self.home)
        self.assertEqual(state, "failed")
        self.assertIn("database is locked", message)

    def test_memory_v2_without_agent_brain_does_nothing(self):
        self.assertEqual(agent_brain.ensure_memory_v2(self.home), ("unavailable", ""))

    @unittest.skipUnless(POSIX, "fake binary is a script")
    def test_memory_v2_is_forced_only_on_an_agent_brain_agentfleet_connects(self):
        log = self.install_memory_binary("0.5.11")
        # Not connected: agent-brain wrote no hooks.
        self.assertEqual(agent_brain.ensure_connected_memory_v2(self.home), ("unavailable", ""))
        settings = self.home / ".claude" / "settings.json"
        settings.write_text(json.dumps({"hooks": {"Stop": [{"hooks": [
            {"type": "command", "command": f"{self.target} hook stop"}]}]}}), encoding="utf-8")
        agent_brain.provider_catalog.write_preferences(self.home, {"agentBrain": False})
        self.assertEqual(agent_brain.ensure_connected_memory_v2(self.home), ("unavailable", ""))
        self.assertEqual(self.calls(log), [])
        agent_brain.provider_catalog.write_preferences(self.home, {"agentBrain": True})
        self.assertEqual(agent_brain.ensure_connected_memory_v2(self.home)[0], "upgraded")

    def test_hooks_run_matches_the_executable_in_either_slash_form(self):
        settings = self.home / ".claude" / "settings.json"
        def write(command):
            settings.write_text(json.dumps({"hooks": {"Stop": [{"hooks": [{"type": "command", "command": command}]}]}}), encoding="utf-8")
        binary = Path("/opt/tools/agent-brain")
        self.assertFalse(agent_brain.hooks_run(self.home, binary))
        write("/usr/local/bin/agent-brain hook stop")
        self.assertFalse(agent_brain.hooks_run(self.home, binary))
        # Another executable whose path starts with this one's.
        write("/opt/tools/agent-brain-old hook stop")
        self.assertFalse(agent_brain.hooks_run(self.home, binary))
        write("/opt/tools/agent-brain hook stop")
        self.assertTrue(agent_brain.hooks_run(self.home, binary))
        write("'/opt/tools/agent-brain' hook stop")
        self.assertTrue(agent_brain.hooks_run(self.home, binary))
        # Hooks written by agent-brain before 0.5.5 on Windows use backslashes.
        write("\\opt\\tools\\agent-brain hook stop")
        self.assertTrue(agent_brain.hooks_run(self.home, binary))
        # AgentFleet's own marked copies never count.
        write("/opt/tools/agent-brain hook stop # claude-agents-config:agent-brain")
        self.assertFalse(agent_brain.hooks_run(self.home, binary))

    def test_memory_status_and_enable_commands(self):
        # No real agent-brain on PATH, so the managed fake is the one found.
        with tempfile.TemporaryDirectory(prefix="agentfleet-memory-helper-") as raw, \
                mock.patch.dict(os.environ, {"PATH": os.defpath}):
            home = Path(raw)
            (home / ".claude").mkdir()
            binary = agent_brain.managed_binary(home)
            binary.parent.mkdir(parents=True)
            log = home / "calls.jsonl"
            binary.write_bytes(fake_binary("0.5.7", log))
            binary.chmod(0o755)
            self.assertTrue(agent_brain.memory_active(home))
            self.assertTrue(agent_brain.enable_memory(home)[0])
            # The fake logs neither `status` nor `--version`.
            calls = [json.loads(line)["argv"] for line in log.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(calls, [["memory", "enable", "--yes"]])
            self.assertTrue(agent_brain.disable_memory(home)[0])
            calls = [json.loads(line)["argv"] for line in log.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(calls[-1], ["memory", "disable", "--all"])

    def test_find_binary_prefers_path_then_the_standard_folder(self):
        self.assertIsNone(agent_brain.find_binary(self.home))
        self.target.parent.mkdir(parents=True)
        self.target.write_bytes(b"x")
        self.target.chmod(0o755)
        self.assertEqual(agent_brain.find_binary(self.home), self.target)
        elsewhere = self.root / "bin"
        elsewhere.mkdir()
        other = elsewhere / agent_brain.executable_name()
        other.write_bytes(b"x")
        other.chmod(0o755)
        with mock.patch.dict(os.environ, {"PATH": str(elsewhere) + os.pathsep + os.defpath}):
            self.assertEqual(agent_brain.find_binary(self.home), other)


@unittest.skipUnless(POSIX, "the fake agent-brain is a Python script, which Windows cannot run as agent-brain.exe")
class AgentBrainCliTests(unittest.TestCase):
    """`agentfleet config agent-brain on` and the daily background update."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="agentfleet-agent-brain-cli-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / "home"
        (self.home / ".claude").mkdir(parents=True)
        self.server = ReleaseServer()
        self.addCleanup(self.server.close)
        self.log = self.root / "calls.jsonl"
        self.target = agent_brain.managed_binary(self.home)
        # A fake claude first on PATH, and no real agent-brain.
        fake_bin = self.root / "fake-bin"
        fake_bin.mkdir()
        fake_claude = fake_bin / "claude"
        if os.name == "nt":
            fake_claude.write_text("import json\nprint('[]')\n", encoding="utf-8")
            (fake_bin / "claude.cmd").write_text(f'@"{PYTHON}" "%~dp0claude" %*\n', encoding="utf-8")
        else:
            fake_claude.write_text("#!/bin/sh\necho '[]'\n", encoding="utf-8")
            fake_claude.chmod(0o755)
        self.env = {"PATH": str(fake_bin) + os.pathsep + os.defpath, "HOME": str(self.home),
                    "PYTHONDONTWRITEBYTECODE": "1", agent_brain.BASE_URL_ENV: self.server.url,
                    "ANTHROPIC_AUTH_TOKEN": "sk-" + "1234567890" * 2}
        if os.name == "nt":
            self.env.update({name: os.environ[name] for name in ("SYSTEMROOT", "COMSPEC", "PATHEXT", "TEMP", "TMP") if name in os.environ})
            self.env["USERPROFILE"] = str(self.home)
        else:
            self.env["LANG"] = "C.UTF-8"

    def cli(self, *args: str, **extra: str):
        return subprocess.run([PYTHON, "-B", str(AGENTFLEET), "--home", str(self.home), *args],
                              env={**self.env, **extra}, text=True, capture_output=True, timeout=60)

    def calls(self):
        return [json.loads(line)["argv"] for line in self.log.read_text(encoding="utf-8").splitlines()] if self.log.exists() else []

    def test_config_on_downloads_verifies_and_connects(self):
        self.server.publish_binary("0.9.0", fake_binary("0.9.0", self.log))
        result = self.cli("config", "agent-brain", "on")
        # No AgentFleet release is installed here, so the lane refresh step
        # fails after agent-brain itself is in place.
        self.assertIn("Installed agent-brain 0.9.0", result.stdout, result.stderr)
        self.assertIn("agent-brain is connected to Claude Code", result.stdout)
        self.assertEqual(self.target.read_bytes(), fake_binary("0.9.0", self.log))
        self.assertEqual(self.calls(), [["install", "--assistant", "claude-code"]])
        result = self.cli("config", "agent-brain", "off")
        self.assertIn("disconnected", result.stdout, result.stderr)
        self.assertEqual(self.calls()[-1], ["uninstall", "--assistant", "claude-code"])

    def test_config_on_refuses_a_tampered_download(self):
        archive = tar_archive([("agent-brain", fake_binary("0.9.0", self.log))])
        self.server.publish("0.9.0", archive, digest=hashlib.sha256(b"other").hexdigest())
        result = self.cli("config", "agent-brain", "on")
        self.assertEqual(result.returncode, 1)
        self.assertIn("does not match the release manifest", result.stderr)
        self.assertFalse(self.target.exists())
        self.assertEqual(self.calls(), [])
        # The choice is not saved, so the wizard and config still offer it.
        self.assertIsNone(agent_brain.provider_catalog.load_preferences(self.home)["agentBrain"])

    def memory_preference(self):
        return agent_brain.provider_catalog.load_preferences(self.home)["agentBrainLocalMemory"]

    def test_memory_is_off_until_someone_says_yes(self):
        self.assertIs(self.memory_preference(), False)

    def test_config_memory_on_and_off(self):
        self.target.parent.mkdir(parents=True)
        self.target.write_bytes(fake_binary("0.5.7", self.log))
        self.target.chmod(0o755)
        result = self.cli("config", "agent-brain-memory", "on")
        self.assertIn("agent-brain memory is enabled.", result.stdout, result.stderr)
        self.assertEqual(self.calls(), [["memory", "enable", "--yes"]])
        self.assertIs(self.memory_preference(), True)
        # Then the lanes are refreshed; no AgentFleet release is installed here.
        self.assertIn("No downloaded release found", result.stderr)
        result = self.cli("config", "agent-brain-memory", "off")
        self.assertIn("existing entries are kept", result.stdout, result.stderr)
        self.assertEqual(self.calls()[-1], ["memory", "disable", "--all"])
        self.assertIs(self.memory_preference(), False)

    def test_config_memory_on_updates_an_old_managed_copy_first(self):
        self.target.parent.mkdir(parents=True)
        self.target.write_bytes(fake_binary("0.5.6", self.log))
        self.target.chmod(0o755)
        self.server.publish_binary("0.5.7", fake_binary("0.5.7", self.log))
        result = self.cli("config", "agent-brain-memory", "on")
        self.assertIn("agent-brain memory is enabled.", result.stdout, result.stderr)
        self.assertEqual(self.target.read_bytes(), fake_binary("0.5.7", self.log))
        self.assertEqual(self.calls(), [["memory", "enable", "--yes"]])

    def test_config_memory_on_leaves_an_old_copy_it_did_not_install(self):
        other = self.root / "other-bin"
        other.mkdir()
        old = other / "agent-brain"
        old.write_bytes(fake_binary("0.5.6", self.log))
        old.chmod(0o755)
        result = self.cli("config", "agent-brain-memory", "on", PATH=str(other) + os.pathsep + self.env["PATH"])
        self.assertEqual(result.returncode, 1)
        self.assertIn("older than 0.5.7", result.stderr)
        self.assertEqual(old.read_bytes(), fake_binary("0.5.6", self.log))
        self.assertEqual(self.calls(), [])
        self.assertIs(self.memory_preference(), False)

    def test_config_memory_on_without_agent_brain(self):
        result = self.cli("config", "agent-brain-memory", "on")
        self.assertEqual(result.returncode, 1)
        self.assertIn("agent-brain is not installed", result.stderr)
        self.assertIs(self.memory_preference(), False)

    def hook(self, **extra: str):
        return self.cli("auto-update", "--hook", AGENTFLEET_AUTO_UPDATE_SYNC="1", **extra)

    def state(self) -> dict:
        path = self.home / ".claude" / "agentfleet" / "auto-update.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    def test_session_start_updates_the_managed_copy_once_a_day_and_reports_it(self):
        self.target.parent.mkdir(parents=True)
        self.target.write_bytes(fake_binary("0.4.0"))
        self.target.chmod(0o755)
        self.server.publish_binary("0.5.0", fake_binary("0.5.0"))
        self.assertEqual(self.hook().returncode, 0)
        self.assertEqual(self.target.read_bytes(), fake_binary("0.5.0"))
        self.assertIn("agentBrainCheck", self.state())
        reported = self.hook()
        self.assertIn("agent-brain was updated to 0.5.0 automatically.", reported.stdout)
        self.assertNotIn("agent-brain was updated", self.hook().stdout)
        # Checked today already: a newer release waits for tomorrow.
        self.server.publish_binary("0.6.0", fake_binary("0.6.0"))
        self.hook()
        self.assertEqual(self.target.read_bytes(), fake_binary("0.5.0"))

    def write_hooks(self, binary: Path):
        command = f"{binary} hook stop"
        (self.home / ".claude" / "settings.json").write_text(
            json.dumps({"hooks": {"Stop": [{"hooks": [{"type": "command", "command": command}]}]}}), encoding="utf-8")

    def test_update_lets_the_new_agent_brain_rewrite_hooks_that_run_it(self):
        self.target.parent.mkdir(parents=True)
        self.target.write_bytes(fake_binary("0.4.0", self.log))
        self.target.chmod(0o755)
        self.write_hooks(self.target)
        self.server.publish_binary("0.5.0", fake_binary("0.5.0", self.log))
        self.assertEqual(self.hook().returncode, 0)
        self.assertEqual(self.target.read_bytes(), fake_binary("0.5.0", self.log))
        self.assertEqual(self.calls(), [["install", "--assistant", "claude-code"]])

    def test_update_does_not_reconnect_after_agent_brain_was_turned_off(self):
        self.target.parent.mkdir(parents=True)
        self.target.write_bytes(fake_binary("0.4.0", self.log))
        self.target.chmod(0o755)
        self.write_hooks(self.target)
        self.server.publish_binary("0.5.0", fake_binary("0.5.0", self.log))
        # `config agent-brain off` ran while this update was downloading.
        real_update = agent_brain.update
        def update_then_turn_off(home):
            version = real_update(home)
            agent_brain.provider_catalog.write_preferences(home, {"agentBrain": False})
            return version
        # bin/agentfleet has no .py suffix, so name its loader explicitly.
        loader = importlib.machinery.SourceFileLoader("agent_brain_cli_agentfleet", str(AGENTFLEET))
        spec = importlib.util.spec_from_file_location(loader.name, AGENTFLEET, loader=loader)
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        paths = cli.Paths(argparse.Namespace(home=str(self.home), config_home=None, prefix=None))
        with mock.patch.dict(os.environ, {agent_brain.BASE_URL_ENV: self.server.url}), \
                mock.patch.object(cli.agent_brain, "update", update_then_turn_off):
            cli._agent_brain_update_run(paths)
        self.assertEqual(self.target.read_bytes(), fake_binary("0.5.0", self.log))
        self.assertEqual(self.calls(), [])

    def test_update_leaves_hooks_that_run_another_agent_brain(self):
        self.target.parent.mkdir(parents=True)
        self.target.write_bytes(fake_binary("0.4.0", self.log))
        self.target.chmod(0o755)
        self.write_hooks(Path("/usr/local/bin/agent-brain"))
        self.server.publish_binary("0.5.0", fake_binary("0.5.0", self.log))
        self.assertEqual(self.hook().returncode, 0)
        self.assertEqual(self.target.read_bytes(), fake_binary("0.5.0", self.log))
        self.assertEqual(self.calls(), [])

    def test_session_start_skips_when_auto_update_or_agent_brain_is_off(self):
        self.target.parent.mkdir(parents=True)
        self.target.write_bytes(fake_binary("0.4.0"))
        self.target.chmod(0o755)
        self.server.publish_binary("0.5.0", fake_binary("0.5.0"))
        self.hook(AGENTFLEET_AUTO_UPDATE="0")
        self.assertEqual(self.target.read_bytes(), fake_binary("0.4.0"))
        agent_brain.provider_catalog.write_preferences(self.home, {"agentBrain": False})
        self.hook()
        self.assertEqual(self.target.read_bytes(), fake_binary("0.4.0"))
        self.assertNotIn("agentBrainCheck", self.state())

    def test_failed_update_is_logged_not_reported(self):
        self.target.parent.mkdir(parents=True)
        self.target.write_bytes(fake_binary("0.4.0"))
        self.target.chmod(0o755)
        self.server.publish("0.5.0", tar_archive([("agent-brain", fake_binary("0.5.0"))]), digest="0" * 64)
        result = self.hook()
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.target.read_bytes(), fake_binary("0.4.0"))
        self.assertNotIn("agent-brain", result.stdout)
        log = (self.home / ".claude" / "agentfleet" / "auto-update.log").read_text(encoding="utf-8")
        self.assertIn("agent-brain update failed", log)


if __name__ == "__main__":
    unittest.main()
