# Contributing to AgentFleet

AgentFleet is maintained by WeCanSync and is MIT licensed. Contributions are
welcome. Read this guide before opening a PR.

- Public repo: https://github.com/wecansync/agentfleet
- Site and docs: https://agentfleet.wecansync.com / https://agentfleet.wecansync.com/docs/

---

## Requirements

- Python 3.10+
- Node.js 18+
- git

All runtime code uses only the Python standard library and Node.js built-ins.
Do not add third-party runtime dependencies.

---

## Set up for development

```bash
git clone https://github.com/wecansync/agentfleet.git
cd agentfleet
```

No build step is needed. The installer discovers its bundle from its own
location, so you can run scripts directly from the clone.

The public repository is moving from `wecansync/claude-agents-config` to
`wecansync/agentfleet`. After the move, GitHub redirects the old web and git
URLs, so existing clones keep working; `git remote set-url origin <new URL>`
points a clone at the new name. GitHub does not redirect reusable Actions
references, so none in this repository use the old name. Do not create a new
repository under the old name: it would take those URLs back from the
redirect.

---

## Layout

```
bin/          install.py (installer), agentfleet, claude-fleet-setup,
              claude-agents-doctor (CLIs)
scripts/      provider_catalog.py, fleet-reconcile.py, fleet_watch.py,
              fleet_suggest.py, generate-claude-agents.mjs, hook scripts
config/       delegate-fleet.json (fleet map), provider-policy.json,
              settings.template.json, suggestions.json,
              agentfleet.md (rendered to ~/.claude/rules/agentfleet.md)
mods/         Claude Code mods catalog (fleet-status, lane-guard,
              fast-jev-compaction, token-weather, session-info, brain)
agents/       generated lane templates
skills/       skills (/fleet-setup, /fleet-suggest)
tests/        test suite (tests/test_*.py)
packaging/    release tooling — never part of an installed bundle
site/         docs site — never part of an installed bundle
dist/         build output — never part of an installed bundle
.github/      CI config, when present — never part of an installed bundle
.claude/      local Claude Code state (settings, worktrees) — git-ignored, never bundled
install.*     one-command install wrappers (sh / ps1 / cmd)
uninstall.*   uninstall wrappers
update.*      update wrappers
VERSION       current version string
CLAUDE.md     instructions for Claude Code when working on this repo
```

---

## Running the tests

```bash
python3 -m unittest discover tests
```

All tests must pass. Some tests require Node.js or a git repository and skip
when those are absent — a skip is not a pass.

When adding a test for new behavior or a bug fix, confirm the test fails
without your change. The easiest way is to stash or temporarily revert the
change, run the test, and verify it fails before restoring.

---

## Measuring a change

Unit tests show that a policy or lane-prompt change renders; they cannot show
that it makes Claude cheaper or better. `packaging/measure.py` runs the same
tasks on two refs and compares them:

```bash
python3 packaging/measure.py --base v2.14.0 --candidate HEAD \
  --tasks tasks.json --runs 3 --model claude-opus-5.5
```

`tasks.json` lists tasks, each with a `name`, a git `repo`, a `prompt` and an
optional `check` shell command that decides pass or fail:

```json
{"tasks": [{"name": "fix-date-bug", "repo": "../fixtures/app",
  "prompt": "Fix the off-by-one in parse_date and add a test.",
  "check": "python3 -m unittest"}]}
```

- Each ref is installed into a throwaway home with your lane models and
  settings (`agentfleet configure --export`, or pass `--preset FILE`), with
  auto-update off.
- Each run uses a fresh clone with no remote, and alternates base and
  candidate.
- `--model` sets the main session's model. Without it, a throwaway home uses
  Claude Code's default, which may not be yours.
