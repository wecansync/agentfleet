# AgentFleet

AgentFleet installs a fleet of native Claude Code subagents ("lanes") under
`~/.claude/agents/fleet-*.md` and maps each lane to whatever models your
provider offers: a Claude subscription, an Anthropic API key, or any
Anthropic-compatible gateway whose models are discovered from `GET /v1/models`.

It also installs a routing hook, a model-sync startup hook, a context-budget
hook, a privacy-safe status line, a short delegation policy in
`~/.claude/rules/agentfleet.md`, the `/fleet-setup` and `/fleet-suggest` skills,
and a local Claude Code mods catalog with `fleet-status`, `lane-guard`,
`token-weather` and `brain` on by default. Everything lives under your home directory; no root or admin access is
required. AgentFleet never edits your own `~/.claude/CLAUDE.md`.

By default Claude works on your task directly, uses a lane only for large
work, and agrees on the approach with you before large, unclear or risky
tasks. See [Work style](#work-style).

Version: **2.9.0** — MIT license. See [LICENSE](LICENSE). See [CHANGELOG.md](CHANGELOG.md) for release history.

> AgentFleet is an independent project by WeCanSync. It is not affiliated with
> or endorsed by Anthropic. Claude and Claude Code are trademarks of Anthropic.

---

## Requirements

- Claude Code
- Python 3.10+ (on Windows, one installed with `uv python install` is found even when it is not on PATH)
- Node.js 18+

---

## Optional Context Mode pilot

Context Mode reduces large tool outputs by indexing them locally and returning
selected excerpts. It is an upstream plugin, not part of AgentFleet's MIT bundle,
and is **off by default**. No new setup question is added.

```sh
agentfleet config context-mode on
agentfleet config context-mode status
agentfleet config context-mode off
```

Enabling installs the upstream `mksglu/context-mode` marketplace plugin. Restart
Claude Code and run `/context-mode:ctx-doctor`. Disabling keeps the plugin and its
indexed data, but turns it off. The command warns when Node.js 22.5+ is not detected; upstream also documents a separate
Bun setup. AgentFleet's normal Node.js minimum is unchanged.

Enabling is user-scoped. Project or organization settings can override that
state; `status` reports the user installation, not every session's effective policy.

Use it initially in the main conversation for large logs, documentation and
MCP-heavy work. With lane-guard enabled, lanes may use Context Mode's search and
stats tools only; execution, indexing, fetching, upgrades, deletion and unknown
Context Mode tools are blocked. Use native tools in lanes. Without lane-guard,
AgentFleet cannot enforce this boundary. Do not grant Context Mode execution
tools to a lane as a workaround.

Context Mode's routing hooks may ask lanes to use tools they cannot execute.
If this causes repeated blocked calls, disable Context Mode for that session;
this compatibility release does not promise full lane-level routing support.

The guard recognizes the official Context Mode server names, not arbitrary aliases.
Do not rename its MCP server or grant aliased execution tools to lanes. Search
can refresh previously indexed files and access other indexed projects; it is
retrieval-only with respect to code execution, not a strict read-only boundary.

Run a paired pilot before keeping it enabled: use equivalent tasks in separate
clean worktrees, first without and then with Context Mode, keeping the model,
lane assignments and compaction settings fixed. Disable JEV for the comparison.
Record total input/output and cache tokens (`/brain`), wall-clock time,
compactions, blocked calls, test results and correctness. Repeat in both orders
and include Windows plus a session that changes directory. Upstream's advertised
output-size reductions are not measured total-session token or cost savings.
No such savings have been established by AgentFleet yet.

The plugin adds execution tools and lifecycle hooks and stores indexed data
locally. Its execution processes can inherit CLI credentials; “sandbox” is not
an assurance of OS-level isolation. Upstream updates and ELv2 licensing remain
upstream-managed; AgentFleet does not vendor or relicense the plugin. See the
[upstream project](https://github.com/mksglu/context-mode) and
[license](https://github.com/mksglu/context-mode/blob/main/LICENSE).

## Install

**macOS / Linux:**

```bash
curl -fsSL https://agentfleet.wecansync.com/install.sh | sh
```

**Windows PowerShell:**

```powershell
irm https://agentfleet.wecansync.com/install.ps1 | iex
```

**One command installs and updates.** On an existing install, run the same
command again or use `agentfleet update`; both keep your installed provider
profile. After a successful install or update in a terminal, the configuration
wizard asks only questions you haven't answered before. Enter keeps the current
value. Skip it with `AGENTFLEET_NO_CONFIGURE=1` or `--no-configure`.

Apply a team preset on update with `AGENTFLEET_PRESET=team.json agentfleet update`
(see [For teams](#for-teams)). Skip the configuration wizard with
`AGENTFLEET_NO_CONFIGURE=1 agentfleet update`. Both variables work with the 2.2.0
CLI and 2.3.0. From 2.3.0, `agentfleet update --preset FILE`, `--yes`, and
`--no-configure` are also available.

The download script fetches `releases/latest.json`, downloads the pinned
release archive, verifies its SHA-256, extracts it to
`~/.local/share/agentfleet/releases/<version>` (Windows:
`%LOCALAPPDATA%\AgentFleet\releases\<version>`), then runs `bin/install.py`.

Pin a version with `AGENTFLEET_VERSION=2.0.0`. Override the host with
`AGENTFLEET_BASE_URL` (https only; http is accepted only for localhost).

From a clone, `./install.sh` (or `install.ps1` / `install.cmd`) does the same.

### First-run wizard

On a fresh interactive install the wizard asks "How should Claude Code reach
its models?":

1. **Claude subscription** (default) — uses Claude Code's own login; lanes map
   to `opus`, `sonnet`, `haiku`.
2. **Anthropic API key** — reads `ANTHROPIC_API_KEY` from the environment.
3. **Custom gateway** — asks for an https URL and the name of the environment
   variable that holds the token (default `AGENTFLEET_GATEWAY_TOKEN`); if
   unset, prompts with hidden input. It then discovers models, shows each
   model's tier, and offers to adjust tiers before installing.

Updates keep the installed profile; `--wizard` explicitly reopens the provider
wizard. The separate configuration wizard runs after terminal installs and
updates, covering work style, auto-compact, JEV, reviewer access, learning, the
status band, lane-guard, automatic updates, and [agent-brain](#agent-brain):
installing it when it is missing and keeping it connected. It then offers the [instruction check](#instruction-check). Run `agentfleet configure` to revisit
every question.

### Automatic updates

Each time Claude Code starts, a quick hook checks at most once a day whether a
newer AgentFleet release exists. A newer release with the same major version
(for example 2.0.3 → 2.1.0) is installed in the background, with the same
checksum verification and backup as a manual update, so new sessions pick it
up. The next session mentions it in one line. A new major version is never
installed automatically; you get a one-time notice to run `agentfleet update`.
The hook never delays startup, and it is skipped for installs made from a git
clone. Background updates never ask questions. If options need review, the
status band says "New AgentFleet options are available: run agentfleet configure
in a terminal."

```bash
agentfleet auto-update status   # enabled?, installed and latest versions, last result
agentfleet auto-update off      # stop automatic updates
agentfleet auto-update on       # turn them back on
```

`AGENTFLEET_AUTO_UPDATE=0` also turns them off (useful in CI). The state lives in
`~/.claude/agentfleet/auto-update.json` and each run's output in
`~/.claude/agentfleet/auto-update.log`.

### Non-interactive flags

```bash
# Gateway example (set the token first, outside shell history):
export MY_GATEWAY_TOKEN=…
curl -fsSL https://agentfleet.wecansync.com/install.sh | \
  sh -s -- --provider gateway \
            --gateway-url https://gateway.example.com \
            --gateway-token-env MY_GATEWAY_TOKEN
```

| Flag | Effect |
|---|---|
| `--provider native\|anthropic-api\|gateway` | Set the provider without the wizard. |
| `--gateway-url URL` | Gateway base URL (https required outside loopback). |
| `--gateway-token-env NAME` | Environment variable that holds the gateway token. |
| `--enable-model-discovery` | Turn startup model discovery back on after you turned it off. Every gateway install turns it on by default. |
| `--no-wizard` | Skip provider prompts and post-install configuration; keep or infer the provider. |
| `--no-configure` | Skip the post-install configuration wizard (`AGENTFLEET_NO_CONFIGURE=1` also skips it). |
| `--wizard` | Force the wizard even on an existing install. |
| `--mods` | Opt back in to mods. The choice is remembered. |
| `--no-mods` | Opt out of mods. The choice is remembered. |
| `--dry-run` | Show changes without writing anything. |
| `--check` | Run the bundle doctor without changing files. |
| `--uninstall --apply` | Remove setup-owned files. |
| `--rollback --apply [--backup DIR]` | Restore the newest (or a named) backup. |
| `--home`, `--config-home`, `--prefix` | Override path roots. |

`AGENTFLEET_NONINTERACTIVE=1` skips the provider questions. In a terminal, the
configuration wizard still asks about new options, so add
`AGENTFLEET_NO_CONFIGURE=1` to skip it too. Without a terminal, nothing is asked.

`AGENTFLEET_MODS=0` also opts out of mods; `agentfleet update` honors the
variable. See [Mods](#mods) for requirements and optional plugins.

To check a Windows machine, download and run the check script in PowerShell.
It prints versions, `agentfleet doctor`, `agent-brain status`, and the result of
each AgentFleet and agent-brain hook run through Git Bash, as Claude Code runs
them; it runs no other hooks.

```powershell
irm https://agentfleet.wecansync.com/windows-check.ps1 -OutFile windows-check.ps1
powershell -ExecutionPolicy Bypass -File .\windows-check.ps1
```

---

## For teams

A team lead runs `agentfleet configure`, then exports the chosen settings:

```bash
agentfleet configure --export team.json
```

Share `team.json`, never an API key. Teammates save the file locally and use the
same command for installation or updates with the preset:

**macOS / Linux:**

```bash
curl -fsSL https://agentfleet.wecansync.com/install.sh | AGENTFLEET_PRESET=team.json sh
```

**Windows PowerShell:**

```powershell
$env:AGENTFLEET_PRESET="team.json"; irm https://agentfleet.wecansync.com/install.ps1 | iex; Remove-Item Env:AGENTFLEET_PRESET
```

A preset path is relative to the current directory.

Already installed? Run `AGENTFLEET_PRESET=team.json agentfleet update` on macOS/Linux, or
on Windows PowerShell:

```powershell
$env:AGENTFLEET_PRESET="team.json"; agentfleet update; Remove-Item Env:AGENTFLEET_PRESET
```

From 2.3.0, `agentfleet configure --preset team.json` also applies a preset. A preset does not supply credentials.
From 2.4.0, a preset can also set `workStyle` and `agentBrain`. It can never run the instruction check.
The wizard can save your TypeSafe key with hidden input into `~/.claude/settings.json`
(env `TYPESAFE_API_KEY`), where commands Claude runs can read it. For private storage,
leave it empty and use `/plugin` → fast-jev-compaction → Configure. Never paste keys in chat.

Verify with `agentfleet config`, `/fleet-status`, and `/jev` (when JEV is enabled).
Roll back an install or update with `agentfleet rollback`.

---

## Work style

The work style decides how readily Claude hands work to a lane. Set it with
`agentfleet config work-style direct|balanced|fleet` or in the configuration
wizard, then restart Claude Code. The default is `balanced`, for new and
existing installs.

| Style | Behavior |
|---|---|
| `balanced` (default) | Claude works directly. It decides whether to use lanes after reading the relevant code, not from how the request is worded, and says in one line whether it will split the work. It uses lanes for independent parts that change different files, long well-specified tasks, broad sweeps whose detail it won't need, and independent reviews of risky changes (security, data loss, migrations). The prompt hook adds no routing suggestion. |
| `direct` | Claude uses a lane only when you ask for one. |
| `fleet` | The 2.3 behavior: Claude delegates by default. |

```bash
agentfleet config work-style balanced
```

AgentFleet writes the policy for your chosen style to
`~/.claude/rules/agentfleet.md`, which Claude Code loads automatically.

### Reviews, fixes and retries

From 2.8.0, in every style that uses lanes:

- Claude reviews each lane's changes itself and reruns the check the lane names.
  A review lane runs only for security, data-loss or migration changes, or when
  you ask, and at most once per change. `fleet-review-deep` runs only when you
  ask for it.
- Claude fixes small findings itself. Larger ones go back to the lane that wrote
  the code, continued with SendMessage so it keeps what it has read, instead of
  to a new lane that reads everything again.
- A long, well-specified task goes to one implementation lane in parts; Claude
  continues the same lane for each part.
- A lane that failed is re-dispatched once. If it fails again, or AgentFleet
  reports that the gateway is failing, Claude does the work itself and tells you.

A project can add its own risky areas in its `CLAUDE.md`, for example "installer
changes count as risky: have them reviewed on a lane".

### Ask first

With `ask-first` on (the default), the policy adds a short "How to approach a
task" section. Claude still does small, clear tasks straight away. For larger,
unclear or risky ones it:

- checks the project's `docs/`, README and CLAUDE.md before changing an area or
  answering how something works, and searches the web only for outside
  libraries and services;
- investigates first, then asks what is still open in one message, with its
  recommended answer to each question;
- explains what worries it about a risky or doubtful request and recommends a
  safer way;
- for a large task, offers Spec Kit (and offers to install it and write the
  project's constitution if missing) or a plan to approve followed by lanes and
  a review, and says which it recommends;
- for several tasks, asks whether to give each its own git worktree or do them
  in turn in the current checkout;
- for a new project, asks about a base project or boilerplate and proposes the
  structure, design pattern and, for a backend, the database schema first;
- stops and reports after two failed attempts at the same fix.

It follows a "Workflow" section in the project's CLAUDE.md instead of asking,
and offers to record the workflow you pick there. Turn the section off with
`agentfleet config ask-first off`.

### Branch check

It is easy to finish a task, forget to switch branches, and start the next one
on the old branch. With `branch-check` on (the default), before Claude changes
files in a git repository it:

- runs `git status -sb` and `git fetch` to see the current branch, uncommitted
  changes, and whether the branch is behind or ahead of its remote;
- tells you what it found and asks whether to continue on this branch, switch
  to another, or create a new one from the up-to-date default branch, with its
  recommendation (a new branch when the current one holds finished or merged
  work), and offers to pull first when the branch is behind;
- waits for your answer before changing files.

It asks once per task, not before every edit, and skips the check when you have
already named the branch or the task runs in its own worktree. It never pulls,
switches or creates a branch without your answer, and lanes never do these at
all. Turn it off with `agentfleet config branch-check off`.

### Lane effort and stopping

Lanes use less reasoning effort than in 2.3. `implement`, `review`,
`review-alt`, `security-review`, and `diagnose-static` run at `high`. `tests`,
`triage-static`, `research-codebase`, and `research-web` run at `medium`. `plan`
and the `-deep` lanes stay at `xhigh`. An update applies the new values
automatically. A lane that is running out of turns now stops and reports what
is done and what remains, instead of ending without a report. From 2.8.0 the
lane-guard mod also warns a lane at 70% and again at 90% of its turn limit, so it
finishes the part it is on and reports before it is cut off. Set a lane's effort
yourself with `claude-fleet-setup --effort LANE=LEVEL` or a team preset.

---

## The 19 lanes

### Task lanes (12)

| Lane | Role | Writable |
|---|---|---|
| `fleet-plan` | Planning and architecture | No |
| `fleet-implement` | General implementation | Yes |
| `fleet-review` | Code review (runs checks; edits in FIX MODE) | In FIX MODE |
| `fleet-tests` | Test writing | Yes |
| `fleet-ui` | UI/frontend work | Yes |
| `fleet-docs` | Documentation | Yes |
| `fleet-security-review` | Security review (runs checks; edits in FIX MODE) | In FIX MODE |
| `fleet-diagnose-static` | Diagnostic command execution (no edits) | No |
| `fleet-explore-narrow` | Bounded exploration | No |
| `fleet-triage-static` | Issue triage | No |
| `fleet-research-codebase` | Repository research (no web tools) | No |
| `fleet-research-web` | Web research (`WebSearch`, `WebFetch`) | No |

### Variant lanes (7)

Variants are named for a capability, never a model, so names survive provider
changes.

| Lane | Capability |
|---|---|
| `fleet-plan-alt` | Alternate planning model |
| `fleet-implement-alt` | Alternate implementation model |
| `fleet-implement-fast` | Low-latency implementation |
| `fleet-implement-deep` | Reasoning-capable implementation |
| `fleet-implement-cheap` | Budget or free-tier implementation |
| `fleet-review-alt` | Independent review model (runs checks; edits in FIX MODE) |
| `fleet-review-deep` | Deep review (runs checks; edits in FIX MODE) |

Read-only lanes (`plan`, `explore-narrow`, `triage-static`, `research-*`) receive
no `Bash`, `Edit`, or `Write` tools. `fleet-diagnose-static` receives `Bash` to
reproduce failures from the command line, but no edit tools. Reviewer lanes
(`review`, `review-alt`, `review-deep`, `security-review`) have `Bash`, `Edit`,
and `Write` tools: they run in CHECK mode by default (executing test suites,
linters, builds, and read-only queries; never editing files) and enter FIX MODE
only when the brief explicitly requests fixes and lists target files. Writable
lanes implement assigned changes. All subagents are guarded by the `lane-guard`
mod, which deterministically intercepts tool calls and blocks git history mutations,
GitHub writes, deployments, migrations, and destructive SQL. All lanes are
background agents with `omitClaudeMd: true`, and none has the `Agent` tool, so none
can spawn nested agents. The main agent owns integration, final gates, and all
external actions.

---

## How lanes get models

Every discovered model gets a **capability tier**: `cheap`, `fast`, `balanced`,
or `deep`. The tier comes from, in priority order:

1. An explicit label set by the user.
2. Keywords in the model ID (e.g. `haiku`/`flash`/`mini` → fast;
   `opus`/`pro`/`max` → deep; `free`/`auto` → cheap).
3. Keywords in the catalog description.
4. `balanced` as the default.

Catalogs report context size but not price or speed, which is why explicit
labels exist.

Each lane has a required tier. At startup the reconcile step ranks all live
models per lane:

- Tier fit first; then reasoning support and 1M-context models rank higher.
- Staying on the current model is preferred, so a settled fleet does not
  reshuffle on every startup.
- Equally good models are spread across lanes; an `-alt` lane never shares its
  sibling's model.

The next-best models become each lane's fallbacks, so every live model can
serve somewhere regardless of lane count. A lane with an explicit `preferred`
list respects the user's order; its remaining fallback slots are still filled
by tier ranking, so a lane pinned to one model keeps somewhere to go. Models
you exclude never serve any lane (they stay in Claude Code's `/model` picker).

With a Claude subscription, lanes use `opus` / `sonnet` / `haiku` aliases.

---

## Tuning

```bash
# Pin a lane to a specific model (or list of fallbacks):
claude-fleet-setup --prefer fleet-implement=my-model-id
# Clear a pin:
claude-fleet-setup --prefer fleet-implement=

# Label a model's tier:
claude-fleet-setup --tier my-model-id=fast
# Clear a label:
claude-fleet-setup --tier my-model-id=

# Keep a model out of every lane, and undo it:
claude-fleet-setup --exclude my-model-id
claude-fleet-setup --include my-model-id

# Use only these models for a lane, first as its model, the rest as fallbacks
# (no ranked fill-ins); 'LANE=' clears it:
claude-fleet-setup --prefer-only fleet-implement=codex-luna,claude-sonnet-5.5
# Reserve an expensive model: it serves only lanes that name it:
claude-fleet-setup --reserve codex-astra
claude-fleet-setup --unreserve codex-astra
# Set a lane's reasoning effort (low, medium, high, xhigh, max); 'LANE=' clears it:
claude-fleet-setup --effort fleet-plan=high

# Set auto-compact percentage (20-95 or 'off'):
claude-fleet-setup --compact-percent 70
# Override with an absolute compaction window (turns compact-percent to 'off'):
claude-fleet-setup --compaction 250000

# Re-rank now:
claude-fleet-setup --reconcile
```

`/fleet-setup` inside Claude Code runs an interactive wizard over all of this:
stale-setting repair, profile switching, tier labels, and agent and skill
curation. Its **Assign models to lanes** option suggests a complete fleet from
each model's description, tier, and context size. You can apply it as is, or go
model by model: for each model it proposes the best-fitting lanes, and you pick
lanes, skip the model (auto-ranking decides), or exclude it. It shows the final
fleet for approval, or you can start over, and it pins only the lanes whose choice
differs from auto-ranking.

---

## Preferences and configuration

Run `agentfleet configure` in a terminal to ask every question: work style,
ask-first, auto-compact percentage, JEV, reviewer access, learning, fleet-status, lane-guard, token-weather,
automatic updates, and [agent-brain](#agent-brain) (install it when it is
missing, connect it when it is installed). It ends by
offering the [instruction check](#instruction-check).
Enter keeps the current value. When enabling JEV without an available
key, the wizard offers hidden TypeSafe API key input and can store it as
`TYPESAFE_API_KEY` in `~/.claude/settings.json`'s `env` (mode 0600), where
commands Claude runs can read it. For private storage, leave the key empty and
use `/plugin` → fast-jev-compaction → Configure.

```bash
agentfleet configure --yes              # keep current values; mark questions answered
agentfleet configure --preset team.json # apply a team preset without prompts
agentfleet configure --export team.json # export settings, never secrets
```

Presets use `agentfleet.preset.v1`; feature keys are optional. Unknown fields,
invalid values, and any other key containing "key", "token", or "secret" are rejected.
Example `team.json`:

```json
{"format":"agentfleet.preset.v1","workStyle":"balanced","compact":70,"jev":true,"reviewerAccess":"full","learn":true,"statusBand":true,"laneGuard":true,"tokenWeather":true,"autoUpdate":true,"agentBrainInstall":true,"agentBrain":true,"askFirst":true}
```

`workStyle` and `agentBrain` are accepted from 2.4.0, `askFirst` and
`tokenWeather` from 2.5.0, and `agentBrainInstall` from 2.6.0. A preset never
runs the instruction check.

From 2.8.0 a preset can also set the team's models. `lanes` gives each listed
lane its models (the first is its model, the rest its fallbacks, nothing else)
and optionally its effort; `reservedModels` keeps a model off every lane that
does not list it. Model names can leave out the `[1m]` suffix; AgentFleet matches
them to the gateway's IDs. Lanes the preset leaves out keep their own models.

```json
{"format":"agentfleet.preset.v1","reservedModels":["codex-astra"],"lanes":{"implement":{"models":["codex-luna","claude-sonnet-5.5"],"effort":"high"},"review":{"models":["claude-opus-5.5","codex-sol"]},"review-deep":{"models":["codex-astra","claude-opus-5.5"],"effort":"xhigh"}}}
```

`agentfleet configure --export` writes these keys too, for lanes pinned with
`--prefer-only` or given an effort.

`agentfleet config` shows every feature, including work-style and agent-brain.
The shared preferences live in `~/.claude/agentfleet/config.json`:

```bash
agentfleet config                                    # view effective preferences
agentfleet config work-style direct|balanced|fleet   # how readily Claude uses lanes (default: balanced)
agentfleet config ask-first on|off                   # agree on the approach before large or unclear tasks (default: on)
agentfleet config branch-check on|off                # check the git branch and ask before changing files (default: on)
agentfleet config compact <20-95|off>                # set auto-compact percentage (default: 70)
agentfleet config reviewer-access full|run|read-only # set reviewer tool access (default: full)
agentfleet config learn on|off                       # command usage learning (new installs: on)
agentfleet config agent-brain on|off                 # install and connect agent-brain, or disconnect it
```

Restart Claude Code after changing the work style, ask-first or branch-check.

### agent-brain

[agent-brain](https://agentbrain.wecansync.com) is a free tool that records
the tokens and cost of each Claude Code session. AgentFleet installs and
connects the free tool only; it never asks you to sign in or create an
agent-brain account.

- **Install:** when agent-brain is missing, the wizard offers to install it
  (default: yes). AgentFleet downloads the release for your system from
  agentbrain.wecansync.com, checks it against the release manifest's size and
  SHA-256, and puts the `agent-brain` executable in `~/.local/bin` (on Windows,
  `%LOCALAPPDATA%\Programs\agent-brain`). `agentfleet config agent-brain on`
  does the same.
- **Connect:** agent-brain adds its own hooks to Claude Code with
  `agent-brain install --assistant claude-code`, using its recommended
  settings. `agentfleet config agent-brain off` runs `agent-brain uninstall
  --assistant claude-code`; agent-brain stays installed and keeps its data.
- **Updates:** with automatic updates on, AgentFleet checks once a day for a
  newer agent-brain and installs it in the background, then says so at the next
  start. It only replaces the copy in agent-brain's standard folder, never a dev
  build or one a package manager installed, and never moves to an older
  version.

Before 2.6.0 AgentFleet wrote agent-brain's hooks itself; an update removes
those copies and lets agent-brain write its own.

### Instruction check

Claude Code reads your instruction files every session, and contradictions and
stale rules cost tokens and cause odd behavior. `agentfleet instructions`
checks `~/.claude/CLAUDE.md`, `~/.claude/rules/`, and a project's instruction
files:

```bash
agentfleet instructions                 # check, then offer edits
agentfleet instructions --project DIR   # include a project's instruction files
agentfleet instructions --check         # report only; edit nothing
```

The check has two parts:

1. Basic checks: lane names that no longer exist, a leftover old AgentFleet
   policy, duplicated lines, and a total size over 3,000 words.
2. One locked-down Claude Code run (no tools, no hooks, no MCP servers) that
   proposes line edits for contradictions, duplicates, and unclear rules.

Each edit is applied only when you say yes, and the file is backed up first in
`~/.claude/agentfleet/instruction-backups/`. The check sends the text of your
instruction files to your model provider, as Claude Code already does every
session. `--check` reports without editing. The configuration wizard offers the
check, and background auto-updates never run it.

### Reviewer modes

Reviewer lanes (`fleet-review`, `fleet-review-alt`, `fleet-review-deep`, `fleet-security-review`) run in **CHECK mode** by default: they have `Bash`, `Edit`, and `Write` tools to execute test suites, linters, builds, and read-only checks, but do not edit files.

To allow a reviewer to apply corrections, the brief must explicitly state **FIX MODE** and list the target files it may modify. A reviewer in FIX MODE requires disjoint file scopes from any other active writable lane. A lane must never review its own fixes; the main session reviews them. `fleet-diagnose-static` receives `Bash` to reproduce failures, but never has edit tools.

Global reviewer access is configured via `agentfleet config reviewer-access`:
- `full` (default): reviewers receive `Bash`, `Edit`, and `Write` (CHECK mode by default, edits in FIX MODE).
- `run`: reviewers receive `Bash` for test and diagnostic execution, but no editing tools.
- `read-only`: reviewers receive no `Bash`, `Edit`, or `Write` tools.

Per-lane `tools` arrays in `fleet.json` can also override specific tools per lane.

### Project-level lane configuration

Pin models for a specific project without affecting global settings:

```bash
agentfleet project pin LANE MODEL [--fallbacks A,B] [--dir DIR]
agentfleet project unpin LANE [--dir DIR]
agentfleet project show [--dir DIR]
```

Project overrides write to `<project>/.claude/fleet.local.json` and generate project agents in `<project>/.claude/agents/`, which Claude Code prioritizes over global fleet agents of the same name.

---

## Switching providers (profiles)

Profiles let you switch providers without reinstalling, and you can keep as
many as you like: one per gateway, plus `native` for your Claude login. Lanes
keep their names; only the models behind them change.

```bash
agentfleet profiles                  # list saved profiles (* = active)
agentfleet use native                # switch to your Claude login
agentfleet use <name>                # switch to a saved gateway profile
agentfleet save <name>               # name the current setup
agentfleet add <name> --gateway-url URL [--token-env VAR] [--use]
agentfleet remove <name> [--yes]     # delete a saved profile and its token
```

Switching first saves the current setup, so switching back restores it exactly.
Restart Claude Code after switching.

Each profile keeps its own:

- **provider policy** (`~/.config/delegate-skills/provider-policy.json`: the
  model families it approves, and the gateway's naming conventions),
- **lane pins**, **excluded models**, and **tier labels**.

A pin or exclusion you set on one provider does not follow you to another,
whose models have different names.

Gateway tokens are stored at `~/.claude/agentfleet/secrets/<name>.token`
(mode 0600, directory mode 0700). Profiles are stored under
`~/.claude/agentfleet/profiles/`.

To switch to `native`, AgentFleet removes `ANTHROPIC_BASE_URL`,
`ANTHROPIC_AUTH_TOKEN`, and the `ANTHROPIC_DEFAULT_*_MODEL` overrides, then
maps lanes to the `opus`/`sonnet`/`haiku` aliases. Gateway profiles set
`ANTHROPIC_API_KEY` to an empty string in `settings.json`, because Claude Code
would otherwise send an API key exported in your shell instead of the gateway
token. A non-empty key already in `settings.json` is yours and is left alone,
with a warning.

### Adding a provider

`agentfleet add` saves a new gateway profile without reinstalling:

```bash
export OPENROUTER_TOKEN=...          # or leave --token-env out to be prompted
agentfleet add openrouter --gateway-url https://openrouter.ai/api --token-env OPENROUTER_TOKEN
agentfleet use openrouter            # or pass --use to add
```

It lists the gateway's models first, and saves nothing if the token is
rejected or no model is available. Every lane is assigned one of that
gateway's models, under a generic policy that approves all of its models. The
token is read from the named environment variable, or typed at a hidden
prompt when you leave `--token-env` out; there is no flag that takes the token
itself, so it never lands in your shell history. `--force` replaces a saved
profile of the same name. `http://` is accepted only for localhost, with
`--allow-insecure-http`.

The gateway must accept Anthropic Messages requests and list its models at
`/v1/models`.

| Provider | `--gateway-url` | Notes |
|---|---|---|
| OpenRouter | `https://openrouter.ai/api` | Namespaced model ids (`anthropic/claude-sonnet-4.5`); the model list includes context sizes. |
| LiteLLM proxy | your proxy, e.g. `http://localhost:4000` | Its model list has no context sizes. |
| Vercel AI Gateway | `https://ai-gateway.vercel.sh/claude-code` | Claude Code support is documented; the model list endpoint is not, so `add` may be unable to list models. |
| OpenCode Zen | `https://opencode.ai/zen` | Claude models only on its Anthropic endpoint; the base URL is not documented. |
| OmniRoute or another Anthropic-compatible gateway | your URL | |

Re-running the installer with a different `--gateway-url` also switches
providers, but it cannot keep the old setup: it uses the policy and choices of
a saved profile for that endpoint if there is one, and otherwise the generic
policy without the old provider's pins and exclusions. Prefer
`agentfleet add`. After a switch with `agentfleet`, re-running the original
install command (or an update) keeps the gateway you switched to.

---

## Commands

| Command | Effect |
|---|---|
| `agentfleet profiles` | List saved profiles with each one's provider policy (`*` = active). |
| `agentfleet use <name>` | Switch to a saved profile, or `native` for your Claude login. `--force` re-applies the active one. |
| `agentfleet save <name>` | Save the current setup as a profile. |
| `agentfleet add <name> --gateway-url URL` | Add a gateway profile without reinstalling (`--token-env VAR`, `--use`, `--force`, `--allow-insecure-http`). |
| `agentfleet remove <name> [--yes]` | Delete a saved profile and its token. The active profile and `native` cannot be removed. |
| `agentfleet configure [--yes\|--preset FILE\|--export FILE]` | Revisit every feature; keep values and mark questions answered, apply a team preset, or export settings without secrets. |
| `agentfleet config [setting] [value]` | View every feature or configure `work-style`, `ask-first`, `branch-check`, `compact`, `reviewer-access`, `learn`, or `agent-brain`. |
| `agentfleet instructions [--project DIR] [--check]` | Check your Claude Code instruction files for stale lane names, duplicates, and contradictions; apply edits only with your approval. `--check` reports without editing. |
| `agentfleet jev <on\|off\|status>` | Enable, disable, or inspect optional `fast-jev-compaction` plugin. |
| `agentfleet report [--days N] [--json]` | Summarize subagent runs (files changed, blocked commands) and active provider outages. |
| `agentfleet suggest [--project DIR] [--json] [--dismiss ID…] [--seen] [--forget]` | Propose plugins, MCP servers, mods, and hooks for the project (`--forget` clears usage history). |
| `agentfleet project pin LANE MODEL` | Pin a lane to a model for the current project (`--fallbacks A,B`, `--dir DIR`). |
| `agentfleet project unpin LANE` | Remove a project-level lane pin (`--dir DIR`). |
| `agentfleet project show` | Show project-level lane overrides (`--dir DIR`). |
| `agentfleet status` | Show the active profile and run the audit (live models, stale settings, hook health, agents, skills, and plugins). |
| `agentfleet doctor` | Verify the installation (`claude-agents-doctor --check`). |
| `agentfleet sync` | Regenerate fleet agents from `~/.claude/fleet.json`; pass `--check` to verify without writing. |
| `agentfleet update [--preset FILE] [--no-configure]` | Install the latest release, keeping the profile; configure unanswered options in a terminal, apply a preset, or skip configuration. |
| `agentfleet auto-update [on\|off\|status]` | Turn automatic background updates on or off, or show their status. |
| `agentfleet uninstall` | Remove AgentFleet and restore earlier settings. |
| `agentfleet rollback [--backup DIR]` | Restore the files and settings from before the last install or update (the newest backup, or a named one). |
| `agentfleet version` | Print the installed version. |
| `claude-fleet-setup --audit [--no-health]` | Scan models, settings, hooks, agents, skills, and plugin/MCP health (`--no-health` skips health checks). |
| `claude-fleet-setup --compact-percent P` | Set the auto-compaction percentage (20–95 or `off`). |
| `claude-fleet-setup --compaction N` | Set an absolute auto-compact window override (sets `compactPercent` to `off`). |
| `claude-fleet-setup --archive-agents <categories>` | Archive agent files by category. |
| `claude-fleet-setup --restore-agents <categories>` | Restore archived agents. |
| `claude-fleet-setup --archive-skills` / `--restore-skills` | Archive or restore skills. |

---

## What gets installed

- `~/.claude/agents/fleet-*.md` — one generated agent per fleet lane, each with
  an optional fallback model chain (at most three entries, for provider
  unavailability or overload only).
- `~/.claude/agentfleet/mods/` — the local `agentfleet` plugin catalog.
  `fleet-status`, `lane-guard`, `token-weather` and `brain` are installed and on by
  default; `fast-jev-compaction` is opt-in. See [Mods](#mods).
- `~/.claude/settings.json` — merged settings. Unrelated permissions, hooks,
  plugins, and environment values are preserved. Mode 0600 because it can
  contain a gateway token.
- `~/.claude/agentfleet/config.json` — user preferences (`workStyle`,
  `askFirst`, `branchCheck`, `compactPercent`, `reviewerAccess`, `learn`, `agentBrain`).
- `~/.claude/rules/agentfleet.md` — the policy for your work style (300 to 500
  words, plus about 300 for the ask-first section and 200 for the branch check). Claude Code loads it automatically. AgentFleet manages
  this file and rewrites it on update. It does not touch your own
  `~/.claude/CLAUDE.md`, so a first install over a personal `CLAUDE.md` works.
  Keep your own instructions in `CLAUDE.md` or in other files under
  `~/.claude/rules/`.
- `~/.claude/route-to-fleet.py` — UserPromptSubmit routing hook.
- `~/.claude/fleet_watch.py` — subagent reports, outage failover, and usage tracker.
- `~/.claude/fleet_suggest.py` — project capability suggestions engine.
- `~/.claude/agent_brain.py` — installs, updates and connects agent-brain.
- `~/.claude/subagent-statusline.py` — native subagent status rendering.
- `~/.claude/sync-provider-models.mjs` — startup hook for gateway model
  discovery and fleet reconciliation.
- `~/.claude/sync-model-context.py` — PreModelSwitch/PostModelSwitch hook that
  keeps the global auto-compact percentage and full window paired, and updates
  the gateway's real-context cap when needed. Fail-open.
- `~/.claude/provider_catalog.py` and `fleet-reconcile.py` — provider identity,
  cache, and approved-family reconciliation helpers.
- `~/.claude/fleet-model-drift.py` — fail-open drift detector that writes a
  mode-0600 proposal and emits bounded startup context.
- `~/.claude/claude-fleet-setup.py` — the setup/reconfigure tool. Prompts
  only in a TTY; hooks never invoke it interactively.
- `~/.claude/fleet.json` — the canonical fleet map.
  `~/.config/delegate-skills/config.json` is a legacy mirror kept in sync.
- `~/.config/delegate-skills/provider-policy.json` — provider policy. Generic
  (one catch-all family) on a fresh install; an existing install keeps its own
  provider-specific families and aliases on update, unless `--gateway-url`
  names a different gateway. `agentfleet use` swaps in each profile's own
  policy.
- Command wrappers in `~/.local/bin/` (POSIX) or equivalent (Windows).
- `~/.claude/.claude-agents-config-manifest.json` — installed-tree verification
  data, so the doctor does not require the source clone.

Machine-local marketplace source paths are not copied (they cannot be
portable). Remote marketplace entries, enabled plugin selections, permissions,
and model picker descriptions are preserved. AgentFleet writes no
`agent-brain` hooks: agent-brain adds and removes its own (see
[agent-brain](#agent-brain)).

AgentFleet 2.3 and earlier kept the policy in `~/.claude/CLAUDE.md`. On upgrade,
if an earlier install replaced your own `CLAUDE.md`, AgentFleet restores your
original from the backup. An unedited old AgentFleet copy is removed. If you
edited it, AgentFleet keeps it and tells you.

---

## Mods

Mods are plugins whose code runs inside Claude Code and can draw in its
interface. The local `agentfleet` catalog lives at
`~/.claude/agentfleet/mods/`. Mods need Claude Code 2.1.287 or later and show
in the terminal and Claude Desktop's Code tab, not in the VS Code extension's
chat panel, `claude -p`, the Agent SDK, or Desktop WSL sessions. They run with
your user permissions and are not sandboxed.

### Installed by default

- **fleet-status** (v1.4.0): displays an advisory band above the prompt when
  action is needed: restart after an update, inspect a failed update, review
  missing models or proposals with `/fleet-setup`, dispatch a fallback for a
  failing lane, or review suggestions with `/fleet-suggest`. The band has a
  Hide button. `/fleet-status` prints version, active profile ("not recorded"
  if absent), `Work style: …`, auto-update state ("enabled (default)" if
  unconfigured), model proposals, auto-compact percentage, reviewer access level, learning state,
  active outages, project suggestions, a JEV stats line, and `Options: all reviewed`
  or `Options: N new (run agentfleet configure)`; from 1.4.0 the count includes
  the work style, ask-first, token-weather and agent-brain options, and from
  1.5.1 the agent-brain install option. The band
  also names a lane that failed over to another of its models. New unanswered options trigger
  "New AgentFleet options are available: run agentfleet configure in a terminal."
  `/fleet-tasks` opens a focused,
  closable pane showing active subagents, recent lane execution reports, and
  active provider outages. Turn it off with `claude plugin disable fleet-status@agentfleet`.
- **lane-guard**: deterministically guards subagents (`fleet-*`) from executing
  dangerous or outward-facing actions:
  - Git mutations: `commit`, `push`, `merge`, `rebase`, `reset`, `restore`, `checkout`, `switch`, `clean`, `revert`, mutating `branch`/`tag`/`stash` flags.
  - GitHub writes: `gh pr create|merge|close`, `gh release`, `gh repo`, `gh issue`, mutating `gh api` calls.
  - Publishing and deployment: package publishing (`npm`, `pnpm`, `cargo`, `twine`), container deployment (`docker push`, `kubectl`, `helm`), cloud deploy (`terraform`, `vercel`, `fly`), and `deploy.sh`.
  - Migrations: `artisan migrate`, `rails db:migrate`, `prisma migrate`, `alembic`, `manage.py migrate`, etc.
  - Destructive SQL: `DROP`, `TRUNCATE`, `ALTER TABLE … DROP`, and unconstrained `DELETE`/`UPDATE` without a `WHERE` clause across DB CLI tools and MCP servers.
  - Dangerous file removal: `rm -rf` targeting root, home, parent directories, `.git`, or wildcard.
  - Model failover: while a lane's own model is down (see [Outages and lane reports](#outages-and-lane-reports)), new runs of that lane start on the first of its fallback models that works, marked `(failover: <model>)` in the task description.
  - Turn-limit warnings (from lane-guard 1.2.0): at 70% of a lane's `maxTurns` it tells the lane to finish the part it is on, run its check and report; at 90%, to report now. The lane reads the warning after a tool result; you do not see it.
  `lane-guard` intercepts subagents only; commands in the main conversation are never blocked. Disable it via `/plugin`; that also turns off model failover and the turn-limit warnings.
- **token-weather** (v0.1.0-agentfleet.1), Anthropic's sample mod (Apache-2.0),
  forecasts how full the context window is above the prompt. It draws below the
  other mods' bands and never replaces them. On by default from 2.5.0; an update
  adds it once and never re-adds it after you remove it. Turn it off with
  `/plugin` or `agentfleet configure`.
- **brain**: adds a `/brain` command that opens a scrollable pane with agent-brain
  token usage and, when agent-brain memory is active for the project, the project's
  memories, critical ones first (↑/↓ to move, Enter to read one in full, Esc to close).
  On by default from 2.7.0; an update adds it once and never re-adds it after you
  remove it. Turn it off with `/plugin`.

### Optional mods

- **fast-jev-compaction** (v0.3.0-agentfleet.3), by tamaratran (MIT), replaces Claude Code's
  compaction summary with TypeSafe's Jev model decisions: obsolete tool calls
  and results are dropped or truncated while retained turns remain verbatim.
  It answers only compactions initiated by Claude Code in the main conversation
  (subagents and precomputes pass through). It requires a TypeSafe API key
  configured via `/plugin` or `TYPESAFE_API_KEY` in the shell or settings `env`.
  `agentfleet configure` can accept it with hidden input and save it in settings.
  If no key is configured, it logs a single notice per session and falls back to
  Claude Code's built-in summary. Manage with `agentfleet jev on|off|status` or
  `claude plugin install fast-jev-compaction@agentfleet`. At compaction time,
  main conversation text and tool inputs are sent to `api.typesafe.ai`.
  A band above the prompt shows before/after token estimates, percentage reduction,
  messages kept word-for-word, and elapsed time, or why it fell back to Claude's
  summary. It clears on your next message or with Hide. `/jev` shows key
  availability, auto-compact percentage, session totals, and the last five
  compactions. Lifetime stats in `~/.claude/agentfleet/jev-stats.json` contain
  neither conversation content nor a key. `agentfleet jev status` detects keys
  in the settings environment as well as your shell; `/fleet-status` has a JEV line.

On install, if Claude Code 2.1.287 or later is on PATH, the installer registers
the catalog and installs default mods once:

```bash
claude plugin marketplace add ~/.claude/agentfleet/mods
claude plugin install fleet-status@agentfleet
claude plugin install lane-guard@agentfleet
```

Updates, including automatic ones, rewrite the mod files and then update each
installed mod whose version changed (`claude plugin update`), for the next
Claude Code session. They never re-register, so disabled or uninstalled mods
stay that way. Opt out with `--no-mods` or `AGENTFLEET_MODS=0`; the choice is
remembered, and `agentfleet update` honors the variable. `--mods` opts back in.

---

## Context compaction

**One percentage for every model.** `compactPercent` defaults to `70` in
`~/.claude/agentfleet/config.json`; choose `20`–`95` or `off`.

AgentFleet writes Claude Code's own `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` and a
1,000,000-token window (`autoCompactWindow` and `CLAUDE_CODE_AUTO_COMPACT_WINDOW`).
Claude Code clamps that window to each model and compacts at the chosen percentage
of its **usable context** (the window minus a 20,000-token output reserve).
Switching models retains the percentage without recalculation, including low
percentages such as 30% on smaller models.

Restart Claude Code **once after changing the percentage**. Gateway profiles also
set `CLAUDE_CODE_MAX_CONTEXT_TOKENS` to the active model's verified real context
(capped at 1,000,000 tokens). A gateway model switch asks for a restart only when
that cap changes, not to recalculate the percentage. Native profiles do not need
that gateway cap.

`off` means AgentFleet no longer manages the percentage and removes
`CLAUDE_AUTOCOMPACT_PCT_OVERRIDE`; it does not turn off Claude Code's own
compaction. `claude-fleet-setup --compaction <tokens>` sets an absolute window
and turns the percentage preference off. Existing 2.2.0 installs migrate
automatically; the doctor checks that the settings percentage matches the
preference.

Configure compaction using:

```bash
agentfleet config compact 75             # set percentage
agentfleet config compact off            # stop managing the compaction window
claude-fleet-setup --compact-percent 75  # CLI configuration
claude-fleet-setup --compaction 300000   # absolute token override (disables percentage)
```

---

## Outages and lane reports

On a gateway profile, each lane in `~/.claude/fleet.json` has a model and up to three fallback models, ranked from your provider's live models. `/fleet-setup` can pin a lane to your own list instead, for example `fleet-implement` on `claude-haiku-5.5`, then `codex-luna`, then a Gemini model; the first is the lane's model and the rest are its fallbacks in that order. Native profiles have no fallbacks. When a lane run fails with an API error 429 (quota), 5xx or 529 (overloaded):
- **Model outages**: AgentFleet records that model as down in `~/.claude/agentfleet/outages.json`: for 60 minutes after a 429, 15 minutes after other errors, or until a later run on it succeeds.
- **Same-lane failover**: with the `lane-guard` mod on (the default), the lane keeps working. New runs start on the first of its fallback models that is not down, and the prompt hook tells Claude to re-dispatch the failed task to the same lane, once; if that run fails too, Claude does the work itself. In the example above, if `claude-haiku-5.5` runs out of quota, the next `fleet-implement` run uses `codex-luna`, and the Gemini model if `codex-luna` is down too.
- **Another lane**: only when every model of a lane is down, or `lane-guard` is off, does the prompt hook name a lane with a working model instead. In the `fleet` work style a request routed to that lane is sent to the alternative while the outage lasts.
- **Gateway outages**: when models from two or more vendors fail within 10 minutes, the gateway itself is failing and switching models will not help. The prompt hook says so once and tells Claude to do the work itself, without dispatching lanes, until the outage expires.
- **Status**: the fleet-status band shows which model a lane switched to, and `/fleet-status` lists the models that are down.
- **Execution reports**: When each subagent finishes, AgentFleet logs a completion report to `~/.claude/agentfleet/lane-reports.jsonl` (local, rotated at 256 KB). Reports record modified files, any forbidden commands the lane ran (checked by AgentFleet's own rules, whether or not `lane-guard` blocked them), and API errors, allowing the main agent to verify lane actions before merging.
- **Inspection**: Summarize reports and active outages using `agentfleet report [--days N] [--json]` or by opening the `/fleet-tasks` pane.

---

## Capability suggestions

The `/fleet-suggest` skill and `fleet_suggest.py` engine analyze the current repository to recommend relevant capabilities:
- **Local repository inspection**: Scans project files and configuration locally without network requests and never reads `.env` files or secret values.
- **Curated catalog**: Evaluates a 59-entry catalog (`config/suggestions.json`) including official-marketplace plugins (language servers, MCP server integrations), AgentFleet optional mods, pre-drafted hooks (`format-after-edit`, `protect-secrets`), project skill generators for repeated tasks, and `/init` if `CLAUDE.md` is absent.
- **Project workflow**: For a repository with a GitHub remote, it offers a drafted "Git workflow" section for the project's `CLAUDE.md`: a branch and pull request per feature, the test command to run first, and no commits, pushes or deploys unless asked. It fills in your main branch and test command, shows the draft, and writes it only after you approve. It is not offered once the section exists.
- **Spec Kit**: For a new or small project, it offers [Spec Kit](https://github.com/github/spec-kit), which writes a spec, a plan and a task list before code. It suits new projects and large features; small changes don't need it. Installing it runs `uv tool install specify-cli` and `specify init --here --integration claude`, after you approve.
- **Local availability**: Recommends only plugins available in your local marketplaces that are not yet installed.
- **Controlled presentation**: The status band shows one new suggestion at a time ("AgentFleet suggests …"). Opening `/fleet-suggest` marks suggestions seen and clears the band; installed items drop out on the next prompt. Hide remembers the suggestion. The model-facing notice is skipped when the band is enabled, and nothing is installed without explicit selection.
- **Usage learning**: On by default for new installs; existing installs keep their setting and are asked once by the configuration wizard. AgentFleet tallies command names, tool names, documentation sites, and slash commands per project, never prompt text, under `~/.claude/agentfleet/usage/`. Turn it off with `agentfleet config learn off`; run `agentfleet suggest --forget` to erase recorded usage and suggestion history.
- **Management**:
  ```bash
  agentfleet suggest [--project DIR] [--json] [--dismiss ID…] [--seen] [--forget]
  ```

---

## Privacy and routing policy

- **Routing log**: The routing hook writes to a mode-0600 JSONL log (`~/.claude/routing-log.jsonl`). Routed entries record only lane name, routing reason, timestamp, and character count — never prompt text. A `no-match` entry stores at most a 160-character snippet with credential-shaped tokens redacted. Excerpts purge after 14 days; entries purge after 90 days. Rotates at 2,000,000 bytes. Fail-open.
- **Outages, reports, and suggestions**: Outage records (`outages.json`), subagent execution logs (`lane-reports.jsonl`), and suggestions metadata stay completely local to your machine.
- **Instruction check**: `agentfleet instructions` sends the text of your instruction files to your model provider through one Claude Code run, as Claude Code already does each session. It runs only when you ask for it or accept it in the wizard.
- **Usage learning**: On by default for new installs; existing settings are preserved. It records only sanitized command names, tool identifiers, documentation sites, and slash commands without prompt text in `~/.claude/agentfleet/usage/`. Disable with `agentfleet config learn off`; erase with `agentfleet suggest --forget`.
- **Local mod safety**: `fleet-status` and `lane-guard` run locally, access no external network, and never read `settings.json`, profiles, or secrets.
- **Optional JEV egress**: The opt-in `fast-jev-compaction` mod transmits main-conversation message text and tool inputs to `api.typesafe.ai` when compaction occurs, with tool results replaced by summary notes. API keys are loaded from `/plugin` or `TYPESAFE_API_KEY`; the configuration wizard can store that variable in the settings environment with hidden input. Preset exports and JEV stats never contain the key.

---

## Safety guarantees

- **Backups before every apply.** All managed files are copied to a mode-0700
  snapshot under `~/.claude/backups/claude-agents-config/` before any write.
- **Rollback.** `agentfleet rollback` (or `install.py --rollback --apply
  [--backup DIR]` from a clone) restores the newest pre-change snapshot;
  `agentfleet rollback --backup DIR` restores a named one. The backup is kept
  until you remove it.
- **Idempotent.** Repeated installs produce the same configuration and
  regenerate no duplicate hooks.
- **Non-destructive.** Uninstall removes only files and hook entries owned by
  this bundle. Unrelated top-level settings and plugin selections are not
  touched.
- **No accidental overwrites.** Existing files without the bundle's ownership
  marker are never overwritten without `--force-owned`.
- **Credential safety.** The scanner checks the bundle for credential-shaped
  literals. The gateway token is the only secret normally needed; keep it in a
  protected environment variable, not in version control, shell history, or
  logs.

`agentfleet uninstall` also removes the `agentfleet` catalog and its plugins
from Claude Code. A rollback to a backup from before 2.1.0 does too. It leaves
agent-brain installed and connected; remove that with `agent-brain uninstall`.

---

## Upgrading from 1.0.0

The installer migrates automatically. It renames lanes that used to encode
vendor model names:

| Old lane name | New lane name |
|---|---|
| `implement-02-gemini-flash` | `implement-fast` |
| `implement-03-agy-opus` | `implement-deep` |
| `implement-04-free-1m` | `implement-cheap` |
| `implement-05-agy-sonnet` | `implement-alt` |
| `review-02-opus` | `review-alt` |
| `review-06-astra` | `review-deep` |

Other numbered lanes are retired. The old `sync-omniroute-models.mjs` hook
script is removed and replaced by `sync-provider-models.mjs`. The model cache
is now `~/.claude/cache/provider-models-cache.json`. All of this happens inside
the same backup/rollback transaction, so the upgrade is reversible.

---

## Development

```bash
# Run the test suite:
python3 -m unittest discover tests

# After editing any bundle file, update the manifest
# (the installer refuses a bundle whose manifest/checksums do not match):
python3 packaging/update-manifest.py

# Build a reproducible release from a git commit, then publish it:
python3 packaging/build-release.py --ref <commit>
# Produces: dist/releases/<version>/agentfleet-<version>.tar.gz, .zip,
#           SHA256SUMS, and dist/releases/latest.json
export AGENTFLEET_DEPLOY_HOST=user@server
sh packaging/deploy.sh --dry-run   # list every change and deletion first
sh packaging/deploy.sh             # upload site/, then archives, then latest.json
```

`deploy.sh` mirrors `site/` with `rsync --delete`, so it only syncs into a web
root that is empty or already carries the `.agentfleet-docroot` marker from an
earlier deploy. Published release archives are never deleted.

`site/`, `packaging/`, `dist/`, `.github/`, and a local `.claude/` are
repository-only and are never part of an installed bundle.

If `agent-brain` is installed (on PATH or in its standard folder) and not
switched off, lanes receive its memory search tool. Otherwise it is omitted
with no functional change.

---

## Contributing and roadmap

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup, project rules, tests, and the
pull request process. The next phase is support for other agent CLIs besides
Claude Code (OpenAI Codex CLI, OpenCode, Kilo Code, Cline); open an issue to
discuss the design before sending code.
