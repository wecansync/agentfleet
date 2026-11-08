#!/usr/bin/env python3
"""Hermetic tests for `agentfleet config computer-use` (Open Computer Use, off by default)."""
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
# Records each call and edits ~/.claude.json the way `claude mcp add|remove --scope user` does.
FAKE_CLAUDE = r'''#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

config = Path.home() / ".claude.json"
log = Path.home() / "claude-calls.jsonl"
argv = sys.argv[1:]
with log.open("a", encoding="utf-8") as handle:
    handle.write(json.dumps({"argv": argv, "secret": os.environ.get("ANTHROPIC_API_KEY")}) + "\n")
data = json.loads(config.read_text()) if config.exists() else {}
if (Path.home() / "claude-fail").exists():
    sys.exit(1)
if argv[:4] == ["mcp", "add", "--scope", "user"] and "--" in argv:
    rest = argv[argv.index("--") + 1:]
    data.setdefault("mcpServers", {})[argv[4]] = {"type": "stdio", "command": rest[0], "args": rest[1:]}
elif argv[:4] == ["mcp", "remove", "--scope", "user"]:
    data.get("mcpServers", {}).pop(argv[4], None)
else:
    sys.exit(2)
config.write_text(json.dumps(data))
'''


def executable(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)


@unittest.skipIf(os.name == "nt", "the fake commands are POSIX scripts")
class ComputerUseTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="agentfleet-computer-use-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.home = self.root / "home"
        (self.home / ".claude").mkdir(parents=True)
        self.config = self.root / "config"
        self.claude_bin = self.root / "claude-bin"
        self.claude_bin.mkdir()
        executable(self.claude_bin / "claude", FAKE_CLAUDE)
        self.ocu_bin = self.root / "ocu-bin"
        self.ocu_bin.mkdir()
        executable(self.ocu_bin / "ocu", "#!/bin/sh\nexit 0\n")
        executable(self.ocu_bin / "npx", "#!/bin/sh\nexit 0\n")
        self.user_config = self.home / ".claude.json"
        self.calls_path = self.home / "claude-calls.jsonl"

    def run_agentfleet(self, *args: str, with_ocu: bool = True, **extra: str) -> subprocess.CompletedProcess:
        path = [str(self.claude_bin)] + ([str(self.ocu_bin)] if with_ocu else []) + [os.defpath]
        env = {"PATH": os.pathsep.join(path), "HOME": str(self.home), "LANG": "C.UTF-8",
               "PYTHONDONTWRITEBYTECODE": "1", "ANTHROPIC_API_KEY": "computer-use-test-secret", **extra}
        return subprocess.run(
            [PYTHON, str(AGENTFLEET), "--home", str(self.home), "--config-home", str(self.config), *args],
            text=True, capture_output=True, env=env, cwd=ROOT, timeout=20,
        )

    def calls(self) -> list[dict]:
        if not self.calls_path.exists():
            return []
        return [json.loads(line) for line in self.calls_path.read_text(encoding="utf-8").splitlines()]

    def servers(self) -> dict:
        return json.loads(self.user_config.read_text(encoding="utf-8")).get("mcpServers", {})

    def test_off_by_default(self):
        result = self.run_agentfleet("config", "computer-use", "status")
        self.assertEqual((result.returncode, result.stdout.strip()), (0, "computer-use: off"))
        self.assertEqual(self.calls(), [])

    def test_on_registers_the_absolute_ocu_path_for_the_user_scope(self):
        result = self.run_agentfleet("config", "computer-use", "on")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Restart Claude Code", result.stdout)
        self.assertIn("main session", result.stdout)
        ocu = str((self.ocu_bin / "ocu").resolve())
        self.assertEqual([c["argv"] for c in self.calls()],
                         [["mcp", "add", "--scope", "user", "open-computer-use", "--", ocu, "mcp"]])
        # Provider credentials are not passed to the Claude CLI.
        self.assertEqual([c["secret"] for c in self.calls()], [None])
        self.assertEqual(self.servers()["open-computer-use"]["command"], ocu)
        self.assertEqual(self.run_agentfleet("config", "computer-use", "status").stdout.strip(), "computer-use: on")
        again = self.run_agentfleet("config", "computer-use", "on")
        self.assertIn("already on", again.stdout)
        self.assertEqual(len(self.calls()), 1)

    def test_off_removes_only_its_registration(self):
        self.user_config.write_text(json.dumps({"mcpServers": {"open-computer-use": {}, "exa": {"type": "http"}}}),
                                    encoding="utf-8")
        result = self.run_agentfleet("config", "computer-use", "off")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("npm package stays installed", result.stdout)
        self.assertEqual(self.servers(), {"exa": {"type": "http"}})
        again = self.run_agentfleet("config", "computer-use", "off")
        self.assertIn("already off", again.stdout)
        self.assertEqual(len(self.calls()), 1)

    def test_on_without_ocu_installed_explains_how_to_install_it(self):
        result = self.run_agentfleet("config", "computer-use", "on", with_ocu=False)
        self.assertEqual(result.returncode, 1)
        self.assertIn("npm install -g open-computer-use@", result.stderr)
        self.assertEqual(self.calls(), [])
        status = self.run_agentfleet("config", "computer-use", "status", with_ocu=False)
        self.assertIn("not installed", status.stdout)

    def test_cli_failure_is_reported(self):
        (self.home / "claude-fail").write_text("", encoding="utf-8")
        result = self.run_agentfleet("config", "computer-use", "on")
        self.assertEqual(result.returncode, 1)
        self.assertIn("could not register", result.stderr)

    def test_mobile_mcp_is_off_by_default_and_toggles_with_telemetry_off(self):
        self.assertEqual(self.run_agentfleet("config", "mobile-mcp", "status").stdout.strip(), "mobile-mcp: off")
        result = self.run_agentfleet("config", "mobile-mcp", "on")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("telemetry off", result.stdout)
        npx = str((self.ocu_bin / "npx").resolve())
        self.assertEqual([c["argv"] for c in self.calls()], [[
            "mcp", "add", "--scope", "user", "mobile-mcp", "-e", "MOBILEMCP_DISABLE_TELEMETRY=1", "--",
            npx, "-y", "@mobilenext/mobile-mcp@1.0.16"]])
        self.assertEqual(self.run_agentfleet("config", "mobile-mcp", "status").stdout.strip(), "mobile-mcp: on")
        # Open Computer Use stays off: each setting registers only its own server.
        self.assertEqual(set(self.servers()), {"mobile-mcp"})
        off = self.run_agentfleet("config", "mobile-mcp", "off")
        self.assertEqual(off.returncode, 0, off.stderr)
        self.assertEqual(self.servers(), {})

    def test_mobile_mcp_without_npx_explains_what_to_install(self):
        result = self.run_agentfleet("config", "mobile-mcp", "on", with_ocu=False)
        self.assertEqual(result.returncode, 1)
        self.assertIn("install Node.js", result.stderr)
        self.assertEqual(self.calls(), [])

    def test_invalid_value_is_rejected(self):
        result = self.run_agentfleet("config", "computer-use", "maybe")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.calls(), [])


if __name__ == "__main__":
    unittest.main()