- The summary in `measure-out/summary.md` gives tokens, list-price cost, turns,
  time, passes, and the lines and files each run changed. A difference counts
  only when every run of one ref is below every run of the other, with at least
  three runs each. A task with an error or a failed lane is marked confounded.
  Both rules come from Google Research's
  [Harness²](https://github.com/google-research/harness2).
- The change size counts the agent's commits and new files since the clone's
  first commit, but not files the repo's `.gitignore` excludes, so give a
  fixture a `.gitignore` for build output such as `__pycache__/`. Each run's
  diff is kept in `measure-out/diffs/`.
- A failed check's output goes into `measure-out/runs.jsonl`, and its last line
  into the summary. Before counting a failure against a ref, read it: a fake in
  the check that differs from the real thing fails correct code. In our own
  measurement, a fake web response without headers failed two runs whose code
  worked against a real server.

It spends real tokens: a one-task, one-run comparison cost about $1.20 and
6 minutes; three vague tasks that went through lanes, three runs per ref,
cost about $13 and an hour. The agent runs with permission checks bypassed inside the clones,
so measure only tasks you would run yourself. It needs a gateway or
`ANTHROPIC_API_KEY`, since a throwaway home has no subscription login.

### Benchmark fixtures

`packaging/measure-fixtures/make-fixtures.py` builds four synthetic repositories
and a `tasks.json` for them: three that change code (`fix-off-by-one`,
`add-verbose-flag`, `rename-config-key`) and one read-only question
(`explain-parse-line`). Every repository and commit is invented, with the
reserved `example.invalid` identity; never add client code or data. Its tests
(`tests/test_measure_fixtures.py`) check that each write task fails on its seed
and passes with the reference fix, and that the read-only check passes on the
seed. They do not measure anything.

- **Baseline and candidate are commits.** `measure.py` installs each ref with
  `git archive`, so it cannot see uncommitted worktree changes. To measure a
  worktree, commit it first (or to a throwaway branch) and pass that commit as
  `--candidate`. The baseline here is `814185e`, the 2.15.0 release commit.
- **Paired repeats.** Runs alternate base and candidate for each task. Each side
  needs at least three runs, and a difference counts only when the two sets of
  runs do not overlap.
- **Outcome per task:** `candidate lower`, `candidate higher`, or
  `inconclusive`, and any task with an error or failed lane is confounded. With
  three runs on small synthetic tasks, `inconclusive` is an expected and
  acceptable result. Do not report a saving from an inconclusive or confounded
  task, and four synthetic tasks show nothing about the work in general.
- **The read-only task** only shows that nothing broke, so its "passed" says
  little about the change.

```bash
python3 packaging/measure-fixtures/make-fixtures.py /tmp/af-bench
python3 packaging/measure.py --base 814185e --candidate <commit> \
  --tasks /tmp/af-bench/tasks.json --runs 3 --model <main-model> --out /tmp/af-bench/out
```

A pilot runs one task with `--only fix-off-by-one` (six sessions). The full set
is four tasks, two refs, and three runs, which is 24 `claude -p` sessions, each
up to 15 minutes by default. Token cost is not estimated here: the figures above
come from earlier, different tasks and are only a rough guide.

---

## Bundle integrity

The installer refuses a bundle whose `manifest.json` and `checksums.sha256` do
not match the files on disk. After editing any bundle file (anything outside
`site/`, `packaging/`, `dist/`, `.github/`, and `.claude/`), regenerate the manifest:

```bash
python3 packaging/update-manifest.py
```

The test file is itself part of the bundle, so run the manifest update before
running tests after any file change.

---

## Rules (read these carefully)

These rules are enforced by the doctor, the installer, or the test suite. PRs
that break them will not be merged.

1. **Lane names describe a role, never a model or vendor.** Use
   `fleet-implement-fast`, not `fleet-implement-gemini-flash`. Nothing in the
   bundle may hard-code a provider's model names as defaults.

2. **Stay provider-agnostic.** Model choice comes from tier ranking against the
   live catalog, explicit user preferences, or tier labels. Do not assume any
   specific provider.

3. **Read-only lanes must never receive `Bash`, `Edit`, `Write`, or `Agent`
   tools.** The doctor enforces this and will flag violations.

4. **Do not ship personal preferences in `config/agentfleet.md` or
   `config/settings.template.json`.** Users keep their own instructions in
   `~/.claude/rules/`, which AgentFleet never touches.

5. **Never commit secrets or real tokens.** The installer scans the bundle for
   credential-shaped strings. Use obvious fixtures (e.g. `FAKE_TOKEN`,
   `TEST_KEY`) in tests.

6. **Installs must be safe: backup before every write, rollback-able,
   idempotent, and non-destructive.** The installer backs up all managed files
   before any write and never overwrites files it does not own. Changes to what
   the installer writes need a migration path for existing installs. See
   `LANE_RENAMES` and `RETIRED_LANES` in `bin/install.py` and
   `SHIPPED_1_0_PREFERRED` in `scripts/fleet-reconcile.py` for examples of
   how old state is carried forward or cleaned up.

7. **User-facing changes must update the docs.** Update `README.md` and the
   docs page `site/docs/index.html` (component markup lives in
   `packaging/site-components.md`). When changing `site/assets/site.css` or
   `site/assets/site.js`, bump the `?v=` query string wherever those files are
   referenced — Cloudflare caches assets aggressively.

---

## Versioning

The version is in `VERSION` and follows semantic versioning. Any change to a
bundle file requires a version bump in the PR because published releases are
immutable. A change under `mods/<name>/` also needs a bump of that mod's
`version` in `.claude-plugin/plugin.json`: updates refresh an installed mod
only when its version changed. Maintainers build and deploy releases;
contributors do not:

```bash
# For reference only — maintainers run these, not contributors:
python3 packaging/build-release.py --ref <merge-commit>
export AGENTFLEET_DEPLOY_HOST=user@server
sh packaging/deploy.sh --dry-run
sh packaging/deploy.sh
```

---

## Git workflow

- Fork or branch from `main`.
- Keep each PR to one focused change.
- Use Conventional Commits with an optional scope:
  - `feat(agentfleet): ...`
  - `fix(install): ...`
  - `feat(site): ...`
  - `chore: ...`
- Include a short summary and a test plan in the PR description.

---

## Security

Report vulnerabilities privately through GitHub's Security tab ("Report a
vulnerability"). Do not open a public issue for a security report.

---

## Roadmap

The next phase is support for agent CLIs beyond Claude Code — OpenAI Codex CLI,
OpenCode, Kilo Code, and Cline — with lanes rendered in each tool's own agent
or mode format while keeping the same provider-agnostic lane model.

No design decisions have been made yet. If you are interested in contributing
to this area, open an issue to discuss the design before writing any code.

---

## License

By contributing, you agree that your contributions are licensed under the
project's [MIT License](LICENSE).
