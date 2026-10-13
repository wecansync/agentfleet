<!-- claude-agents-config:managed -->

# AgentFleet

AgentFleet adds the `fleet-*` subagents, called lanes. Each lane runs on a model picked for its role, and its description says what it is for. `agentfleet config` shows the current settings.

## Work style

<!-- work-style:balanced -->
Work directly by default: answer questions, make edits, fix bugs, and run commands and tests yourself. Decide whether to use lanes after you have looked at the relevant code, not from how the request is worded: a few words can describe a large job, and a long message can be a small fix. Use lanes when what you found shows that:

- the work splits into two or more parts that change different files and do not need each other's results: one lane per part, in parallel;
- answering needs a sweep of many files or pages whose detail you will not need afterwards;
- a risky change (security, installer, migration, data loss) needs an independent review on a lane whose model did not write it.

Before a large job, say in one line whether you will do it directly or split it, and why, so the user can redirect you. If the work turns out bigger than it looked, hand the remaining independent parts to lanes. Plan as a separate step only when the approach is unclear, and review as a separate step only when the change is risky.
<!-- /work-style -->
<!-- work-style:direct -->
Do all work yourself. Dispatch a lane only when the user asks for one.
<!-- /work-style -->
<!-- work-style:fleet -->
Delegate substantive work to the matching lane: plan when the approach is open, then implement, then review. Work directly on a single obvious edit, a question the context already answers, or a command whose output you need next.
<!-- /work-style -->

## Dispatching a lane

- A lane starts without this file and without the conversation, so the brief is all it knows: the goal, the files in scope, constraints, the check to run, and what to report.
- Give each lane one concern it can finish well inside its turn limit. Split bigger work into separate lanes rather than one long brief.
- Save tokens: pick the lightest lane that fits (`-fast` for small, well-specified edits, `-cheap` for mechanical bulk changes, `-deep` only for hard problems), and point briefs at files and line ranges instead of pasting their contents.
- Lanes that write in parallel need disjoint files.
- Review lanes run checks and tests; they edit only in FIX MODE, with the brief listing the files they may change. `agentfleet config reviewer-access` can restrict them.
- When a lane reports, read its diff and rerun the check it names. Redo its investigation only when the diff or the check disagrees with its report.
- Lanes cannot commit, push, merge, deploy, or run migrations; you do those, and only when the user's request covers them.
- When an AgentFleet notice says a lane's model failed, re-dispatch the task as it says: to the same lane when the lane has switched to another of its models, otherwise to the lane it names.

## Safety

- Confirm before destructive or outward-facing actions: deleting files, force pushing, publishing, deploying.
- Keep API keys and tokens out of output, logs, and commits. When a step needs an interactive login, ask the user to run it as `! <command>`.
