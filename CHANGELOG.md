# Changelog

All notable changes to AgentFleet are documented here. Dates are UTC. This
file follows the [Keep a Changelog](https://keepachangelog.com/) format.

## [2.6.2] - 2026-10-14

### Fixed

- An install or update over settings that already point at a gateway no longer stops with "autoCompactWindow and CLAUDE_CODE_AUTO_COMPACT_WINDOW differ". While AgentFleet manages auto-compact, a mismatched pair, an invalid value, or a pair deleted after an earlier install is set to AgentFleet's window, and the installer says what it changed.
- The installer replaces `model`, `advisorModel` and `ANTHROPIC_DEFAULT_*_MODEL`/`ANTHROPIC_SMALL_FAST_MODEL` values the gateway does not serve with models from its live list, and says which. Before, settings left over from another provider or an older model list were kept, so Claude Code asked for models the gateway does not have, and an active model missing from the model picker made the post-install check fail and roll the install back. Aliases (`opus`, `sonnet`, `haiku`, `default`), models the gateway serves, and models you added to the picker yourself stay: an alias you chose gets its own picker row, and a served model written without the picker's context suffix (`claude-opus-5` for `claude-opus-5[1m]`) uses the picker's ID. When the gateway does not answer, an active model the picker lacks is replaced with `default`.
- Gateway model discovery is on for every gateway install, including settings or shell variables that already name a gateway. Before, it was on only with `--provider gateway` or the wizard, so the startup hook never fetched the gateway's models. If you turn it off (`CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY=0`) after AgentFleet turned it on, updates keep it off.
- The installer saves the model list it fetched, and `claude-fleet-setup --audit` and `--fix-settings` (used by `/fleet-setup`) fetch it again when a gateway is configured. Before, they read only the list the startup hook saves, so on a new device they found no models and nothing to repair.
- At startup, model reconciliation also replaces an `advisorModel` the gateway does not serve (with the best model of the nearest tier the gateway has), and an active model that has no picker row.
- A gateway URL and token exported together in the shell are used to fetch the model list during install.

## [2.6.1] - 2026-10-14

### Removed

- The agent-brain memory question (`agentBrainMemory`). AgentFleet installs and connects the free agent-brain only, which records the tokens and cost of each session. It never asks you to sign in to agent-brain, create an account, or turn on its memory, and its messages and docs no longer mention them.

### Changed

- The agent-brain wizard questions and `agentfleet config agent-brain on` describe only what the free tool does.
- `fleet-status` 1.5.1 counts only the agent-brain install question as new in 2.6.

## [2.6.0] - 2026-10-14

### Added

- Branch check: a new `branchCheck` setting, on by default, adds a "Check the branch before changing files" section to the policy. In a git repository, before the first file change of a task (and before a lane that changes files), Claude runs `git status -sb` and `git fetch`, tells you the current branch, uncommitted changes and whether it is behind its remote, and asks whether to continue on this branch, switch, or create a new one from the up-to-date default branch, with its recommendation. It offers to pull when the branch is behind and waits for your answer before changing files. It asks once per task, skips the check when you already chose the branch or the task has its own worktree, and never pulls, switches or creates a branch without your answer; lanes never do these at all. Set it with `agentfleet config branch-check on|off`.
- agent-brain install: when agent-brain is missing, the configuration wizard offers to install it (default: yes; wizard question and `"agentBrainInstall"` preset key). AgentFleet downloads the release for your system from agentbrain.wecansync.com, checks it against the release manifest's size and SHA-256, and puts only the `agent-brain` executable in `~/.local/bin` (on Windows, `%LOCALAPPDATA%\Programs\agent-brain`). `agentfleet config agent-brain on` installs it the same way when it is missing.
- agent-brain memory: the wizard asks whether to turn memory on. agent-brain opens your browser to sign in (`agent-brain login`) and shows its own consent text (`agent-brain memory enable`). Memory needs an agent-brain Pro account; without one agent-brain says so and the wizard goes on. Presets never turn memory on.
- agent-brain updates: with automatic updates on, AgentFleet checks once a day for a newer agent-brain, installs it in the background, and says so at the next start. It only replaces the copy in agent-brain's standard folder, skips dev builds and copies installed elsewhere, and never moves to an older version. Failures go to `~/.claude/agentfleet/auto-update.log`.

### Changed

- agent-brain owns its Claude Code hooks. AgentFleet no longer writes them: `agentfleet config agent-brain on` runs `agent-brain install --assistant claude-code`, and `off` runs `agent-brain uninstall --assistant claude-code` (agent-brain stays installed and keeps its data). An update removes the hooks earlier releases wrote and, unless agent-brain is switched off, lets agent-brain write its own. Before, agent-brain's own `install` and AgentFleet's update replaced each other's copies.
- The implement, ui, tests and docs lanes are told never to pull, switch or create branches; the main conversation agrees the branch with you first.
- `agentfleet config` shows `branch-check`, and `agent-brain: …, not connected` when agent-brain is installed but its hooks are missing.
- The agent-brain wizard question now says what the hooks do without memory: they record the tokens and cost of each session.
- `fleet-status` 1.5.0 counts the two new wizard questions.

## [2.5.0] - 2026-10-13

### Added

- Same-lane model failover. When a lane's model fails with an API error 429 (quota), 5xx or 529 (overloaded), AgentFleet records that model as down (60 minutes after a 429, 15 minutes after other errors, or until a run on it succeeds). While it is down, the `lane-guard` mod starts new runs of that lane on the first of its fallback models that works, and marks them `(failover: <model>)`. The prompt hook tells Claude to re-dispatch the failed task to the same lane; it names another lane only when every model of the lane is down or `lane-guard` is off.
- The fleet-status band shows which model a lane failed over to, and `/fleet-status` lists the models that are down.
- Ask first: a new `askFirst` setting, on by default, adds a short "How to approach a task" section to the policy. Claude still does small, clear tasks straight away; for larger, unclear or risky ones it checks the project's `docs/`, README and CLAUDE.md first, investigates and asks what is still open with a recommended answer, flags risky or doubtful requests with a safer alternative, offers Spec Kit (installing it and writing the constitution only if you agree) or a plan followed by lanes and a review, asks whether several tasks should each get a git worktree, proposes structure, design pattern and database schema for a new project after asking about a boilerplate, and stops after two failed attempts at the same fix. It follows a "Workflow" section in the project's CLAUDE.md instead of asking. Set it with `agentfleet config ask-first on|off`, the wizard, or `"askFirst"` in a preset.
- `token-weather` is installed and on by default. Existing installs get it once on their next update; removing it is respected by later updates. Wizard question and `"tokenWeather"` preset key.

### Changed

- Balanced work style: the prompt hook no longer guesses task size from the prompt (length, list items or wording) and adds no routing suggestion. The policy tells Claude to decide whether to use lanes after reading the relevant code, to say in one line whether it will split a large job, and to hand remaining independent parts to lanes when work turns out bigger than it looked.
- In the balanced and direct work styles the routing log keeps no prompt text.
- A lane keeps up to three fallback models instead of two, so a lane pinned with `/fleet-setup` to four models keeps all four in order.
- Fewer tokens per lane run:
  - With agent-brain connected, lanes only search project memory. They no longer save memory or write a session summary, and ignore hook requests to do so; the main conversation records what they report.
  - Lanes are told to keep reports short: the outcome first, then one line per file, check or finding.
  - The policy asks for the lightest lane that fits and for briefs that point at files instead of pasting them.
  - When new models are approved automatically, the session-start notice lists only lanes left without a model, not every unused model.
- `lane-guard` 1.1.0, `fleet-status` 1.4.0 and `token-weather` 0.1.0-agentfleet.1.

### Fixed

- `token-weather` no longer replaces the bands other mods draw above the prompt (such as the fleet-status notice); it draws its forecast below them.
- `agentfleet config work-style` (and the new `ask-first`) no longer overwrite `~/.claude/rules/agentfleet.md` when you have edited it. They keep the file, say so, and exit 1; move your own rules to another file in `~/.claude/rules/`.

## [2.4.3] - 2026-10-13

### Fixed

- `/fleet-suggest` no longer lists the same learned skill twice. Commands that map to one skill name, such as `npm run deploy` and `make deploy`, give one suggestion, for the command you run most.
- `/fleet-suggest` no longer suggests skills for general-purpose tools such as `git`, `head`, `tail` or `python3`. Many of these were only counted because they ended a pipe (`… | tail -3`).
- Learning records `python3 -B -m pytest` as `python3 -m pytest`, so options before `-m` no longer hide the module. A module or script names its skill: `python3 -m pytest` suggests `pytest`, `node scripts/seed.js` suggests `seed`.

## [2.4.2] - 2026-10-13

### Added

- `/fleet-suggest` offers a "Git workflow" section for the project's `CLAUDE.md` when the repository has a GitHub remote: a branch and pull request per feature, the test command to run first, and no commits, pushes or deploys unless asked. Claude fills in the main branch and test command, shows the draft, and writes it only after you approve. It is not offered once the section exists.
- `/fleet-suggest` offers Spec Kit (spec-driven development) for new or small projects. It is installed only after you approve, with `uv tool install specify-cli` and `specify init --here --integration claude --integration-options="--no-model-invocation"`.
- A `CLAUDE.md` for working on this repository.

### Changed

- `agentfleet config` shows `agent-brain: not installed` when the `agent-brain` CLI is missing and no choice was saved, instead of `auto (default), not installed`.

## [2.4.1] - 2026-10-13

### Fixed

- `agentfleet instructions`, and the instruction check offered by
  `agentfleet configure`, no longer stop with a Python error
  (`AttributeError: 'NoneType' object has no attribute '__dict__'`) before
  checking anything.

## [2.4.0] - 2026-10-13

Claude works directly by default and uses lanes for large work, AgentFleet's
policy moves out of your `CLAUDE.md`, lanes use less effort, and a new check
finds stale or conflicting instructions.

### Added

- Work style: `agentfleet config work-style direct|balanced|fleet`, or the setup wizard. Restart Claude Code after changing it.
  - `balanced` (the default, for new and existing installs): Claude works directly. The prompt hook suggests a lane only for large prompts: 400 or more characters, three or more list items, broad wording such as "across the codebase", a branch or PR review, a security review, or codebase or web research.
  - `direct`: lanes only when you ask.
  - `fleet`: the 2.3 behavior, delegate by default.
- `agentfleet instructions [--project DIR] [--check]`, also offered by the setup wizard (`agentfleet configure`). Basic checks find lane names that no longer exist, a leftover old AgentFleet policy, duplicated lines, and a total size over 3,000 words. Then one locked-down Claude Code run (no tools, no hooks, no MCP servers) proposes line edits for contradictions, duplicates, and unclear rules. Each edit is applied only when you say yes, with a backup in `~/.claude/agentfleet/instruction-backups/`. The check sends the text of your instruction files to your model provider, as Claude Code already does each session. `--check` reports without editing. Background auto-updates never run it.
- `agentfleet config agent-brain on|off`, and a wizard question that appears only when `agent-brain` is installed. Off removes its memory step after every reply and lane run.
- The setup wizard asks about work style, agent-brain, and the instruction check. Team presets accept `workStyle` and `agentBrain`, never the instruction check. `agentfleet config` shows work-style and agent-brain.
- `fleet-status` 1.3.0 shows "Work style: …" in `/fleet-status` and counts the new options.

### Changed

- AgentFleet's rules moved from `~/.claude/CLAUDE.md` to `~/.claude/rules/agentfleet.md`, which Claude Code loads automatically. AgentFleet no longer touches your `~/.claude/CLAUDE.md`.
- On upgrade, if an earlier install replaced your own `CLAUDE.md`, the original is restored from the backup. An unedited old AgentFleet copy is removed. An edited one is kept with a note.
- The policy is shorter: about 350 words, down from about 1,400.
- Lanes use less effort. `implement`, `review`, `review-alt`, `security-review`, and `diagnose-static` are now `high`. `tests`, `triage-static`, `research-codebase`, and `research-web` are now `medium`. `plan` and the `-deep` lanes stay `xhigh`. Updates apply the new values automatically.
- Lanes now stop and report what is done and what remains, instead of running out of turns without a report.

### Fixed

- A first install over a personal `~/.claude/CLAUDE.md` no longer fails with "Conflicts requiring --force-owned".

## [2.3.1] - 2026-10-12

Windows fixes found by running AgentFleet end to end on a real Windows machine,
plus one fix that applies to every platform.

### Fixed

- Rerunning or updating a 2.3.0 install no longer turns learning off. Only
  installs older than 2.3.0 start with learning off, as 2.3.0 intended. A
  `learn` value you set yourself is never changed.
- Windows: `agentfleet update`, `rollback` and `uninstall` no longer fail with
  "The batch file cannot be found." when they replace or remove the
  `agentfleet.cmd` wrapper they were started from. Wrappers find their
  interpreter on PATH only, never in the current folder.
- Windows: wrappers pass non-ASCII paths such as `C:\Users\José` intact and
  restore the console's code page afterwards.
- Windows: a lock left by a crashed run no longer blocks the model catalog,
  the agent generator or suggestions, and a lock held by a running process is
  never taken over.
- Windows: rollback can restore over read-only files.
- Windows: `claude-fleet-setup` now creates agent files for new lanes after
  reconciling, and reports when it cannot start the agent generator.
- Windows: the "add this folder to your PATH" tip no longer appears when the
  folder is already on PATH with different letter case or a trailing
  backslash.
- Clones made with Git for Windows' default settings install again: every
  text file is now checked out with LF line endings, so checksums match.

## [2.3.0] - 2026-10-12

One command for installation and updates, a configuration wizard with shareable
team presets, one auto-compact percentage for every model, visible JEV results,
and suggestions that clear when reviewed.

### Added
- `agentfleet configure`: asks about auto-compact, JEV, reviewer access, learning, fleet-status, lane-guard, and automatic updates. Enter keeps the current value; `--yes` keeps values and marks unanswered questions reviewed. `--preset FILE` applies a team preset; `--export FILE` exports settings without secrets.
- Automatic configuration after a successful terminal install or update asks only unanswered questions. Skip with `AGENTFLEET_NO_CONFIGURE=1` or `--no-configure`. Background updates never prompt or apply presets; fleet-status says "New AgentFleet options are available: run agentfleet configure in a terminal."
- Team presets in `agentfleet.preset.v1` format, accepted through `AGENTFLEET_PRESET=team.json` by the install command, or by `AGENTFLEET_PRESET=team.json agentfleet update` (from 2.3.0, also `agentfleet update --preset team.json`). Feature keys are optional; keys containing "key", "token", or "secret" are rejected.
- JEV result band with before/after token estimates, reduction, verbatim messages kept, and elapsed time, or a fallback reason. `/jev` shows key availability, auto-compact percentage, session totals, and the last five compactions. Lifetime counters in `~/.claude/agentfleet/jev-stats.json` contain no conversation content or key.

### Changed
- The same download command now installs or updates. `agentfleet update` remains available and keeps the installed provider profile.
- Auto-compact uses Claude Code's `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` with a 1,000,000-token window clamped by Claude Code to each model. Default 70; range 20–95 or `off`. Restart once after changing the percentage. Gateway model switches need a restart only if their real-context cap changes. Existing 2.2.0 installs migrate automatically.
- Learning is on by default for new installs; existing installs keep their setting and are asked once. Local counters record command/tool names, documentation sites, and slash commands, never prompt text. Disable with `agentfleet config learn off`; erase with `agentfleet suggest --forget`.
- `fleet-status` 1.2.1 shows one unseen suggestion at a time, remembers Hide, and includes options and JEV lines in `/fleet-status`. The model-facing suggestion notice is skipped when the band is enabled.
- `fast-jev-compaction` 0.3.0-agentfleet.3; the wizard can accept a TypeSafe key with hidden input and store it as `TYPESAFE_API_KEY` in the settings environment, where commands Claude runs can read it. For private storage, leave it empty and use `/plugin`. Conversation text still goes to `api.typesafe.ai` when JEV compacts.
- `AGENTFLEET_NONINTERACTIVE=1` now skips only the provider questions. In a terminal the configuration wizard still asks about new options unless `AGENTFLEET_NO_CONFIGURE=1` is set; without a terminal nothing is asked.

### Fixed
- The suggestion band no longer stays after opening `/fleet-suggest`; installed items drop out on the next prompt.
- Compaction no longer needs recalculation or a restart merely to retain the percentage on model switches (gateway cap changes still require a restart).
- 30% and other low percentages now work on smaller models.
- `agentfleet jev status` detects keys in the settings environment as well as the shell; `agentfleet config` shows every feature, and the doctor checks that the percentage in settings matches the preference.
- A changed compact percent is now applied on gateway installs even before the model catalog is cached.
- A 2.2.0 gateway upgrade replaces the old scaled context limit with the model's real context.
- A `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` you set yourself is kept and becomes your preference.
- A hand-set native compaction window stays fully in your control.
- Rollback also resets the wizard's answered questions.
- `claude-fleet-setup --compact-percent` accepts 20–95.
- Background updates ignore leftover preset variables.
- `/fleet-status` shows JEV stats after a fallback.
- Suggestion dismissals can no longer be lost when two prompts race.

## [2.2.0] - 2026-10-11

Auto-compact percentages based on model context, reviewer command execution
and fix modes, lane-guard safety mod, outage failover, subagent reports,
project suggestions, and Windows hardening.

### Added
- Auto-compact percentage: `compactPercent` (default 70, range 40–95, or `off`) in `~/.claude/agentfleet/config.json`. The context watcher sets Claude Code's auto-compact window to that percentage of the active model's context length at startup and on model switch (native Claude login profiles use a 1,000,000-token basis). Manage via `agentfleet config compact <40-95|off>`, `claude-fleet-setup --compact-percent P`, or `/fleet-setup`. `claude-fleet-setup --compaction N` sets an absolute token override and turns the percentage off.
- `lane-guard` mod: installed and on by default, intercepting tool calls from subagent lanes (`fleet-*`) to block git history and tree mutations, GitHub writes, package publishing and deployments, database migrations, destructive SQL via database CLI tools or MCP servers, and dangerous `rm` targets. Read operations and the main conversation remain unblocked.
- Reviewers with execution and editing capability: `fleet-review`, `fleet-review-alt`, `fleet-review-deep`, and `fleet-security-review` have `Bash`, `Edit`, and `Write` tools; they run in CHECK mode by default (linters, test suites, builds, queries; no file edits) and enter FIX MODE only when requested by the brief with a specified file list. `fleet-diagnose-static` can run shell commands to reproduce issues, with no edit tools.
- `agentfleet config reviewer-access full|run|read-only` (default `full`): global reviewer access control, complemented by per-lane `tools` overrides in `fleet.json`.
- Outage failover: a lane whose model fails with a 5xx gateway error is marked failing for 15 minutes from the failure in `~/.claude/agentfleet/outages.json`. The main agent is told which lane to use instead, and requests routed to the failing lane go to that alternative while the outage lasts.
- Lane reports: records the files each fleet lane changed and any forbidden commands it ran (by AgentFleet's own rules, whether or not lane-guard blocked them) after each subagent finishes in `~/.claude/agentfleet/lane-reports.jsonl` (local, rotated at 256 KB). Inspect via `agentfleet report [--days N] [--json]`.
- Project capability suggestions: `/fleet-suggest` and `scripts/fleet_suggest.py` inspect the current repository locally against a 57-entry catalog (`config/suggestions.json`) covering plugins, MCP servers, optional mods, drafted hooks, and project skills. Mentions suggestions at most once per project per catalog version, never reads `.env` secrets, and installs only selected items. Manage via `agentfleet suggest [--project DIR] [--json] [--dismiss ID…] [--seen] [--forget]`.
- Opt-in usage learning: `agentfleet config learn on` (off by default) aggregates command names, tool calls, documentation domains, and slash commands per project without recording prompt text in `~/.claude/agentfleet/usage/`. `agentfleet suggest --forget` deletes recorded usage data.
- Per-project lane configuration: `agentfleet project pin LANE MODEL [--fallbacks A,B] [--dir DIR]`, `agentfleet project unpin LANE [--dir DIR]`, and `agentfleet project show [--dir DIR]` manage project overrides in `<project>/.claude/fleet.local.json` and project agents in `<project>/.claude/agents/`.
- Plugin and MCP health auditing: `claude-fleet-setup --audit`, `/fleet-setup`, and `agentfleet status` check enabled plugins, plugin errors, and failing or unauthenticated MCP servers (`claude-fleet-setup --audit --no-health` skips health checks).
- `/fleet-tasks` pane in `fleet-status` 1.1.0: closable UI showing active fleet subagents, recent lane reports, and active provider outages.

### Changed
- Existing installations migrate automatically from the former fixed 800,000-token window to 70% of context on update.
- `fleet-status` mod 1.1.0: displays the auto-compact percentage, reviewer access level, learning status, active outages, and project suggestions.
- `fast-jev-compaction` mod simplified: removed separate 60% trigger and `compactAtPercent` option; runs only on compactions initiated by Claude Code in the main conversation; reads TypeSafe API key only from `/plugin` or `TYPESAFE_API_KEY` (never `settings.json`); logs a single no-key notice per session; manageable via `agentfleet jev on|off|status`.
- Global delegation policy in `CLAUDE.md`: documents reviewer CHECK vs FIX modes, lane-guard policy, outage failover handling, lane report verification, and capability suggestions.

### Fixed
- `/fleet-status` no longer shows "Profile: default" and "Auto-update: unknown": AgentFleet now writes the active profile marker and records the auto-update setting at session start, and the mod shows "enabled (default)" when no setting was recorded.
- Windows hardening: `.cmd` shims prevent loading executables from the current directory via `NoDefaultCurrentDirectoryInExePath` and explicit `where.exe` paths; hook command generators reject install paths containing `$` or backticks; `.ps1` shims include a UTF-8 BOM for Windows PowerShell 5.1 compatibility.

## [2.1.0] - 2026-10-09

Claude Code mods: a status band when something needs action, with optional
context forecasts and Jev compaction in a local plugin catalog.

### Added
- A local plugin catalog named `agentfleet` at `~/.claude/agentfleet/mods/`. Mods need Claude Code 2.1.287 or later and show in the terminal and the Code tab of Claude Desktop, not in the VS Code extension's chat panel, `claude -p`, the Agent SDK, or Desktop WSL sessions. They run with your user permissions and are not sandboxed.
- `fleet-status`, installed and on by default, draws a band above the prompt only for an update during the session, a failed automatic update, lanes without an available model, or model changes waiting for review. It has a Hide button; `/fleet-status` prints the version, profile, auto-update state, model proposals, and notices. It reads only the install manifest, active profile marker, `auto-update.json`, and `fleet-model-proposal.json`, never settings or tokens. It sends nothing anywhere and adds nothing to Claude's context except the command reply. Turn it off with `claude plugin disable fleet-status@agentfleet`.
- Two optional mods, neither installed by default: Anthropic's `token-weather` (Apache-2.0) forecasts context-window fullness above the prompt; tamaratran's `fast-jev-compaction` (MIT) replaces the compaction summary with a request to TypeSafe's Jev model that chooses which old tool calls and results to drop or truncate, keeping the rest word for word. Install with `claude plugin install token-weather@agentfleet` or `claude plugin install fast-jev-compaction@agentfleet`.
- Jev compaction needs a paid TypeSafe API key through `TYPESAFE_API_KEY` or its `apiKey` option. At each compaction it sends conversation text and tool inputs to `api.typesafe.ai`, with tool results replaced by short notes. Its default threshold is 60%; set `compactAtPercent` to 80 to match AgentFleet. AgentFleet's copy never forces compaction without a configured key.
- `--no-mods` and `AGENTFLEET_MODS=0` opt out; the choice is remembered and `agentfleet update` honors the variable. `--mods` opts back in.

### Changed
- On install, when Claude Code 2.1.287 or later is on PATH, the installer runs `claude plugin marketplace add ~/.claude/agentfleet/mods` and `claude plugin install fleet-status@agentfleet` once. Otherwise it prints the commands to run by hand and retries on the next update.
- Updates, including automatic ones, rewrite mod files in place for the next Claude Code session without re-registering plugins, so disabled or uninstalled mods stay that way.
- Uninstall removes the `agentfleet` catalog and its plugins from Claude Code. Rolling back to a backup from before 2.1.0 does too.

## [2.0.6] - 2026-09-25

Windows fixes: the one-command install works on the PowerShell built into
Windows, and updates repair hooks an older install left behind.

### Fixed
- The Windows one-command install (`irm https://agentfleet.wecansync.com/install.ps1 | iex`) works on Windows PowerShell 5.1, the version built into Windows. It stopped with an error when the Microsoft Store `python3` placeholder was on PATH, and it always reported Node.js 18+ as missing because PowerShell 5.1 drops the quotes in the version check. This also fixes `agentfleet update` and automatic updates on Windows machines without PowerShell 7.
- The Windows installer finds a Python installed with `uv python install` even when it is not on PATH. It never picks a project's virtual environment, and uv settings in the folder you run it from are ignored.
- On Windows, `agentfleet`, `claude-agents-doctor`, and `claude-fleet-setup` run with the Python the installer used, as the hooks already did, instead of whatever `py` or `python` finds on PATH. They had failed with "Python was not found" on machines whose only Python is not on PATH.
- An update refreshes managed hooks whose commands differ from the recorded install only in quoting or comment style, instead of treating them as your edits and never updating them again.
- An update no longer leaves a hook pointing at a script it has just removed. An edited hook that still runs a retired script, such as `sync-omniroute-models.mjs`, is replaced with the current hook and the installer says so. Your previous `settings.json` is in the backup snapshot.

## [2.0.5] - 2026-09-24

Any number of provider profiles: add and remove gateways without
reinstalling, and give each profile its own settings.

### Added
- `agentfleet add NAME --gateway-url URL [--token-env VAR] [--use] [--force] [--allow-insecure-http]` adds a gateway profile: it lists the gateway's models first and saves nothing on a bad token or an empty catalog.
- `agentfleet remove NAME [--yes]` removes a saved profile.
- Each profile now keeps its own provider policy, model pins, exclusions, and tier labels, instead of sharing one policy across every profile.
- Gateway setups set `ANTHROPIC_API_KEY` to an empty value, so an API key exported in your shell is not sent instead of the gateway token. A key you set yourself in `settings.json` is kept, with a warning.
- Re-running the installer with a `--gateway-url` for another gateway uses the settings saved in an `agentfleet` profile for it, or a generic policy, instead of carrying over the previous provider's model families and pins.
- `/fleet-setup` can add and remove providers without handling tokens in chat.
- README and docs cover the new commands and a table of supported gateways (OpenRouter, LiteLLM, Vercel AI Gateway, OpenCode Zen).

### Fixed
- Gateway tokens are handled more safely: model-list requests no longer follow redirects to another endpoint with the token attached, the install journal stores token digests instead of plaintext, a dry run with a new URL never sends the live token to it, and `--token-env` errors never echo the token value.
- A stale active-profile marker can no longer cause one gateway's setup to be saved over another gateway's profile.
- The installer decides the gateway URL and token together, so it never pairs one provider's URL with another's token. Re-running the original install command keeps a gateway you switched to with `agentfleet`.
- Uninstall keeps the API-key mask for a gateway you switched to, restores a pre-install gateway exactly, and no longer leaves a token behind without its URL.
- `agentfleet update` (including automatic updates) now succeeds after `agentfleet use native`, keeps model discovery on, and keeps your excluded models.
- The model-sync hook is always wired up and defers instead of failing if the provider changes while it waits.
- Cached model catalogs are only reused for the same endpoint and token.
- Installs from a group-writable git checkout work.
- Default profile names no longer collide with reserved Windows device names.

## [2.0.4] - 2026-09-23

`/fleet-setup` now interviews you model by model, and updates can install
themselves automatically.

### Added
- `/fleet-setup` suggests a complete fleet from each model's description, tier, reasoning support, and context size, or walks you through it model by model: pick lanes, skip the model, or exclude it entirely.
- `claude-fleet-setup --exclude/--include MODEL` keeps a model out of every lane while leaving it available in the `/model` picker.
- Automatic updates: a new release with the same major version installs itself in the background at most once a day, without delaying startup; a new major version gets a one-time notice instead. Turn it off with `agentfleet auto-update off` or `AGENTFLEET_AUTO_UPDATE=0`; check status with `agentfleet auto-update status`.
- `CONTRIBUTING.md` with setup, project rules, tests, versioning, security reporting, and the roadmap.

### Fixed
- A local `.claude/` directory no longer breaks installs made from a git clone.

## [2.0.3] - 2026-09-23

A redesigned website with full documentation, plus a missing CLI command.

### Added
- Redesigned landing page: OS-tabbed install commands, a wizard preview, and sections on why AgentFleet exists, features, how it works, the 19 lanes, providers and profiles, safety and privacy, requirements, and FAQ. Light and dark themes; works without JavaScript.
- Full documentation site at `/docs/`: install, the first-run wizard, installer flags, lanes, tiers and ranking, tuning, custom lanes, profiles, the command reference, `/fleet-setup`, installed files, context compaction, privacy, safety, upgrading, uninstall and rollback, troubleshooting, FAQ, and development.
- `agentfleet rollback [--backup DIR]`, which the README already documented but which didn't exist yet.

### Fixed
- The README, landing page, and docs now correctly say that writable lanes are only instructed, not technically prevented, from committing, pushing, or deploying; only read-only lanes are tool-restricted, and no lane can spawn nested agents.

## [2.0.2] - 2026-09-23

### Fixed
- `agentfleet update` works again. It had failed for everyone on 2.0.0 and 2.0.1 because the download used the default Python user agent, which Cloudflare rejected before the request reached the server.
- An update now removes preference values (effort level, output style, TUI mode, push notifications) that AgentFleet stopped shipping in 2.0.1, but only if you never changed them yourself; a value you changed is kept either way.

## [2.0.1] - 2026-09-23

### Changed
- Updating from 1.0.0 no longer carries over the old shipped model pins as if you had chosen them yourself; lanes stay free to pick the best model unless you pinned one.
- `CLAUDE.md` and settings no longer include the maintainer's personal preferences (commit/PR footers, the agent-brain memory section, a `claude-delegate` pointer, and UI preferences such as effort level, output style, and push notifications). Put your own instructions in `~/.claude/rules/*.md` instead, which Claude Code loads every session and AgentFleet never touches.

### Fixed
- The read-only check (`agentfleet doctor`) now covers all 19 lanes, including `review-alt`, `review-deep`, and `research-web`, and flags any custom lane marked read-only.
- Several edge cases found in an independent review: the one-time pin cleanup runs only once, saved profiles record their AgentFleet version so old profiles don't restore old pins, permission defaults are correctly handed back on update, and a malformed lane entry no longer breaks the doctor check.

## [2.0.0] - 2026-09-23

The project becomes AgentFleet: any Anthropic-compatible provider, role-named
lanes, provider profiles, and a one-command install.

### Added
- Any provider: a Claude subscription, an Anthropic API key, or any gateway that serves `GET /v1/models`.
- Tier-based model assignment: every discovered model gets a tier (cheap, fast, balanced, or deep), and each lane is ranked against live models by tier, with automatic fallbacks.
- A first-run wizard that asks how Claude Code should reach its models, discovers the gateway's models, and lets you correct tiers. Updates never prompt.
- Profiles: `agentfleet use native|<name>` switches between a gateway and your Claude login without reinstalling; lane names stay the same, only the models behind them change.
- A one-command install (`curl | sh` on macOS/Linux, `irm | iex` on Windows) that verifies a SHA-256 checksum and rejects unsafe archive paths.
- A landing page and full site at `agentfleet.wecansync.com`, built from reproducible release archives.
- MIT license.

### Changed
- 19 role-named lanes (`fleet-plan`, `fleet-implement`, `fleet-review`, ...) replace the 30 lanes that used to be named after models. Lane names never contain a model name.
- Upgrading from 1.0.0 renames or retires old lanes, carries over your per-lane choices, and keeps any custom lanes you added through `/fleet-setup`.

## [1.0.0] - 2026-09-18

The portable Claude agents configuration, published as `claude-agents-config`,
before the AgentFleet name. It received many small, unversioned improvements
through 2026-09-22:

### Added
- Initial release: a portable Claude Code agents configuration installed entirely under `~/.claude` and `~/.local/bin`, with no root or admin access required.
- Provider-aware fleet repair: model discovery, advisory fallback models, and reconciliation so the fleet adapts automatically as the gateway's model list changes, including an announcement when a model the fleet was using disappears.
- A `/fleet-setup` skill and a clean startup banner for reviewing and adjusting lane-to-model assignments.
- Automatic context-window sync when switching models, with a restart notice after a model switch changes the context budget.

### Fixed
- Bidirectional model-context scaling: switching from a large-context model to a small one and back no longer traps the compaction window at the smaller size.
- A bytecode-guard preflight check, manifest/fleet-hash sync, and the gateway catalog's user agent, each of which could otherwise break installs or model discovery.
- Windows compatibility: UTF-8 output across scripts and binaries.

[2.6.2]: https://github.com/wecansync/claude-agents-config/pulls
[2.6.1]: https://github.com/wecansync/claude-agents-config/pull/35
[2.6.0]: https://github.com/wecansync/claude-agents-config/pull/34
[2.5.0]: https://github.com/wecansync/claude-agents-config/pull/33
[2.4.3]: https://github.com/wecansync/claude-agents-config/pull/32
[2.4.2]: https://github.com/wecansync/claude-agents-config/pull/31
[2.4.1]: https://github.com/wecansync/claude-agents-config/pull/30
[2.4.0]: https://github.com/wecansync/claude-agents-config/pull/29
[2.3.1]: https://github.com/wecansync/claude-agents-config/pull/27
[2.3.0]: https://github.com/wecansync/claude-agents-config/pull/25
[2.2.0]: https://github.com/wecansync/claude-agents-config/pull/24
[2.1.0]: https://github.com/wecansync/claude-agents-config/pull/23
[2.0.6]: https://github.com/wecansync/claude-agents-config/pull/22
[2.0.5]: https://github.com/wecansync/claude-agents-config/pull/20
[2.0.4]: https://github.com/wecansync/claude-agents-config/pull/19
[2.0.3]: https://github.com/wecansync/claude-agents-config/pull/18
[2.0.2]: https://github.com/wecansync/claude-agents-config/pull/17
[2.0.1]: https://github.com/wecansync/claude-agents-config/pull/16
[2.0.0]: https://github.com/wecansync/claude-agents-config/pull/15
