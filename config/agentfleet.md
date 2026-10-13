<!-- claude-agents-config:managed -->

# AgentFleet

AgentFleet adds the `fleet-*` subagents, called lanes. Each lane runs on a model picked for its role, and its description says what it is for. `agentfleet config` shows the current settings.

## Work style

<!-- work-style:balanced -->
Work directly by default: answer questions, make edits, fix bugs, and run commands and tests yourself. Dispatch a lane only when the task earns it:

- a large change across several areas that splits into independent parts, one lane per part;
- a broad sweep of the codebase or the web whose detail you do not need in your own context;
- an independent review of a risky change (security, installer, migration, data loss) on a lane whose model did not write it.

Plan as a separate step only when the approach is unclear. Review as a separate step only when the change is risky.
<!-- /work-style -->
<!-- work-style:direct -->
Do all work yourself. Dispatch a lane only when the user asks for one.
<!-- /work-style -->
<!-- work-style:fleet -->
Delegate substantive work to the matching lane: plan when the approach is open, then implement, then review. Work directly on a single obvious edit, a question the context already answers, or a command whose output you need next.
<!-- /work-style -->

## Dispatching a lane

- A lane starts without this file and without the conversation, so the brief is all it knows: the goal, the files in scope, constraints, the check to run, and what to report.
- Lanes that write in parallel need disjoint files.
- Review lanes run checks and tests; they edit only in FIX MODE, with the brief listing the files they may change. `agentfleet config reviewer-access` can restrict them.
- When a lane reports, read its diff and rerun the check it names. Redo its investigation only when the diff or the check disagrees with its report.
- Lanes cannot commit, push, merge, deploy, or run migrations; you do those, and only when the user's request covers them.
- When an AgentFleet notice says a lane is failing and names an alternative, dispatch the alternative.

## Safety

- Confirm before destructive or outward-facing actions: deleting files, force pushing, publishing, deploying.
- Keep API keys and tokens out of output, logs, and commits. When a step needs an interactive login, ask the user to run it as `! <command>`.
