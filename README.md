# AgentFleet

AgentFleet installs a fleet of native Claude Code subagents ("lanes") under
`~/.claude/agents/fleet-*.md` and maps each lane to whatever models your
provider offers: a Claude subscription, an Anthropic API key, or any
Anthropic-compatible gateway whose models are discovered from `GET /v1/models`.

It also installs a routing hook, a model-sync startup hook, a context-budget
hook, a privacy-safe status line, a global `CLAUDE.md`, the `/fleet-setup` and
`/fleet-suggest` skills, and a local Claude Code mods catalog with `fleet-status`
and `lane-guard` on by default. Everything lives under your home directory; no
root or admin access is required.

Version: **2.3.0** — MIT license. See [LICENSE](LICENSE). See [CHANGELOG.md](CHANGELOG.md) for release history.

> AgentFleet is an independent project by WeCanSync. It is not affiliated with
> or endorsed by Anthropic. Claude and Claude Code are trademarks of Anthropic.

---

## Requirements

- Claude Code
- Python 3.10+ (on Windows, one installed with `uv python install` is found even when it is not on PATH)
- Node.js 18+

---

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

Apply a team preset with `AGENTFLEET_PRESET=team.json` (see [For teams](#for-teams))
or `agentfleet update --preset team.json`.

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
updates, covering auto-compact, JEV, reviewer access, learning, the status band,
lane-guard, and automatic updates. Run `agentfleet configure` to revisit every
question.

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
| `--provider native\|anthropic-api\|gateway` | Set the provider without the wizard. `gateway` also turns on startup model discovery. |
| `--gateway-url URL` | Gateway base URL (https required outside loopback). |
| `--gateway-token-env NAME` | Environment variable that holds the gateway token. |
| `--enable-model-discovery` | Turn on startup model discovery for a bare `--gateway-url` install (implied by `--provider gateway`). |
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

`AGENTFLEET_NONINTERACTIVE=1` disables all prompts.

`AGENTFLEET_MODS=0` also opts out of mods; `agentfleet update` honors the
variable. See [Mods](#mods) for requirements and optional plugins.

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
$env:AGENTFLEET_PRESET="team.json"; irm https://agentfleet.wecansync.com/install.ps1 | iex
```

Already installed? Use `agentfleet update --preset team.json` or
`agentfleet configure --preset team.json`. A preset does not supply credentials;
each person sets their own TypeSafe key with hidden input in `agentfleet configure`
or through `/plugin` (fast-jev-compaction → Configure), never in chat.

Verify with `agentfleet config`, `/fleet-status`, and `/jev` (when JEV is enabled).
Roll back an install or update with `agentfleet rollback`.

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

Run `agentfleet configure` in a terminal to ask every question: auto-compact
percentage, JEV, reviewer access, learning, fleet-status, lane-guard, and automatic
updates. Enter keeps the current value. When enabling JEV without an available
key, the wizard offers hidden TypeSafe API key input and stores it as
`TYPESAFE_API_KEY` in `~/.claude/settings.json`'s `env` (mode 0600).

```bash
agentfleet configure --yes              # keep current values; mark questions answered
agentfleet configure --preset team.json # apply a team preset without prompts
agentfleet configure --export team.json # export settings, never secrets
```

Presets use `agentfleet.preset.v1`; feature keys are optional. Unknown fields,
invalid values, and any key containing "key", "token", or "secret" are rejected.
Example `team.json`:

```json
{"format":"agentfleet.preset.v1","compact":70,"jev":true,"reviewerAccess":"full","learn":true,"statusBand":true,"laneGuard":true,"autoUpdate":true}
```

`agentfleet config` shows every feature. The shared compact, reviewer, and learning
preferences live in `~/.claude/agentfleet/config.json`:

```bash
agentfleet config                                    # view effective preferences
agentfleet config compact <20-95|off>                # set auto-compact percentage (default: 70)
agentfleet config reviewer-access full|run|read-only # set reviewer tool access (default: full)
agentfleet config learn on|off                       # command usage learning (new installs: on)
```

### Reviewer modes

Reviewer lanes (`fleet-review`, `fleet-review-alt`, `fleet-review-deep`, `fleet-security-review`) run in **CHECK mode** by default: they have `Bash`, `Edit`, and `Write` tools to execute test suites, linters, builds, and read-only checks, but do not edit files.

To allow a reviewer to apply corrections, the brief must explicitly state **FIX MODE** and list the target files it may modify. A reviewer in FIX MODE requires disjoint file scopes from any other active writable lane. A lane must never review its own fixes; dispatch fixes to `fleet-review-alt` or another independent reviewer. `fleet-diagnose-static` receives `Bash` to reproduce failures, but never has edit tools.

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
| `agentfleet config [setting] [value]` | View every feature or configure `compact`, `reviewer-access`, or `learn`. |
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
  `fleet-status` and `lane-guard` are installed and on by default; `token-weather`
  and `fast-jev-compaction` are opt-in. See [Mods](#mods).
- `~/.claude/settings.json` — merged settings. Unrelated permissions, hooks,
  plugins, and environment values are preserved. Mode 0600 because it can
  contain a gateway token.
- `~/.claude/agentfleet/config.json` — user preferences (`compactPercent`,
  `reviewerAccess`, `learn`).
- `~/.claude/CLAUDE.md` — the global delegation policy. AgentFleet manages this
  file and rewrites it on update; keep your own instructions in
  `~/.claude/rules/*.md`, which Claude Code loads in every session and
  AgentFleet never touches.
- `~/.claude/route-to-fleet.py` — UserPromptSubmit routing hook.
- `~/.claude/fleet_watch.py` — subagent reports, outage failover, and usage tracker.
- `~/.claude/fleet_suggest.py` — project capability suggestions engine.
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
and model picker descriptions are preserved. `agent-brain` hooks are guarded
with `command -v` and are inert when that optional tool is absent.

---

## Mods

Mods are plugins whose code runs inside Claude Code and can draw in its
interface. The local `agentfleet` catalog lives at
`~/.claude/agentfleet/mods/`. Mods need Claude Code 2.1.287 or later and show
in the terminal and Claude Desktop's Code tab, not in the VS Code extension's
chat panel, `claude -p`, the Agent SDK, or Desktop WSL sessions. They run with
your user permissions and are not sandboxed.

### Installed by default

- **fleet-status** (v1.2.1): displays an advisory band above the prompt when
  action is needed: restart after an update, inspect a failed update, review
  missing models or proposals with `/fleet-setup`, dispatch a fallback for a
  failing lane, or review suggestions with `/fleet-suggest`. The band has a
  Hide button. `/fleet-status` prints version, active profile ("not recorded"
  if absent), auto-update state ("enabled (default)" if unconfigured), model
  proposals, auto-compact percentage, reviewer access level, learning state,
  active outages, project suggestions, a JEV stats line, and `Options: all reviewed`
  or `Options: N new (run agentfleet configure)`. New unanswered options trigger
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
  `lane-guard` intercepts subagents only; commands in the main conversation are never blocked. Disable it via `/plugin`.

### Optional mods

- **token-weather**, Anthropic's sample mod (Apache-2.0), forecasts how full
  the context window is above the prompt. Install with `claude plugin install token-weather@agentfleet`.
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

Updates, including automatic ones, rewrite mod files in place for the next
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

When a provider gateway responds to a lane model with a 5xx gateway error:
- **Automatic outage tracking**: When a lane's model fails with a 5xx gateway error, that lane is marked as failing for 15 minutes from the failure in `~/.claude/agentfleet/outages.json`.
- **Intelligent failover**: AgentFleet's routing hook alerts the main agent and suggests a configured non-outage alternative lane with a different model. While the outage lasts, a request routed to that lane is sent to the alternative instead.
- **Execution reports**: When each subagent finishes, AgentFleet logs a completion report to `~/.claude/agentfleet/lane-reports.jsonl` (local, rotated at 256 KB). Reports record modified files, any forbidden commands the lane ran (checked by AgentFleet's own rules, whether or not `lane-guard` blocked them), and API errors, allowing the main agent to verify lane actions before merging.
- **Inspection**: Summarize reports and active outages using `agentfleet report [--days N] [--json]` or by opening the `/fleet-tasks` pane.

---

## Capability suggestions

The `/fleet-suggest` skill and `fleet_suggest.py` engine analyze the current repository to recommend relevant capabilities:
- **Local repository inspection**: Scans project files and configuration locally without network requests and never reads `.env` files or secret values.
- **Curated catalog**: Evaluates a 57-entry catalog (`config/suggestions.json`) including official-marketplace plugins (language servers, MCP server integrations), AgentFleet optional mods, pre-drafted hooks (`format-after-edit`, `protect-secrets`), project skill generators for repeated tasks, and `/init` if `CLAUDE.md` is absent.
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
from Claude Code. A rollback to a backup from before 2.1.0 does too.

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

If the `agent-brain` CLI is on PATH at install time, agents receive its memory
tools and hooks. Otherwise they are omitted with no functional change.

---

## Contributing and roadmap

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup, project rules, tests, and the
pull request process. The next phase is support for other agent CLIs besides
Claude Code (OpenAI Codex CLI, OpenCode, Kilo Code, Cline); open an issue to
discuss the design before sending code.
