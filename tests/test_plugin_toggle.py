#!/usr/bin/env python3
"""Hermetic tests for `agentfleet config context7|security-guidance|playwright|egm|security-audit`."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable
AGENTFLEET = ROOT / "bin" / "agentfleet"
# Records each call; plugin install/enable/disable update Claude Code's files the way the real CLI does.
FAKE_CLAUDE = r'''#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

claude = Path.home() / ".claude"
state_path = Path.home() / "fake-state.json"
log = Path.home() / "claude-calls.jsonl"
argv = sys.argv[1:]
with log.open("a", encoding="utf-8") as handle:
    handle.write(json.dumps({"argv": argv, "secret": os.environ.get("ANTHROPIC_API_KEY")}) + "\n")
state = json.loads(state_path.read_text()) if state_path.exists() else {"marketplaces": []}
installed_path = claude / "plugins" / "installed_plugins.json"
settings_path = claude / "settings.json"
installed = json.loads(installed_path.read_text()) if installed_path.exists() else {"plugins": {}}
settings = json.loads(settings_path.read_text()) if settings_path.exists() else {}
if argv == ["plugin", "marketplace", "list", "--json"]:
    print(json.dumps(state["marketplaces"]))
elif argv[:3] == ["plugin", "marketplace", "add"]:
    state["marketplaces"].append({"name": argv[3].split("/")[1].replace("elephant-goldfish-model", "egm"),
                                  "source": "github", "repo": argv[3]})
    state_path.write_text(json.dumps(state))
elif argv[:2] == ["plugin", "install"] and argv[3:] == ["--scope", "user"]:
    installed_path.parent.mkdir(parents=True, exist_ok=True)
    installed["plugins"][argv[2]] = [{"scope": "user"}]
    installed_path.write_text(json.dumps(installed))
    settings.setdefault("enabledPlugins", {})[argv[2]] = True
    settings_path.write_text(json.dumps(settings))
elif argv[:2] in (["plugin", "enable"], ["plugin", "disable"]) and argv[3:] == ["--scope", "user"]:
    settings.setdefault("enabledPlugins", {})[argv[2]] = argv[1] == "enable"
    settings_path.write_text(json.dumps(settings))
else:
    sys.exit(2)
'''
OFFICIAL = {"name": "claude-plugins-official", "source": "git",
            "url": "https://github.com/anthropics/claude-plugins-official.git"}


@unittest.skipIf(os.name == "nt", "the fake claude is a POSIX script")
class PluginToggleTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="agentfleet-plugin-toggle-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.home = self.root / "home"
        (self.home / ".claude").mkdir(parents=True)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        fake = self.bin / "claude"
        fake.write_text(FAKE_CLAUDE, encoding="utf-8")
        fake.chmod(0o755)
        self.calls_path = self.home / "claude-calls.jsonl"

    def markets(self, *entries: dict) -> None:
        (self.home / "fake-state.json").write_text(json.dumps({"marketplaces": list(entries)}), encoding="utf-8")

    def run_agentfleet(self, *args: str) -> subprocess.CompletedProcess:
        env = {"PATH": os.pathsep.join([str(self.bin), os.defpath]), "HOME": str(self.home), "LANG": "C.UTF-8",
               "PYTHONDONTWRITEBYTECODE": "1", "ANTHROPIC_API_KEY": "plugin-toggle-test-secret"}
        return subprocess.run([PYTHON, str(AGENTFLEET), "--home", str(self.home), "--config-home",
                               str(self.root / "config"), *args],
                              text=True, capture_output=True, env=env, cwd=ROOT, timeout=20)

    def calls(self) -> list[list[str]]:
        if not self.calls_path.exists():
            return []
        return [json.loads(line)["argv"] for line in self.calls_path.read_text(encoding="utf-8").splitlines()]

    def test_status_reads_files_without_running_claude(self):
        for setting in ("context7", "security-guidance", "playwright", "egm"):
            with self.subTest(setting=setting):
                result = self.run_agentfleet("config", setting, "status")
                self.assertEqual(result.stdout.strip(), f"{setting}: off (not installed)")
        self.assertEqual(self.calls(), [])

    def test_on_installs_from_the_registered_official_marketplace_then_off_disables(self):
        self.markets(OFFICIAL)
        result = self.run_agentfleet("config", "context7", "on")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls(), [["plugin", "marketplace", "list", "--json"],
                                        ["plugin", "install", "context7@claude-plugins-official", "--scope", "user"]])
        self.assertEqual(self.run_agentfleet("config", "context7", "status").stdout.strip(), "context7: on")
        self.assertIn("already on", self.run_agentfleet("config", "context7", "on").stdout)
        off = self.run_agentfleet("config", "context7", "off")
        self.assertIn("stays installed", off.stdout)
        self.assertEqual(self.calls()[-1], ["plugin", "disable", "context7@claude-plugins-official", "--scope", "user"])
        self.assertEqual(self.run_agentfleet("config", "context7", "status").stdout.strip(),
                         "context7: off (installed, disabled)")
        # Turning it back on re-enables it rather than reinstalling.
        self.run_agentfleet("config", "context7", "on")
        self.assertEqual(self.calls()[-1], ["plugin", "enable", "context7@claude-plugins-official", "--scope", "user"])

    def test_egm_adds_googles_marketplace_when_missing(self):
        self.markets(OFFICIAL)
        result = self.run_agentfleet("config", "egm", "on")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(["plugin", "marketplace", "add", "google/elephant-goldfish-model"], self.calls())
        self.assertEqual(self.calls()[-1], ["plugin", "install", "egm@egm", "--scope", "user"])

    def test_a_foreign_marketplace_with_the_same_name_is_left_alone(self):
        foreign = (
            ("egm", {"name": "egm", "source": "github", "repo": "someone/else"}),
            # Look-alikes that merely contain the repository name.
            ("context7", {"name": "claude-plugins-official", "source": "git",
                          "url": "https://evil.example/anthropics/claude-plugins-official"}),
            ("context7", {"name": "claude-plugins-official", "source": "git",
                          "url": "https://github.com/anthropics/claude-plugins-official-fork.git"}),
            ("egm", {"name": "egm", "source": "github", "repo": "evil/google/elephant-goldfish-model"}),
        )
        for setting, entry in foreign:
            with self.subTest(entry=entry):
                self.markets(entry)
                result = self.run_agentfleet("config", setting, "on")
                self.assertEqual(result.returncode, 1)
                self.assertIn("leaving it untouched", result.stderr)
                self.assertFalse([c for c in self.calls() if c[:2] == ["plugin", "install"]])

    def test_credentials_are_not_passed_to_claude(self):
        self.markets(OFFICIAL)
        self.run_agentfleet("config", "security-guidance", "on")
        records = [json.loads(line) for line in self.calls_path.read_text(encoding="utf-8").splitlines()]
        self.assertTrue(records)
        self.assertTrue(all(r["secret"] is None for r in records))

    def test_invalid_value_is_rejected(self):
        self.assertEqual(self.run_agentfleet("config", "egm", "maybe").returncode, 2)
        self.assertEqual(self.calls(), [])


if __name__ == "__main__":
    unittest.main()


# A git stand-in for the security-audit skill: it records each call and builds the
# pinned skill folder on checkout, so no network is used. Marker files in the
# state directory make it fail the fetch, report another commit, or add a symlink.
FAKE_GIT = r'''#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

state = Path(__STATE__)
args = sys.argv[1:]
while args[:1] == ["-c"]:
    args = args[2:]
with (state / "git-calls.jsonl").open("a", encoding="utf-8") as handle:
    handle.write(json.dumps(args) + "\n")
here = Path.cwd()
if args[0] == "init":
    (here / ".git").mkdir()
elif args[0] == "fetch":
    if (state / "fail-fetch").exists():
        sys.exit(1)
    (here / ".git" / "FETCHED").write_text(args[-1])
elif args[0] == "checkout":
    skill = here / "skills" / "security-audit"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: security-audit\n---\n")
    (skill / "validate-findings.cjs").write_text("// validator\n")
    if (state / "add-symlink").exists():
        os.symlink("/etc/hostname", skill / "linked")
elif args[0] == "rev-parse":
    print("0" * 40 if (state / "wrong-commit").exists() else (here / ".git" / "FETCHED").read_text())
else:
    sys.exit(2)
'''
PINNED = "c1c8a8c1471069fb0e188eeaff69b8e8db6564a8"


@unittest.skipIf(os.name == "nt", "the fake git is a POSIX script")
class SecurityAuditSkillTests(unittest.TestCase):
    setUp_base = PluginToggleTests.setUp
    run_agentfleet = PluginToggleTests.run_agentfleet
    calls = PluginToggleTests.calls

    def setUp(self):
        self.setUp_base()
        fake = self.bin / "git"
        fake.write_text(FAKE_GIT.replace("__STATE__", repr(str(self.root))), encoding="utf-8")
        fake.chmod(0o755)
        self.skill = self.home / ".claude" / "skills" / "security-audit"

    def git_calls(self) -> list[list[str]]:
        path = self.root / "git-calls.jsonl"
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []

    def leftovers(self) -> list[str]:
        parent = self.skill.parent
        return sorted(p.name for p in parent.iterdir() if p.name != "security-audit") if parent.exists() else []

    def test_on_copies_the_pinned_commit_with_a_marker_and_off_removes_it(self):
        self.assertEqual(self.run_agentfleet("config", "security-audit", "status").stdout.strip(),
                         "security-audit: off (not installed)")
        result = self.run_agentfleet("config", "security-audit", "on")
        self.assertEqual(result.returncode, 0, result.stderr)
        fetch = next(c for c in self.git_calls() if c[0] == "fetch")
        self.assertEqual(fetch[-2:], ["https://github.com/cloudflare/security-audit-skill.git", PINNED])
        self.assertTrue((self.skill / "SKILL.md").is_file())
        self.assertIn(PINNED, (self.skill / ".agentfleet-skill").read_text(encoding="utf-8"))
        self.assertEqual(self.leftovers(), [])
        self.assertEqual(self.run_agentfleet("config", "security-audit", "status").stdout.strip(), "security-audit: on")
        self.assertIn("already on", self.run_agentfleet("config", "security-audit", "on").stdout)
        result = self.run_agentfleet("config", "security-audit", "off")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("its copy is removed", result.stdout)
        self.assertFalse(self.skill.exists())
        self.assertEqual(self.calls(), [])

    def test_a_copy_installed_outside_agentfleet_is_never_removed(self):
        self.skill.mkdir(parents=True)
        (self.skill / "SKILL.md").write_text("mine\n", encoding="utf-8")
        self.assertEqual(self.run_agentfleet("config", "security-audit", "status").stdout.strip(),
                         "security-audit: on (installed outside AgentFleet)")
        result = self.run_agentfleet("config", "security-audit", "off")
        self.assertEqual(result.returncode, 1)
        self.assertIn("not installed by AgentFleet", result.stderr)
        self.assertEqual((self.skill / "SKILL.md").read_text(encoding="utf-8"), "mine\n")

    def test_failures_leave_nothing_installed(self):
        for marker, message in (("fail-fetch", "git fetch failed"), ("wrong-commit", "pinned commit"),
                                ("add-symlink", "symbolic link")):
            with self.subTest(marker=marker):
                (self.root / marker).write_text("", encoding="utf-8")
                result = self.run_agentfleet("config", "security-audit", "on")
                (self.root / marker).unlink()
                self.assertEqual(result.returncode, 1)
                self.assertIn(message, result.stderr)
                self.assertFalse(self.skill.exists())
                self.assertEqual(self.leftovers(), [])

