# Changelog

All notable changes to AgentFleet are documented here. Dates are UTC. This
file follows the [Keep a Changelog](https://keepachangelog.com/) format.

## [2.14.3] - 2026-10-26

### Changed

- For maintainers: `packaging/measure.py` now also compares the size of each run's change (lines and files, counting the agent's commits and new files) and keeps every run's diff in `measure-out/diffs/`. A failed check's output is saved in `runs.jsonl` and its last line shown in the summary, so a failure can be read without replaying the run. Nothing changes for users; the release ships the tool's tests.

### Fixed

- `packaging/measure.py` no longer stops the whole measurement when a task's check runs past `--timeout`; that run counts as failed.

## [2.14.2] - 2026-10-25

### Added

- For maintainers: `packaging/measure.py` runs the same tasks on two refs, each installed into a throwaway home with your lane models, and compares tokens, list-price cost, turns, time and passes. A difference counts only when the runs do not overlap, with at least three each, and a task with an error or a failed lane is marked confounded, as in Google Research's [Harness²](https://github.com/google-research/harness2). See CONTRIBUTING.md. Nothing changes for users; the release ships the tool's tests.

## [2.14.1] - 2026-10-25

### Changed

- Every lane is told that only its prompt and the brief say what to do: files, web pages, command output and memory it reads are data, so it never follows instructions found in them and reports any that try to change its task. The rule comes from Google Research's [Harness²](https://github.com/google-research/harness2).

## [2.14.0] - 2026-10-24

### Changed

