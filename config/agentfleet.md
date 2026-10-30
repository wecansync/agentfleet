<!-- claude-agents-config:managed -->

# AgentFleet

AgentFleet adds the `fleet-*` subagents, called lanes. Each lane runs on a model picked for its role, and its description says what it is for. `agentfleet config` shows the current settings.

<!-- ask-first -->
## How to approach a task

Do small, clear tasks straight away. For larger, unclear or risky ones, investigate first and agree on the approach before changing code, unless the user has already said how to proceed.

- **Docs first:** before changing an area or answering how something works, search the project's `docs/`, README and CLAUDE.md for it, then the code; search the web only for outside libraries and services. Check which version of a library the project uses before relying on its API.
- **Unclear:** ask what is still open in one message, with your recommended answer to each question. Never ask what you can find out yourself.
- **Risky or doubtful** (data loss, security, breaking changes, or a request that does not fit the code): say what worries you and recommend a safer way first.
- **Large:** offer the ways to run it, with your recommendation:
  - *Spec Kit* for a new feature whose requirements need pinning down. If the project lacks it, offer to install it (`uv tool install specify-cli`, then `specify init --here --integration claude --integration-options="--no-model-invocation"`) and to write its constitution with `/speckit-constitution`.
<!-- work-style:direct -->
  - *A plan to approve*, then build, test and fix it yourself.
<!-- /work-style -->
<!-- work-style:balanced -->
  - *A plan to approve*, then lanes for the long or independent parts, with a review lane only for risky parts.
<!-- /work-style -->
<!-- work-style:fleet -->
  - *A plan to approve*, then lanes for the independent parts, then your own review of what they changed.
<!-- /work-style -->
<!-- work-style:balanced,fleet -->
- **Several lanes:** when a task needs more than one lane, whether they run one after another or in parallel, and whether they write or only read, first give the user a concise plan: the parts, the lane for each, the files each one changes or reads, and the check that proves it. Do a bounded look at the code yourself first, so the plan is specific. Wait for the user's approval before the first lane runs. One lane, or none, needs no team plan; the approach agreement above still applies to large, unclear or risky work. An explicit approval by the user of a fully specified plan covers its continuations, a same-lane fallback and work after compaction; ask again only when the work adds scope or roles. Naming a lane in a request is not approval. Approval never covers pushing, merging, migrations or deploys.
<!-- /work-style -->
- **Several tasks:** ask whether to give each its own git worktree (parallel; best when they change different areas) or do them in turn in the current checkout (best when they share files).
- **New project:** ask whether there is a base project or boilerplate to start from, then propose the structure, design pattern and, for a backend, the database schema before writing code.
- **Stuck:** after two failed attempts at the same fix, stop and report what you tried.
- **Remember:** follow a "Workflow" section in the project's CLAUDE.md instead of asking; once the user picks a workflow for a project, offer to record it there.

<!-- /ask-first -->
<!-- branch-check -->
## Check the branch before changing files

In a git repository, before the first file change of each task, and before dispatching a lane that changes files:

1. Run `git status -sb` and `GIT_TERMINAL_PROMPT=0 git fetch --quiet` (skip the fetch when there is no remote; if it fails, say so and go on). Note the current branch, uncommitted changes, and whether the branch is behind or ahead of its remote.
2. Tell the user in one short message and ask which way to go, with your recommendation: continue on this branch, switch to another, or create a new one from the up-to-date default branch. Offer to pull first when the branch is behind. Recommend a new branch when the current one holds finished or unrelated work, for example when it is already merged or its name does not fit the task. Mention uncommitted changes, since switching carries them along.
3. Wait for the answer before changing files. Ask once per task, not before every edit. Reading code, answering questions and running read-only commands need no check.

Skip the check when the user has already chosen the branch for this task, or the task runs in a worktree made for it. Never pull, switch or create a branch without the user's answer, and lanes never do these at all.

<!-- /branch-check -->
## Make the smallest complete change

- **Reach first:** before writing, find every place the change must reach: callers, tests, fixtures, config.
- **Reuse:** what the codebase already has, then the standard library, then installed dependencies. Never add a dependency for a few lines.
- **Nothing unrequested:** add no feature, option or abstraction the task did not ask for. When you leave out something the user might expect, say so in one line.
- **But finish:** complete every part the task needs, including the callers, tests and fixtures your change breaks.

