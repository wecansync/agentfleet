# AgentFleet (claude-agents-config)

AgentFleet installs model-routed `fleet-*` subagents, hooks, mods and a policy file into `~/.claude/`. The installer is `bin/install.py`; the user CLI is `bin/agentfleet`. Read `CONTRIBUTING.md` for the layout and the project rules; this file adds what is easy to get wrong.

## After editing any bundle file

Everything outside `site/`, `packaging/`, `dist/`, `.github/` and `.claude/` is a bundle file.

- Regenerate the manifest before running tests: `find . -name __pycache__ -not -path './.git/*' -prune -exec rm -rf {} + && python3 -B packaging/update-manifest.py`. The script refuses to run while `__pycache__` exists, and the installer rejects a stale manifest.
- Published releases are immutable, so a bundle change ships with a version bump: `VERSION`, a `CHANGELOG.md` entry, the version line in `README.md`, and the "Latest release" card in `site/index.html`. Move the previous card into "Version history" rather than deleting it.

## Tests

- Run Python tests one module per process, in parallel, inside a clean environment. The serial `unittest discover` is slow:
  `mkdir -p /tmp/af-gate-homes && ls tests/test_*.py | sed 's#tests/##;s#\.py$##' | xargs -P 8 -I{} sh -c 'H=$(mktemp -d -p /tmp/af-gate-homes); env -i PATH="$PATH" HOME="$H" LANG=C.UTF-8 PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.{} >/tmp/af-{}.log 2>&1 || echo "FAIL {}"'; rm -rf /tmp/af-gate-homes`
- The scratch homes go under one fixed folder that is removed by its literal path: Claude Code's safety check blocks `rm -rf "$H"` inside an `sh -c` script, because it cannot inspect the variable.
- Why the clean environment: a Claude Code session exports the user's provider URL and token. An install run without `--provider` detects them and copies them into the scratch home.
- Any test that can start the `claude` CLI must put a fake `claude` first on `PATH` and pass an explicit environment to the subprocess.
- Mods: `cd mods/<name> && claude plugin test && claude plugin validate`.
- A new interactive wizard question needs an answer added to the pty tests in `tests/test_provider_repair.py`, or the suite hangs.
- Kill stray test processes by PID, never with `pkill -f`: the pattern also matches the shell running it.

## Invariants the tests enforce

- The bundle scan rejects key-shaped strings and machine-local home or web-root paths in every bundle file, tests included. Build fake keys at runtime (`"sk-" + "1234567890" * 2`) and use neutral paths such as `/usr/local/bin`. Never add exclusions to the scan.
- `agents/fleet-*.md` must equal the generator's output, and `mods/lane-guard/tests/cases.ts` must mirror `cases.json`. Update both when you change their source.
- AgentFleet's policy is `config/agentfleet.md`, rendered to `~/.claude/rules/agentfleet.md`. The installer never writes the user's `~/.claude/CLAUDE.md`.

## Git workflow

- Branch from `main` and open a PR for each change. Never push to `main` directly.
- Use Conventional Commits (`feat(install): …`, `fix(suggest): …`).
- Building and deploying releases (`packaging/build-release.py`, `packaging/deploy.sh`) is for maintainers, and only when asked.
