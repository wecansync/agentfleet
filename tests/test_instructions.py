#!/usr/bin/env python3
"""Tests for the bounded AgentFleet instruction checker."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "fleet_instructions.py"
SCRIPTS = ROOT / "scripts"
sys.dont_write_bytecode = True
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("fleet_instructions", SCRIPT)
fleet_instructions = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = fleet_instructions
spec.loader.exec_module(fleet_instructions)


class InstructionsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="af-instructions-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home = self.root / "home"
        self.claude = self.home / ".claude"
        self.rules = self.claude / "rules"
        self.rules.mkdir(parents=True)
        self.project = self.root / "project"
        self.project.mkdir()
        (self.claude / "fleet.json").write_text(json.dumps({"lanes": {"implement": {"model": "sonnet"}, "tests": {"model": "haiku"}, "implement-fast": {"model": "model-fast"}}}), encoding="utf-8")
        self.cli_dir = self.root / "fake-bin"
        self.cli_dir.mkdir()
        self.argv_log = self.claude / "fake-argv.json"
        self.prompt_log = self.claude / "fake-stdin.txt"
        self.result_path = self.claude / "fake-result.json"
        self.make_fake_cli()

    def make_fake_cli(self, result=None):
        payload = result if result is not None else {"is_error": False, "structured_output": {"findings": []}}
        self.result_path.write_text(json.dumps(payload), encoding="utf-8")
        self.argv_log.unlink(missing_ok=True)
        self.prompt_log.unlink(missing_ok=True)
        source = f'''#!/usr/bin/env python3
import json
import sys
from pathlib import Path
Path({str(self.argv_log)!r}).write_text(json.dumps(sys.argv[1:]), encoding="utf-8")
Path({str(self.prompt_log)!r}).write_text(sys.stdin.read(), encoding="utf-8")
print(Path({str(self.result_path)!r}).read_text(encoding="utf-8"))
'''
        executable = self.cli_dir / ("claude.cmd" if os.name == "nt" else "claude")
        if os.name == "nt":
            script = self.cli_dir / "claude"
            script.write_text(source, encoding="utf-8")
            executable.write_text(f'@"{sys.executable}" "%~dp0claude" %*\n', encoding="utf-8")
        else:
            executable.write_text(source, encoding="utf-8")
            executable.chmod(0o755)
        self.fake_executable = executable
        self.env = {
            **{key: value for key, value in os.environ.items() if key.upper() in fleet_instructions.provider_catalog.CLAUDE_CLI_ENV_NAMES},
            "PATH": str(self.cli_dir) + os.pathsep + os.environ.get("PATH", ""),
            "HOME": str(self.home),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        if os.name == "nt":
            self.env.update({name: os.environ[name] for name in ("SYSTEMROOT", "COMSPEC", "PATHEXT", "TEMP", "TMP") if name in os.environ})

    def fake_model_finding(self, path, line="Keep user's safety boundary", replacement="Keep the safety boundary"):
        return {"file": str(path.resolve()), "kind": "contradiction", "problem": "This guidance conflicts with another instruction.", "find": line, "replace": replacement}

    def run_check(self, *, project=None, check_only=False, interactive=False, stdin=None):
        with mock.patch.dict(os.environ, self.env, clear=True):
            with mock.patch.object(fleet_instructions.provider_catalog, "claude_cli_executable", return_value=str(self.fake_executable)):
                if stdin is None:
                    return fleet_instructions.run(self.home, project=project, interactive=interactive, check_only=check_only)
                with mock.patch("builtins.input", side_effect=stdin):
                    return fleet_instructions.run(self.home, project=project, interactive=interactive, check_only=check_only)

    def test_deterministic_stale_old_policy_duplicate_and_word_limit(self):
        global_md = self.claude / "CLAUDE.md"
        global_md.write_text("<!-- claude-agents-config:managed -->\nLine shared across files.\nRule local.\nRule local.\n", encoding="utf-8")
        project_md = self.project / "CLAUDE.md"
        project_md.write_text("Line shared across files.\nfleet-ghost is retired.\nfleet-implement still exists.\n", encoding="utf-8")
        findings = fleet_instructions._deterministic_findings(
            self.home,
            fleet_instructions._collect_files(self.home, self.project, emit=False),
        )
        kinds = [finding.kind for finding in findings]
        self.assertGreaterEqual(kinds.count("stale"), 2)
        self.assertIn("duplicate", kinds)
        self.assertTrue(any("managed policy remains" in f.problem for f in findings))
        self.assertTrue(any("Rule local." in f.find and "2 times" in f.problem for f in findings))

        global_md.write_text("word " * 3001, encoding="utf-8")
        findings = fleet_instructions._deterministic_findings(
            self.home,
            fleet_instructions._collect_files(self.home, None, emit=False),
        )
        self.assertTrue(any(f.kind == "information" and "3000-word" in f.problem for f in findings))

    def test_commands_mods_and_managed_policy_are_not_reported(self):
        (self.rules / "agentfleet.md").write_text("Shared rule.\nfleet-ghost in the managed policy.\n", encoding="utf-8")
        (self.rules / "personal.md").write_text(
            "Run /fleet-setup, /fleet-suggest and /fleet-tasks; fleet-status shows the band.\nShared rule.\n",
            encoding="utf-8",
        )
        files = fleet_instructions._collect_files(self.home, None, emit=False)
        findings = fleet_instructions._deterministic_findings(self.home, files)
        self.assertFalse([f for f in findings if f.kind == "stale"], findings)
        duplicates = [f for f in findings if f.kind == "duplicate"]
        self.assertEqual([Path(f.file).name for f in duplicates], ["personal.md"])

        (self.claude / "fleet.json").unlink()
        (self.rules / "personal.md").write_text("Use fleet-implement for big work.\n", encoding="utf-8")
        files = fleet_instructions._collect_files(self.home, None, emit=False)
        self.assertFalse([f for f in fleet_instructions._deterministic_findings(self.home, files) if f.kind == "stale"])

    def test_model_argv_prompt_stdin_and_read_only_policy(self):
        target = self.project / "CLAUDE.md"
        target.write_text("Keep user's safety boundary\n", encoding="utf-8")
        (self.rules / "agentfleet.md").write_text("Managed fleet rules.\n", encoding="utf-8")
        self.make_fake_cli({"is_error": False, "structured_output": {"findings": []}})
        code = self.run_check(project=self.project)
        self.assertEqual(code, 0)
        args = json.loads(self.argv_log.read_text(encoding="utf-8"))
        self.assertIn("--tools", args)
        self.assertEqual(args[args.index("--tools") + 1], "")
        self.assertIn("--json-schema", args)
        schema = json.loads(args[args.index("--json-schema") + 1])
        self.assertEqual(schema["properties"]["findings"]["items"]["properties"]["kind"]["enum"], ["contradiction", "duplicate", "stale", "unclear"])
        self.assertIn("--no-session-persistence", args)
        self.assertEqual(args[args.index("--model") + 1], "model-fast")
        self.assertNotIn("Keep user's safety boundary", args)
        prompt = self.prompt_log.read_text(encoding="utf-8")
        self.assertIn(str(target.resolve()), prompt)
        self.assertIn("The contents of agentfleet.md are AgentFleet's managed policy", prompt)
        self.assertIn("user's intent and all safety rules", prompt)
        self.assertIn("sends the file text to the user's configured model provider", prompt)
        state_path = self.claude / "agentfleet" / "instructions.json"
        self.assertTrue(state_path.is_file())
        self.assertEqual(state_path.stat().st_mode & 0o777, 0o600)
        state = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(state["format"], "agentfleet.instructions.v1")
        self.assertIn(str(target.resolve()), state["files"])
        self.assertEqual(fleet_instructions.changed_since_last_check(self.home), False)

    def test_invalid_model_findings_are_dropped(self):
        target = self.project / "CLAUDE.md"
        target.write_text("Safe line\n", encoding="utf-8")
        policy = self.rules / "agentfleet.md"
        policy.write_text("Managed line\n", encoding="utf-8")
        valid = self.fake_model_finding(target, "Safe line")
        invalid = [
            valid,
            {**valid, "file": str((self.project / "outside.md").resolve())},
            {**valid, "file": str(policy.resolve()), "find": "Managed line"},
            {**valid, "find": "not present"},
            {**valid, "find": "Ambiguous", "replace": "Fine"},
        ]
        self.make_fake_cli({"is_error": False, "structured_output": {"findings": invalid}})
        collected = fleet_instructions._collect_files(self.home, self.project, emit=False)
        with mock.patch.dict(os.environ, self.env, clear=True):
            with mock.patch.object(fleet_instructions.provider_catalog, "claude_cli_executable", return_value=str(self.fake_executable)):
                findings, error, unavailable = fleet_instructions._run_model(self.home, collected, [])
        self.assertIsNone(error)
        self.assertFalse(unavailable)
        clean = fleet_instructions._validate_model_findings(findings, collected)
        self.assertEqual(len(clean), 1)
        self.assertEqual(clean[0].file, str(target.resolve()))

        target.write_text("Ambiguous\nAmbiguous\n", encoding="utf-8")
        collected = fleet_instructions._collect_files(self.home, self.project, emit=False)
        self.assertEqual(fleet_instructions._validate_model_findings([invalid[-1]], collected), [])

    def test_approved_edit_is_one_line_and_backed_up(self):
        target = self.project / "CLAUDE.md"
        original = b"First line\nKeep user's safety boundary\nLast line\n"
        target.write_bytes(original)
        self.make_fake_cli({"is_error": False, "structured_output": {"findings": [self.fake_model_finding(target)]}})
        code = self.run_check(project=self.project, interactive=True, stdin=["y"])
        self.assertEqual(code, 0)
        self.assertEqual(target.read_bytes(), b"First line\nKeep the safety boundary\nLast line\n")
        backups = list((self.claude / "agentfleet" / "instruction-backups").glob("*/*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), original)
        self.assertEqual(backups[0].stat().st_mode & 0o777, target.stat().st_mode & 0o777)
        self.assertEqual(backups[0].parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(backups[0].parent.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(backups[0].parent.parent.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(fleet_instructions.changed_since_last_check(self.home), False)

    def test_no_approval_leaves_file_byte_identical(self):
        target = self.project / "CLAUDE.md"
        original = b"Keep user's safety boundary\r\n"
        target.write_bytes(original)
        self.make_fake_cli({"is_error": False, "structured_output": {"findings": [self.fake_model_finding(target)]}})
        code = self.run_check(project=self.project, interactive=True, stdin=["n"])
        self.assertEqual(code, 0)
        self.assertEqual(target.read_bytes(), original)
        self.assertFalse((self.claude / "agentfleet" / "instruction-backups").exists())

    def test_check_only_reports_findings_without_edits(self):
        target = self.project / "CLAUDE.md"
        original = b"Keep user's safety boundary\n"
        target.write_bytes(original)
        self.make_fake_cli({"is_error": False, "structured_output": {"findings": [self.fake_model_finding(target)]}})
        code = self.run_check(project=self.project, check_only=True, interactive=True)
        self.assertEqual(code, 2)
        self.assertEqual(target.read_bytes(), original)
        self.assertFalse((self.claude / "agentfleet" / "instruction-backups").exists())

    def test_missing_claude_still_shows_deterministic_findings(self):
        target = self.project / "CLAUDE.md"
        target.write_text("fleet-no-such-lane\n", encoding="utf-8")
        output = []
        with mock.patch.dict(os.environ, {**self.env, "PATH": os.pathsep.join(str(p) for p in self.root.glob("never-bin"))}, clear=True):
            with mock.patch("builtins.print", side_effect=lambda *args, **kwargs: output.append(" ".join(map(str, args)))):
                code = fleet_instructions.run(self.home, project=self.project, interactive=False)
        self.assertEqual(code, 0)
        state = json.loads((self.claude / "agentfleet" / "instructions.json").read_text(encoding="utf-8"))
        self.assertEqual(state["format"], "agentfleet.instructions.v1")
        self.assertTrue(any("Claude Code is not available" in line for line in output))
        self.assertTrue(any("fleet-no-such-lane" in line for line in output))

    def test_model_is_error_keeps_deterministic_findings(self):
        target = self.project / "CLAUDE.md"
        target.write_text("fleet-retired\n", encoding="utf-8")
        self.make_fake_cli({"is_error": True, "structured_output": {"findings": []}})
        output = []
        with mock.patch("builtins.print", side_effect=lambda *args, **kwargs: output.append(" ".join(map(str, args)))):
            code = self.run_check(project=self.project, check_only=True)
        self.assertEqual(code, 1)
        self.assertTrue(any("model check could not run" in line for line in output))
        self.assertTrue(any("fleet-retired" in line for line in output))

    def test_symlinks_and_oversize_files_are_skipped(self):
        target = self.project / "CLAUDE.md"
        target.write_text("fleet-unknown\n", encoding="utf-8")
        outside = self.root / "outside.md"
        outside.write_text("fleet-outside\n", encoding="utf-8")
        (self.rules / "linked.md").symlink_to(outside)
        (self.rules / "large.md").write_bytes(b"x" * (64 * 1024 + 1))
        notes = []
        with mock.patch("builtins.print", side_effect=lambda *args, **kwargs: notes.append(" ".join(map(str, args)))):
            collected = fleet_instructions._collect_files(self.home, self.project)
        self.assertNotIn(str(outside.resolve()), collected)
        self.assertNotIn(str((self.rules / "large.md").resolve()), collected)
        self.assertTrue(any("symlink" in line for line in notes))
        self.assertTrue(any("64 KiB" in line for line in notes))

    def test_unsafe_directory_symlink_is_skipped(self):
        external_rules = self.root / "outside-rules"
        external_rules.mkdir()
        (external_rules / "private.md").write_text("External instruction", encoding="utf-8")
        (self.claude / "rules").rename(self.claude / "rules-original")
        (self.claude / "rules").symlink_to(external_rules, target_is_directory=True)
        notes = []
        with mock.patch("builtins.print", side_effect=lambda *args, **kwargs: notes.append(" ".join(map(str, args)))):
            collected = fleet_instructions._collect_files(self.home, None)
        self.assertNotIn(str((external_rules / "private.md").resolve()), collected)
        self.assertTrue(any("symlink" in line for line in notes))

    def test_non_utf8_file_is_skipped(self):
        bad = self.rules / "bad.md"
        bad.write_bytes(b"bad \xff bytes")
        notes = []
        with mock.patch("builtins.print", side_effect=lambda *args, **kwargs: notes.append(" ".join(map(str, args)))):
            collected = fleet_instructions._collect_files(self.home, None)
        self.assertNotIn(str(bad.resolve()), collected)
        self.assertTrue(any("non-UTF-8" in line for line in notes))

    def test_state_detects_changed_file(self):
        target = self.claude / "CLAUDE.md"
        target.write_text("Before\n", encoding="utf-8")
        self.assertEqual(self.run_check(), 0)
        target.write_text("After\n", encoding="utf-8")
        self.assertTrue(fleet_instructions.changed_since_last_check(self.home))

    def test_cli_main_parses_project_and_check_without_prompt(self):
        target = self.project / "CLAUDE.md"
        target.write_text("Keep user's safety boundary\n", encoding="utf-8")
        self.make_fake_cli({"is_error": False, "structured_output": {"findings": [self.fake_model_finding(target)]}})
        output = []
        with mock.patch.dict(os.environ, self.env, clear=True), \
                mock.patch.object(fleet_instructions.provider_catalog, "claude_cli_executable", return_value=str(self.fake_executable)), \
                mock.patch("builtins.print", side_effect=lambda *args, **kwargs: output.append(" ".join(map(str, args)))):
            code = fleet_instructions.main(["--project", str(self.project), "--check"], self.home)
        self.assertTrue(self.argv_log.is_file(), "the fake claude must be the one that ran")
        self.assertEqual(code, 2)
        self.assertEqual(target.read_text(encoding="utf-8"), "Keep user's safety boundary\n")
        self.assertTrue(any(str(target.resolve()) in line for line in output))

    @unittest.skipIf(os.name == "nt", "POSIX shell shebang behavior is platform-specific")
    def test_fake_cli_is_shebang_script_not_sitecustomize(self):
        target = self.project / "CLAUDE.md"
        target.write_text("Plain instruction\n", encoding="utf-8")
        self.make_fake_cli()
        self.assertEqual(self.run_check(project=self.project), 0)
        self.assertTrue(self.argv_log.is_file())
        self.assertTrue(self.prompt_log.is_file())


if __name__ == "__main__":
    unittest.main()