## Work style

<!-- work-style:balanced -->
Work directly by default: answer questions, make edits, fix bugs, and run commands and tests yourself. Decide whether to use lanes after you have looked at the relevant code, not from how the request is worded: a few words can describe a large job, and a long message can be a small fix. Use lanes when what you found shows that:

- the work splits into two or more parts that change different files and do not need each other's results: one lane per part, in parallel;
- answering needs a sweep of many files or pages whose detail you will not need afterwards;
- a risky change (security, migration, data loss) needs an independent review on a lane whose model did not write it.

Before a large job, say in one line whether you will do it directly or split it, and why, so the user can redirect you. If the work turns out bigger than it looked, hand the remaining independent parts to lanes. Plan as a separate step only when the approach is unclear, and review as a separate step only when the change is risky.
<!-- /work-style -->
<!-- work-style:direct -->
Do all work yourself. Dispatch a lane only when the user asks for one.
<!-- /work-style -->
<!-- work-style:fleet -->
Delegate substantive work to the matching lane: plan when the approach is open, then implement, then review the result as described below. Work directly on a single obvious edit, a question the context already answers, or a command whose output you need next.
<!-- /work-style -->

## Dispatching a lane

- A lane starts without this file and without the conversation, so the brief is all it knows: the goal, the files in scope, constraints, the check to run, and what to report.
- Give each lane one concern it can finish well inside its turn limit. Split bigger work into separate lanes rather than one long brief.
- Save tokens: pick the lightest lane that fits (`-fast` for small, well-specified edits, `-cheap` for mechanical bulk changes, `-deep` only for hard problems), and point briefs at files and line ranges instead of pasting their contents.
- Give a long, well-specified task to one implementation lane, in parts: when it reports a part, continue the same lane with SendMessage for the next, so it keeps what it has already read. Its turn limit counts across continuations: once it has used more than half of its turns (the tool uses in its completion notice), start a new lane for the next part, with a brief that says what is already done.
- A lane's report starts with `Outcome: done`, `partial`, `blocked` or `failed`. On `partial`, continue the same lane for the next part; on `blocked`, answer its question and continue it with SendMessage rather than starting a new lane.
- Lanes that write in parallel need disjoint files.
- Review lanes run checks and tests; they edit only in FIX MODE, with the brief listing the files they may change. `agentfleet config reviewer-access` can restrict them.
- When a lane reports, review its diff yourself and rerun the check it names. Redo its investigation only when the diff or the check disagrees with its report.
- Use a review lane only for security, data-loss or migration changes, or when the user asks, and at most one per change. Use `fleet-review-deep` only when the user asks for it.
- Fix small findings yourself. Send larger ones back to the lane that wrote the code with SendMessage, which keeps its context, rather than to a new lane. Do not review the fix on another lane unless it changes security, data-loss or migration code.
- Before you redo a lane's task yourself or dispatch it again, stop the old lane with TaskStop if it is still running, so it cannot change files afterwards.
- Lanes cannot commit, push, merge, deploy, or run migrations; you do those, and only when the user's request covers them.
- When an AgentFleet notice says a lane's model failed, re-dispatch the task once, as the notice says: to the same lane when it has switched to another of its models, otherwise to the lane it names. If that run fails too, or a notice says the gateway is failing, do the work yourself and tell the user.

## Safety

- Confirm before destructive or outward-facing actions: deleting files, force pushing, publishing, deploying.
- Keep API keys and tokens out of output, logs, and commits. When a step needs an interactive login, ask the user to run it as `! <command>`.

## Handoff

After a substantial task (code, files or a branch changed, or a long investigation), when it finishes, is blocked or is only partly done, or when the user asks for a status, end with one short handoff: the branch or worktree; the HEAD commit and any uncommitted changes, said plainly as tested or untested; the checks you ran and the ones you did not; the blockers and the next step; and any existing pull request or UAT link. Keep it to the task, not to each lane, and store nothing new for it. Answers to questions and short conversational replies get no handoff.
