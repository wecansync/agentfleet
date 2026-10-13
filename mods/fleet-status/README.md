# fleet-status

An AgentFleet band above the prompt shows actions worth taking: restart after an
update, inspect a failed update, review missing models and pending model changes
with `/fleet-setup`, dispatch a fallback for a failing lane, or review unseen
project suggestions with `/fleet-suggest`. Healthy sessions show no band. **Hide**
persists the currently displayed suggestion ids in `agentfleet/suggestions-hidden.json`
(the only file this mod writes), with session-only fallback if writing is unavailable.
Other notices are hidden for the session; changed notices show again. Listing suggestions
marks their ids seen and clears the band automatically. Surveys take precedence.
The band refreshes every 60 seconds, on prompt submission, and on turn completion.
It composes its band alongside `await next(e)` in a layout-only Box, preserving other mods' bands. Fallback dispatches
use the watcher's validated `alternative` lane in `outages.json`; missing, null or
invalid alternatives (including older files without this field) recommend retrying
after the outage expires.

`/fleet-status` prints a fresh version, profile, auto-update, model-proposal,
auto-compaction, reviewer-access, work-style, suggestions-learning and outage summary. Missing
profile markers show **not recorded**; auto-update defaults to **enabled**.
Configuration defaults are 70% compaction, full reviewer access, balanced work style, and learning on.
Compaction accepts 20–95% of each model's context, or `off`. The summary includes
strictly validated JEV compaction totals, last time, and before/after tokens when known.
Suggestions are matched to the session project root, not another project or a
nested working directory.

`/fleet-tasks` opens a focused, closable pane with fleet agents in this session
(lane, status and truncated description), up to eight recent lane reports (files
changed, forbidden rule ids, API errors and model ids), and current outages. It refreshes
every two seconds while open. Use **Close**, the engine close control, or Escape;
closing stops the refresh timer. Elapsed time is shown only when the host supplies
a validated start timestamp; the current AgentInfo API does not supply one.

The mod derives the Claude directory only from its known installed location
`<Claude directory>/agentfleet/mods/fleet-status` and reads only these files:

- `.claude-agents-config-manifest.json`
- `agentfleet/active`
- `agentfleet/auto-update.json`
- `fleet-model-proposal.json`
- `agentfleet/config.json`
- `agentfleet/outages.json`
- `agentfleet/suggestions.json`
- `agentfleet/suggestions-hidden.json`
- `agentfleet/jev-stats.json`
- `agentfleet/wizard.json` — on AgentFleet 2.3.0+, unreviewed options prompt `agentfleet configure` in a terminal and appear in `/fleet-status`; AgentFleet 2.4.0 adds the work-style and agent-brain questions (the instruction check is offered by the wizard but not counted).
- `agentfleet/lane-reports.jsonl`

It uses filesystem stat to check size and resolved paths before reading; links
that resolve to a different path are not read. Files over 512 KiB are skipped.
The reports parser retains the last 20 valid JSONL records, skipping malformed
records, and the pane displays the newest eight first. Missing or malformed
sources are treated as absent. Display strings reject C0/C1 controls, formatting
characters (including bidi overrides), and Unicode line/paragraph separators.

It **never reads `settings.json`, `agentfleet/secrets/`, or `agentfleet/profiles/`**.
No other settings, profiles, secrets, environment variables, processes or network
are accessed. It also reads the engine's session project root and agent list.
Nothing is added to model context unless you run a command (the tasks command
only reports whether the pane opened). Tests are not installed, and generated
`.claude-plugin/types` directories are git-ignored.

Install from the terminal:

```text
/plugin install fleet-status --marketplace wecansync/claude-agents-config
```

Disable it with `claude plugin disable fleet-status@agentfleet`.