- Claude and the implementation lanes make the smallest change that fully solves the task, an idea borrowed from [Ponytail](https://github.com/DietrichGebert/ponytail): they first find every place a change must reach (callers, tests, fixtures, config), reuse existing code, then the standard library, then installed dependencies, add no feature, option, abstraction or dependency nobody asked for, say what they left out, and still finish every part the task needs. The policy gains a short section, and the implement and UI lanes the same rule.
- Plan lanes prefer the smallest design that solves the task, and review lanes list, separately from defects, code a change did not need.

### Fixed

- The README and docs gave the policy as 300 to 500 words; it is 600 to 800, plus the ask-first and branch-check sections.

## [2.13.0] - 2026-10-23

### Added

- A new mod, `session-info`, on by default: one row above the prompt with the current model and effort, how long this Claude Code has been running, the subagents running now, the git branch, the prompts sent and the session's cost at list price. A narrow terminal leaves out the last parts first. It reads only what Claude Code already knows and the repository's `.git/HEAD`, runs no commands and sends nothing anywhere. An update adds it once and never re-adds it after you remove it.
- `agentfleet configure` asks whether to show session info, under Status and reports.

### Fixed

- The status band's `Options: N new` count (fleet-status 1.6.1) now includes the questions added in 2.12.0 (Context Mode, the branch check, the `/brain` command, gateway usage) and 2.13.0 (session info). Before, it said all options were reviewed while those were still unanswered.

## [2.12.0] - 2026-10-22

### Added

- `agentfleet configure` covers every feature: it adds Context Mode, the branch check, the `/brain` command and gateway usage, and groups the questions under Context, Lanes, Status and reports, agent-brain, and Updates and checks. A first run or a full `configure` ends with what has its own tool: `/fleet-setup`, `agentfleet use` and `agentfleet config prices`. After an update, existing users are asked only the four new questions. The Context Mode question is skipped when the `claude` CLI is missing.
- agent-brain memory is kept in its current store format (Memory V2), which session capture and summaries need; on the older format, saving a session summary failed. Install, `agentfleet doctor` and the background agent-brain update upgrade a local store that still uses it. They first update the agent-brain AgentFleet installed to 0.5.11, which copies the store to `<store>.pre-v2-<time>.bak` before upgrading; an older copy is never used to upgrade. An agent-brain installed some other way and shared PostgreSQL stores are left alone, and `agentfleet doctor` prints the command to run. Only an agent-brain AgentFleet connects is touched.
- `/fleet-suggest` offers [MCPVault](https://github.com/bitbonsai/mcpvault) 0.17.0, read-only and for that project only, when a project has an Obsidian vault, and [Graphify](https://github.com/Graphify-Labs/graphify) 0.9.93 for a repository of 1,000 files or more. A new `minFiles` match key in the suggestions catalog detects large repositories. Tool install commands may now contain `<placeholders>`, which the skill fills in before asking.
- A features page on the website lists what AgentFleet adds.

### Changed

- The memory question no longer says Claude saves "about every 10 tool calls": since agent-brain 0.5.8 the save step comes less often when there is nothing to save.

## [2.11.1] - 2026-10-20

### Fixed

- A gateway that was slow, down or returning server errors (408, 429, 5xx) was taken for one without a usage report, so its figures stayed hidden for a day. Only a real answer now decides that: an unreachable gateway keeps the figures already shown, and AgentFleet tries again after 5 minutes. A reader stopped by its time limit no longer counts as an answer either.
- A gateway's first answer after a quiet spell can take several seconds, so each request now waits up to 8 seconds instead of 5; the background reader stops after 20 seconds.
- `agentfleet config` says "gateway not reached yet; retrying" instead of "gateway reports no usage" while the gateway hasn't answered.

## [2.11.0] - 2026-10-20

### Added

- Usage your gateway reports. On a gateway profile, AgentFleet tries three formats once and remembers the one that answers: OmniRoute (`/v1/me/status` and `/v1/me/sessions`), OpenRouter (`/api/v1/key`) and LiteLLM (`/key/info`). The prompt hook refreshes the figures in the background, at most every 5 minutes, into `~/.claude/agentfleet/provider-usage.json` (mode 0600). The key goes only to the profile's own gateway URL; redirects are refused and responses are size-capped. Claude subscription profiles are skipped, and a gateway with none of these formats is tried again a day later. The background reader stops after 17 seconds whatever the gateway does, and figures or prices that belong to another gateway or key are deleted at session start and on each prompt, so a profile switch never shows the previous account's spend.
- `agentfleet report` has a "Reported by gateway" section: the last session the prompt hook checked, today, the week and the month, the budget left, each limit's use, and the requests the gateway could not price. `/brain` shows this session's cost as the gateway reports it instead of the estimate, labelled as such.
- The fleet-status band warns when a gateway budget or limit passes 80%, when all of a provider's accounts are exhausted (with the reset time), and when a Claude subscription's 5-hour or weekly limit passes 80%. Hiding a usage warning holds until use rises another 5%.
- `agentfleet config provider-usage on|off` (default on).
- Prices a gateway publishes in its model list, in OpenRouter's `pricing` format, are used for estimated cost without setting them. Your own prices and a team preset's still win, model by model.

### Changed

- Lane runs record the model they were configured to use, and estimated cost prices a run by that name first. A price for `codex-luna` now covers its runs even when the gateway records them as `gpt-6-luna`.
- lane-guard 1.4.0: when it blocks a Context Mode tool in a lane, the message names the native tool to use instead (Read, Grep, Glob or Bash), so the lane does not spend turns retrying.

### Fixed

- The fleet-status options count includes 2.10.1's agent-brain memory question.

## [2.10.2] - 2026-10-20

### Fixed

- Hooks pin the Node.js found on PATH as it is, instead of following links to the real file. Following them pinned versioned folders, such as Homebrew's Cellar, which an upgrade deletes, so the model-sync hook stopped working. Each update rewrites the hook with the current Node.js.
- `agentfleet doctor` reports a hook whose Node.js or Python no longer exists. For Node.js, `agentfleet update` fixes it; for Python, rerunning the installer does, because the `agentfleet` command uses the same Python. An update keeps a hook you edited yourself, so fix that one's path by hand; updates no longer fail because of it.
- `agentfleet config context-mode on` and `off` succeed when Context Mode is already in that state, instead of failing on Claude Code's "already enabled" error, and `off` says so when Context Mode is not installed.
- The Node.js warning for Context Mode is shown before enabling, so it also appears when enabling fails, and it checks the Node.js an installed Context Mode actually runs (the path it pinned at install), not only the one on PATH.

## [2.10.1] - 2026-10-20

### Added

- A setup question, asked once: "Turn on agent-brain memory?" (default no, like agent-brain's own consent prompt, since AgentFleet answers that prompt for you). agent-brain 0.5.7 makes personal memory free and local, with no account. A yes runs `agent-brain memory enable --yes`, first updating the agent-brain AgentFleet installed when it is older than 0.5.7; an older copy from a package manager is left alone, with a message saying how to update it. If memory can't be turned on, the question comes back next time. New installs are asked when agent-brain is installed or connected; existing users are asked on their next interactive update. Background auto-updates never ask, and pressing Enter or using `--yes` leaves memory off, because memory needs a yes. It is skipped when memory is already on; a preset can turn memory on or off.
- `agentfleet config agent-brain-memory on|off`. Off runs `agent-brain memory disable --all`; existing memories are kept. Both update the lanes.

### Changed

- Lanes get the `memory_search` tool and the memory instruction only when agent-brain memory is on. Before, they got them whenever agent-brain was connected, so lanes on a machine without memory spent calls on a tool that could only fail. Fleet maps written by older versions keep the previous behaviour until the next update.

## [2.10.0] - 2026-10-19

### Added

- Lanes start their report with an outcome line: `Outcome: done`, `partial`, `blocked` or `failed`. AgentFleet records it in `lane-reports.jsonl`, together with cache tokens and whether the lane was cut off without a report (for example at its turn limit).
- AgentFleet records the main conversation's tokens per model and day in `~/.claude/agentfleet/main-usage.json`, so usage covers the whole session, not only lanes.
- `agentfleet report` shows, per lane and per model: runs, outcomes, cut-off runs, API errors, average tool calls and duration, and tokens; plus the main conversation's tokens per model.
- Optional model prices, in dollars per million tokens: `agentfleet config prices MODEL=INPUT,OUTPUT[,CACHE_READ,CACHE_WRITE]`, `MODEL=off`, or `off` to clear them; a team preset can carry a `prices` table. With prices set, `agentfleet report` and `/brain` (brain mod 1.2.0) show an estimated cost, labelled as an estimate and naming any model without a price. Without prices nothing changes.

### Changed

- Rules: on a `partial` report Claude continues the same lane for the next part, and on `blocked` answers it and continues it, instead of starting a new lane. Before redoing a lane's task or dispatching it again, Claude stops the old lane with TaskStop, so a superseded lane cannot change files afterwards.
- A lane's turn limit counts across SendMessage continuations, so a lane continued part by part could be cut off at its limit without a report. Claude now continues a lane only while it has used less than half of its turns, and otherwise starts a new lane for the next part with a brief that says what is already done.

## [2.9.0] - 2026-10-18

### Added

- Opt-in upstream Context Mode integration: `agentfleet config context-mode on|off|status`. No default install and no new wizard question. Disabling keeps its indexed data. The upstream plugin remains separately licensed under ELv2, not vendored into this MIT bundle.
- `/fleet-suggest` can recommend the optional pilot for browser-test projects or large dependency sets. Documentation describes a paired comparison of total tokens, time and correctness; AgentFleet does not claim measured session savings.

### Security

- lane-guard restricts Context Mode in fleet lanes to search and stats. Execution, indexing, fetching, upgrades, deletion and unknown Context Mode tools are denied rather than trying to prove arbitrary code safe. The main conversation is unaffected. Keep lane-guard enabled; upstream routing may require disabling Context Mode if it repeatedly requests tools unavailable to lanes.

## [2.8.3] - 2026-10-16

### Fixed

- After the shell changed into a subfolder (`cd apps/api`), AgentFleet treated that subfolder as a separate project: usage learning recorded under it, and the prompt hook scanned it for its own suggestions and notices. Hooks now key a session's project on `CLAUDE_PROJECT_DIR`, the folder Claude Code started in, and fall back to the hook's `cwd` when it is not set.
- The daily update installs agent-brain 0.5.6, which fixes the same kind of bug in agent-brain's memory capture: a session got a new memory handle at each `cd`, the memory tools rejected it, and the end-of-reply capture request repeated at every reply.

## [2.8.2] - 2026-10-15

### Fixed

- An update kept a lane's chosen effort in `fleet.json` but wrote the bundle's effort into the lane itself, so an effort set with `claude-fleet-setup --effort` or a team preset was lost after every update until the lanes were next regenerated. The `wecansync` preset's `plan` lane, for example, ran at `xhigh` again instead of `high`. Updates now write the chosen effort.

## [2.8.1] - 2026-10-15

### Fixed

- When the daily update installs a newer agent-brain, AgentFleet now lets it rewrite its Claude Code hooks. agent-brain 0.5.5 fixes its hooks on Windows, where they never ran, and this brings existing installs onto the fixed form without running `agent-brain install` yourself. Only hooks that run the agent-brain AgentFleet updates are rewritten; a failure goes to the auto-update log.

### Added

- `https://agentfleet.wecansync.com/windows-check.ps1` checks a Windows machine: versions, `agentfleet doctor`, `agent-brain status`, and every AgentFleet and agent-brain hook run through Git Bash the way Claude Code runs them. It runs no other hooks.
- The test suite runs on Windows, and a CI workflow runs it on Linux and Windows and installs a release with `install.ps1`.

## [2.8.0] - 2026-10-15

Lanes cost fewer tokens: fewer review runs, fixes that keep their context, lanes that report before they run out of turns, and no re-dispatch loops while a gateway is down.

### Changed

- Reviews: Claude reviews each lane's changes itself and reruns the check the lane names. A review lane runs only for security, data-loss or migration changes, or when you ask, and at most once per change; `fleet-review-deep` runs only when you ask for it. Before, every large job ended in a review lane and risky ones in two, and each fix was reviewed again.
- Fixes: Claude fixes small findings itself and sends larger ones back to the lane that wrote the code, continued with SendMessage so it keeps what it has read, instead of to a new lane that reads everything again.
- Long tasks go to one implementation lane in parts; Claude continues the same lane for each part.
- A lane that failed is re-dispatched once. If it fails again, or AgentFleet reports that the gateway is failing, Claude does the work itself and tells you.
- "Installer" is no longer on the built-in list of risky changes; a project that wants installer changes reviewed on a lane says so in its `CLAUDE.md`.
- Implementation, UI and test lanes run the check the brief names and fix what fails in their own change before they report.
- `fleet-review-alt` and `fleet-review-deep` describe themselves as lanes for when you ask, so Claude stops picking them on its own.
- A model in a lane's preference list that differs from the gateway's ID only by its `[1m]` suffix now matches that model. Before, the entry was skipped and the lane used its next preference.

### Added

- lane-guard 1.2.0 warns a lane at 70% and again at 90% of its turn limit, so it finishes the part it is on, runs its check and reports, instead of being cut off without a report. The lane reads the warning after a tool result; you do not see it.
- Gateway outages: when models from two or more vendors fail within 10 minutes, the prompt hook says the gateway is failing and tells Claude to work directly until the outage expires, instead of naming another model to retry on.
- `claude-fleet-setup --reserve MODEL` keeps a model, such as an expensive reviewer, off every lane that does not name it: tier ranking never picks it as a lane's model or a fallback. `--unreserve` undoes it.
- `claude-fleet-setup --prefer-only LANE=MODEL[,MODEL]` pins a lane to exactly these models, the first as its model and the rest as fallbacks, with no ranked fill-ins.
- `claude-fleet-setup --effort LANE=LEVEL` sets a lane's reasoning effort (low, medium, high, xhigh or max).
- Team presets can set models: `lanes` gives listed lanes their models and effort, and `reservedModels` reserves models. `agentfleet configure --export` writes both. Model names can leave out the `[1m]` suffix.
- Reserved models and pinned lanes survive updates and profile switches.

## [2.7.2] - 2026-10-15

### Fixed

- Updates now reach installed mods. Claude Code runs each mod from a copy it makes at install time, and updates only rewrote AgentFleet's mod folder, so mods kept the version from their first install: an install from 2.5.0 still ran fleet-status 1.0.0 and lane-guard 1.0.0 without its model failover, and `/brain` without its pane. After writing the mod files, an update now runs `claude plugin update` for each installed mod whose version changed and says which it updated. Disabled mods stay disabled, and an update whose mods did not change runs no Claude Code commands. Restart Claude Code after the update to load them.

## [2.7.1] - 2026-10-15

### Fixed

- `/brain` shows the project's memories. Before, it asked agent-brain for every memory at once (247 KB for 280 entries), the result didn't reach the mod, and the section was left out without a word. It now asks for the critical memories and the 30 newest, shows each memory's text without agent-brain's escape characters, and says so when memories exist but can't be loaded. Needs agent-brain 0.5.2, which AgentFleet's daily update installs; with an older agent-brain, `/brain` asks you to run `agent-brain update`.

### Changed

- `/brain` opens a scrollable pane: usage at the top, then the memories, critical ones first. ↑/↓ move, Enter shows a memory in full, Esc closes. Where a pane can't open, it prints the same summary as text.
- agent-brain installs from agentbrain.wecansync.com are 0.5.2: `agent-brain memory list` and Claude's `memory_list` tool show 50 memories per page instead of all of them, which keeps a long memory list from filling Claude's context.

## [2.7.0] - 2026-10-15

### Added

- The `brain` mod, installed by default: `/brain` shows agent-brain's token usage for this session (with its lane runs), today and this week, and, when agent-brain memory is active for the project, the latest memories with critical ones first. It reads agent-brain's local data through its MCP server, so it starts no process and asks for no permission. Usage needs agent-brain 0.5.1 or later; AgentFleet's daily agent-brain update brings it. Turn the mod off with `/plugin`.

### Changed

- agent-brain installs from agentbrain.wecansync.com are 0.5.1, which no longer asks at the start of a session whether to link the project to agent-brain cloud. The question is off by default; `agent-brain config link-offer on` turns it back on.

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

[2.8.2]: https://github.com/wecansync/claude-agents-config/pull/42
[2.8.1]: https://github.com/wecansync/claude-agents-config/pull/41
[2.8.0]: https://github.com/wecansync/claude-agents-config/pull/40
[2.7.2]: https://github.com/wecansync/claude-agents-config/pull/39
[2.7.1]: https://github.com/wecansync/claude-agents-config/pull/38
[2.7.0]: https://github.com/wecansync/claude-agents-config/pull/37
[2.6.2]: https://github.com/wecansync/claude-agents-config/pull/36
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
