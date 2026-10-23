# session-info

One row above the prompt with what this session is running on:

```
◆ claude-opus-5.5[1m]  ·  effort high  ·  up 1h 05m  ·  1 agent running  ·  ⎇ main  ·  12 prompts  ·  $4.21 at list price
```

- **Model and effort:** the main conversation's model, as `/model` shows it, and the effort its last request asked for.
- **Uptime:** how long this Claude Code session has been running.
- **Agents running:** subagents that are pending, running or waiting now.
- **Branch:** the git branch of the working directory, or the short commit when HEAD is detached. Worktrees read their own HEAD.
- **Prompts:** how many prompts you have sent.
- **Cost:** the session's cost as `/cost` totals it, at list price.

A part that is unknown is left out. On a narrow terminal the row leaves out the last parts first; the model is always shown. The uptime and branch refresh every minute, the rest after each prompt and turn.

The mod reads only what Claude Code already knows and the repository's `.git/HEAD`. It runs no commands and sends nothing anywhere.

AgentFleet installs it on by default from 2.13.0; an update adds it once and never re-adds it after you remove it. Turn it off with `/plugin` or `agentfleet configure`, or:

```
claude plugin disable session-info@agentfleet
```
