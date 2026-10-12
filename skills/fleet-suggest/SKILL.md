---
name: fleet-suggest
description: Suggest skills, plugins, MCP servers, mods, and hooks for the current project from its files and (opt-in) your usage; installs only what you pick.
---

# /fleet-suggest: Project Capability Suggestions

You are running **/fleet-suggest**. This wizard inspects the current project and opt-in usage counts to propose relevant plugins, MCP servers, mods, project skills, and settings hooks. It installs or writes only what the user picks.

## Rules
1. **Never install without explicit selection.** Ask before installing plugins or creating files.
2. **Third-party code disclosure.** Remind the user that plugins and mods execute code with their user permissions, and display each selected item's `access` line before running the install command.
3. **Never handle secrets in chat.** Never ask for, paste, or accept API keys or credentials. Direct the user to configure credentials in Claude Code (`/plugin`) or their shell environment.
4. **Draft and confirm files before writing.** Show the exact drafted content for hooks, skills, or CLAUDE.md before writing them.
5. **Merge hooks safely.** Hooks must be merged into `.claude/settings.json`, never overwriting existing hook definitions.
6. **Limit output.** Show at most 8 top suggestions compactly (title, why, access).

## Phase 1: Retrieve Suggestions
Run:
```bash
python3 ~/.claude/fleet_suggest.py --project "$PWD" --json
```
*(On Windows, use `python` or `py -3` if `python3` is not found).*

Parse the returned JSON payload. If `suggestions` is empty, report that no new capabilities match this project and exit cleanly.

Present up to 8 top suggestions compactly:
```
• <title> (<kind>) — <why>
  Access: <access>
```

## Phase 2: Selection
Use `AskUserQuestion` with `multiSelect: true` (maximum 4 options per question; batch into multiple questions if there are more than 4 items).
- Each option presents the item's title, kind, and a brief description.
- Include a "None / Skip" option.

For every item the user selects:
- **Plugins & Mods (`kind: plugin` or `kind: mod`):**
  - Remind the user: *"Plugins execute third-party code with your user permissions."*
  - Show the item's access line: `<access>`.
  - Run the install command: `claude plugin install <name>@<marketplace>`.
- **Project Hooks (`kind: hook`):**
  - Read project `.claude/settings.json` (create if missing).
  - Draft the merged hook definition into `.claude/settings.json` under `hooks.PostToolUse` or `hooks.PreToolUse`.
  - Show the drafted JSON diff and ask the user to confirm before writing.
- **Project Skills (`kind: create-skill`):**
  - Target path: `.claude/skills/<verb>/SKILL.md`.
  - Display the drafted SKILL.md content from the suggestion's template.
  - Ask confirmation and write the file.
- **Project Guide (`kind: claude-md`):**
  - Suggest running `/init` in Claude Code to generate a tailored CLAUDE.md file.

## Phase 3: Dismiss & Finalize
1. If unpicked suggestions remain, offer to dismiss them:
   ```bash
   python3 ~/.claude/fleet_suggest.py --project "$PWD" --dismiss <id1> <id2> ...
   ```
2. The band clears automatically when Phase 1 lists suggestions; no final `--seen` step is needed.
3. Briefly mention:
   - `agentfleet config learn on|off`: opt-in usage learning (counts command names, tools, doc sites, slash commands per project; never prompt text; on by default).
   - `agentfleet suggest --forget`: purges all stored usage counters and suggestion history.
