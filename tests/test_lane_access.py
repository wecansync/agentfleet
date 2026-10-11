import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

class TestLaneAccess(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"
        self.config_home = self.tmp / "config"
        
        self.home_claude = self.home / ".claude"
        self.home_claude.mkdir(parents=True)
        self.config_delegate = self.config_home / "delegate-skills"
        self.config_delegate.mkdir(parents=True)
        
        self.settings_path = self.home_claude / "settings.json"
        self.settings_path.write_text(json.dumps({
            "modelPicker": {
                "options": [
                    {"model": "test-model", "description": "test"}
                ]
            }
        }))
        
        self.fleet_path = self.home_claude / "fleet.json"
        
        self.generator_script = str(Path(__file__).parent.parent / "scripts" / "generate-claude-agents.mjs")
        
    def tearDown(self):
        shutil.rmtree(self.tmp)
        
    def run_gen(self, *args, check=True):
        cmd = ["node", self.generator_script, "--home", str(self.home), "--config-home", str(self.config_home)] + list(args)
        res = subprocess.run(cmd, capture_output=True, text=True)
        if check and res.returncode != 0:
            self.fail(f"Generator failed: {res.stderr}\n{res.stdout}")
        return res

    def test_default_full_access(self):
        self.fleet_path.write_text(json.dumps({
            "version": "delegate-fleet.v1",
            "lanes": {
                "review": {"model": "test-model", "implementer": "claude"},
                "security-review": {"model": "test-model", "implementer": "claude"},
                "diagnose-static": {"model": "test-model", "implementer": "claude"}
            }
        }))
        
        self.run_gen()
        
        agents_dir = self.home_claude / "agents"
        
        review = (agents_dir / "fleet-review.md").read_text()
        self.assertIn("tools: Read, Glob, Grep, LSP, Bash, Edit, Write", review)
        self.assertIn("FIX MODE", review)
        
        sec = (agents_dir / "fleet-security-review.md").read_text()
        self.assertIn("tools: Read, Glob, Grep, LSP, Bash, Edit, Write", sec)
        self.assertIn("FIX MODE", sec)
        
        diag = (agents_dir / "fleet-diagnose-static.md").read_text()
        self.assertIn("tools: Read, Glob, Grep, LSP, Bash", diag)
        self.assertNotIn("FIX MODE", diag)
        
    def test_run_access(self):
        self.fleet_path.write_text(json.dumps({
            "version": "delegate-fleet.v1",
            "lanes": {
                "review": {"model": "test-model", "implementer": "claude"},
                "security-review": {"model": "test-model", "implementer": "claude"}
            }
        }))
        
        agentfleet = self.home_claude / "agentfleet"
        agentfleet.mkdir()
        (agentfleet / "config.json").write_text(json.dumps({
            "format": "agentfleet.config.v1",
            "reviewerAccess": "run"
        }))
        
        self.run_gen()
        
        agents_dir = self.home_claude / "agents"
        review = (agents_dir / "fleet-review.md").read_text()
        self.assertIn("tools: Read, Glob, Grep, LSP, Bash", review)
        self.assertNotIn("Write", review)
        self.assertIn("You cannot edit files; when a fix is needed, describe it precisely", review)
        
    def test_readonly_access(self):
        self.fleet_path.write_text(json.dumps({
            "version": "delegate-fleet.v1",
            "lanes": {
                "review": {"model": "test-model", "implementer": "claude"},
                "diagnose-static": {"model": "test-model", "implementer": "claude"}
            }
        }))
        
        agentfleet = self.home_claude / "agentfleet"
        agentfleet.mkdir()
        (agentfleet / "config.json").write_text(json.dumps({
            "format": "agentfleet.config.v1",
            "reviewerAccess": "read-only"
        }))
        
        self.run_gen()
        
        agents_dir = self.home_claude / "agents"
        review = (agents_dir / "fleet-review.md").read_text()
        self.assertIn("tools: Read, Glob, Grep, LSP\n", review)
        self.assertNotIn("FIX MODE", review)
        
    def test_invalid_preference_defaults_to_full(self):
        self.fleet_path.write_text(json.dumps({
            "version": "delegate-fleet.v1",
            "lanes": {
                "review": {"model": "test-model", "implementer": "claude"}
            }
        }))
        
        agentfleet = self.home_claude / "agentfleet"
        agentfleet.mkdir()
        (agentfleet / "config.json").write_text(json.dumps({
            "format": "agentfleet.config.v1",
            "reviewerAccess": "invalid-value"
        }))
        self.run_gen()
        
        agents_dir = self.home_claude / "agents"
        review = (agents_dir / "fleet-review.md").read_text()
        self.assertIn("tools: Read, Glob, Grep, LSP, Bash, Edit, Write", review)
        
    def test_custom_readonly_lane(self):
        self.fleet_path.write_text(json.dumps({
            "version": "delegate-fleet.v1",
            "lanes": {
                "custom-static": {"model": "test-model", "implementer": "claude", "readOnly": True}
            }
        }))
        self.run_gen()
        
        agents_dir = self.home_claude / "agents"
        custom = (agents_dir / "fleet-custom-static.md").read_text()
        self.assertIn("tools: Read, Glob, Grep, LSP\n", custom)
        
    def test_tools_override(self):
        self.fleet_path.write_text(json.dumps({
            "version": "delegate-fleet.v1",
            "lanes": {
                "review": {"model": "test-model", "implementer": "claude", "tools": ["Read", "mcp__test__tool"]}
            }
        }))
        self.run_gen()
        
        agents_dir = self.home_claude / "agents"
        review = (agents_dir / "fleet-review.md").read_text()
        self.assertIn("tools: Read, mcp__test__tool", review)
        
    def test_tools_override_invalid(self):
        self.fleet_path.write_text(json.dumps({
            "version": "delegate-fleet.v1",
            "lanes": {
                "review": {"model": "test-model", "implementer": "claude", "tools": ["invalid-tool!"]}
            }
        }))
        res = self.run_gen(check=False)
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("invalid tool override", res.stderr)

    def test_project_override(self):
        self.fleet_path.write_text(json.dumps({
            "version": "delegate-fleet.v1",
            "lanes": {
                "review": {"model": "test-model", "implementer": "claude"},
                "plan": {"model": "test-model", "implementer": "claude"}
            }
        }))
        # Make global first
        self.run_gen()
        
        project_dir = self.tmp / "project"
        project_claude = project_dir / ".claude"
        project_claude.mkdir(parents=True)
        (project_claude / "fleet.local.json").write_text(json.dumps({
            "version": "delegate-fleet.local.v1",
            "lanes": {
                "review": {"effort": "low"}
            }
        }))
        
        # Test 1: project render
        self.run_gen("--project", str(project_dir))
        
        proj_agents = project_claude / "agents"
        self.assertTrue(proj_agents.exists())
        review = (proj_agents / "fleet-review.md").read_text()
        self.assertIn("effort: low", review)
        
        self.assertFalse((proj_agents / "fleet-plan.md").exists())
        
        # Test 2: --check works
        res = self.run_gen("--project", str(project_dir), "--check")
        self.assertEqual(res.returncode, 0)
        
        # Test 3: stale removal
        (project_claude / "fleet.local.json").write_text(json.dumps({
            "version": "delegate-fleet.local.v1",
            "lanes": {}
        }))
        self.run_gen("--project", str(project_dir))
        self.assertFalse((proj_agents / "fleet-review.md").exists())
        
    def test_project_invalid_keys(self):
        self.fleet_path.write_text(json.dumps({
            "version": "delegate-fleet.v1",
            "lanes": {
                "review": {"model": "test-model", "implementer": "claude"}
            }
        }))
        project_dir = self.tmp / "project"
        project_claude = project_dir / ".claude"
        project_claude.mkdir(parents=True)
        
        # Unknown lane
        (project_claude / "fleet.local.json").write_text(json.dumps({
            "version": "delegate-fleet.local.v1",
            "lanes": {
                "nonexistent": {"effort": "low"}
            }
        }))
        res = self.run_gen("--project", str(project_dir), check=False)
        self.assertNotEqual(res.returncode, 0)
        
        # Invalid key
        (project_claude / "fleet.local.json").write_text(json.dumps({
            "version": "delegate-fleet.local.v1",
            "lanes": {
                "review": {"implementer": "claude"}
            }
        }))
        res = self.run_gen("--project", str(project_dir), check=False)
        self.assertNotEqual(res.returncode, 0)
        
    def test_refusal_hand_authored(self):
        self.fleet_path.write_text(json.dumps({
            "version": "delegate-fleet.v1",
            "lanes": {
                "review": {"model": "test-model", "implementer": "claude"}
            }
        }))
        agents_dir = self.home_claude / "agents"
        agents_dir.mkdir()
        (agents_dir / "fleet-review.md").write_text("Hand authored file")
        
        res = self.run_gen(check=False)
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("refusing to overwrite hand-authored", res.stderr)


    def test_repo_templates_match_generator_default(self):
        root = Path(__file__).parent.parent
        repo_fleet = root / "config" / "delegate-fleet.json"
        self.fleet_path.write_text(repo_fleet.read_text())
        
        self.settings_path.write_text(json.dumps({
            "modelPicker": {
                "options": [
                    {"model": "opus", "description": "Opus"},
                    {"model": "sonnet", "description": "Sonnet"},
                    {"model": "haiku", "description": "Haiku"}
                ]
            }
        }))
        self.run_gen()
        
        repo_agents_dir = root / "agents"
        gen_agents_dir = self.home_claude / "agents"
        
        for agent_file in repo_agents_dir.glob("fleet-*.md"):
            gen_file = gen_agents_dir / agent_file.name
            self.assertTrue(gen_file.exists(), f"Missing generated file for {agent_file.name}")
            self.assertEqual(agent_file.read_text(), gen_file.read_text(), f"Mismatch in template {agent_file.name}")

if __name__ == '__main__':
    unittest.main()



class ProjectTargetSafetyTests(unittest.TestCase):
    """A project whose .claude points at the global one must never be written or pruned."""

    @unittest.skipIf(shutil.which("node") is None, "Node.js is required")
    def test_symlinked_project_claude_is_refused_and_global_agents_survive(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            home, project = root / "home", root / "project"
            (home / ".claude" / "agents").mkdir(parents=True)
            project.mkdir()
            (project / ".claude").symlink_to(home / ".claude", target_is_directory=True)
            (home / ".claude" / "settings.json").write_text(json.dumps({"model": "opus", "modelPicker": {"options": [{"model": "opus"}, {"model": "sonnet"}]}}), encoding="utf-8")
            (home / ".claude" / "fleet.json").write_text(json.dumps({"version": "delegate-fleet.v1", "lanes": {
                "implement": {"implementer": "claude", "model": "sonnet"},
                "plan": {"implementer": "claude", "model": "opus", "readOnly": True},
            }}), encoding="utf-8")
            generator = Path(__file__).resolve().parent.parent / "scripts" / "generate-claude-agents.mjs"
            base = ["node", str(generator), "--home", str(home), "--config-home", str(root / "config"), "--quiet"]
            self.assertEqual(subprocess.run(base, capture_output=True, text=True).returncode, 0)
            (home / ".claude" / "fleet.local.json").write_text(json.dumps({"version": "delegate-fleet.local.v1", "lanes": {"plan": {"model": "sonnet"}}}), encoding="utf-8")
            refused = subprocess.run(base + ["--project", str(project)], capture_output=True, text=True)
            self.assertNotEqual(refused.returncode, 0)
            self.assertIn("symlinked", refused.stderr)
            self.assertEqual(sorted(path.name for path in (home / ".claude" / "agents").iterdir()), ["fleet-implement.md", "fleet-plan.md"])
