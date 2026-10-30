"""Canonical GitHub repository refs after the planned rename.

The public repository moves from wecansync/claude-agents-config to
wecansync/agentfleet. These tests read files only; they make no network calls.
Installed-state names (markers, metadata, backups, caches) are not renamed and
are checked here so that the rename cannot change them by accident.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OLD_SLUG = "wecansync/claude-agents-config"
NEW_SLUG = "wecansync/agentfleet"

# Files that carry canonical GitHub refs for the repository.
CANONICAL_FILES = [
    "CONTRIBUTING.md",
    "packaging/windows-build.md",
    "mods/lane-guard/README.md",
    "mods/fleet-status/README.md",
    "site/index.html",
    "site/features/index.html",
    "site/docs/index.html",
]


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


class CanonicalRepoRefs(unittest.TestCase):
    def test_no_old_slug_in_canonical_files_except_redirect_note(self):
        # The only allowed mention of the old name is the redirect note in CONTRIBUTING.md.
        for relative in CANONICAL_FILES:
            count = read(relative).count(OLD_SLUG)
            expected = 1 if relative == "CONTRIBUTING.md" else 0
            self.assertEqual(count, expected, f"{relative} mentions {OLD_SLUG} {count} times")

    def test_clone_instructions_use_new_repo_and_directory(self):
        for relative in ("CONTRIBUTING.md", "packaging/windows-build.md"):
            text = read(relative)
            self.assertRegex(text, rf"git clone [^\n]*https://github\.com/{re.escape(NEW_SLUG)}\.git")
            self.assertRegex(text, r"(?m)^cd agentfleet$")
            self.assertNotRegex(text, r"(?m)^cd claude-agents-config$")

    def test_mod_marketplace_install_lines_use_new_repo(self):
        for relative in ("mods/lane-guard/README.md", "mods/fleet-status/README.md"):
            self.assertIn(f"--marketplace {NEW_SLUG}", read(relative), relative)

    def test_site_github_links_use_new_repo(self):
        for relative in ("site/index.html", "site/features/index.html", "site/docs/index.html"):
            text = read(relative)
            links = re.findall(r"https://github\.com/wecansync/[\w.\-]+", text)
            self.assertTrue(links, relative)
            self.assertTrue(all(link.startswith(f"https://github.com/{NEW_SLUG}") for link in links), relative)

    def test_no_reusable_action_reference_to_old_repo(self):
        for path in (ROOT / ".github").rglob("*.yml"):
            self.assertNotIn(f"uses: {OLD_SLUG}", path.read_text(encoding="utf-8"), path.name)
            self.assertNotIn(f"uses: {NEW_SLUG}", path.read_text(encoding="utf-8"), path.name)


class RedirectDocumentation(unittest.TestCase):
    def test_contributing_documents_redirect_and_no_reuse(self):
        text = read("CONTRIBUTING.md")
        self.assertIn("is moving from", text)
        self.assertIn("GitHub redirects the old web and git", text)
        self.assertIn("Do not create a new", text)

    def test_changelog_2_16_0_records_rename_without_claiming_completion(self):
        text = read("CHANGELOG.md")
        section = text.split("## [2.15.0]", 1)[0]
        self.assertIn("### Changed", section)
        self.assertIn("is moving from", section)
        self.assertIn("must not be reused", section)
        self.assertNotIn("has been renamed", section)
        self.assertNotIn("was renamed", section)


class LegacyLocalNamesUnchanged(unittest.TestCase):
    def test_installed_state_names_still_present(self):
        installer = read("bin/agentfleet") + read("bin/install.py") + read("bin/claude-agents-doctor")
        for name in (
            ".claude-agents-config-install.json",
            ".claude-agents-config-manifest.json",
            "claude-agents-config:managed",
            "claude-agents-config.bundle.v1",
            "claude-agents-config.installed.v1",
        ):
            self.assertIn(name, installer, name)
        self.assertIn(".claude-agents-config.lock", read("scripts/provider_catalog.py"))
        self.assertIn('CACHE_FORMAT = "claude-agents-config.provider-cache.v1"', read("scripts/provider_catalog.py"))
        self.assertIn("backups/claude-agents-config/", read("README.md"))

    def test_old_version_policy_fixture_present(self):
        self.assertTrue((ROOT / "tests/fixtures/agentfleet-2.15.0-policy.md").is_file())


if __name__ == "__main__":
    unittest.main()
