# lane-guard

Claude Code mod for AgentFleet that guards fleet subagent lanes from running destructive or publishing commands, keeps a lane working when its model is down, and warns a lane before it runs out of turns.

## Context Mode compatibility

Fleet lanes may call only `ctx_search` and `ctx_stats` on recognized Context Mode
MCP servers. All other Context Mode tools, including execution, batch execution,
file execution, indexing, fetching, upgrades, purge and unknown future tools,
are denied. The main conversation is unaffected. This is a conservative
name-based compatibility boundary, not a general code sandbox. Keep lane-guard
enabled; if upstream routing repeatedly asks a lane to use a blocked tool,
disable Context Mode instead of bypassing this restriction.

The guard recognizes the official Context Mode server names, not arbitrary aliases.
Do not rename its MCP server or grant aliased execution tools to lanes. Search
can refresh previously indexed files and access other indexed projects; it is
retrieval-only with respect to code execution, not a strict read-only boundary.

## What It Blocks

`lane-guard` intercepts tool calls made by fleet subagents (any agent whose type starts with `fleet-`) and blocks actions across six categories. It looks through common wrappers and shell forms first: pipes and `xargs`, `for … do … done` loops, subshells, `timeout`, `nice`, `sudo`, `env`, `find -exec`, `bundle exec`, `uv`/`poetry`/`pipenv run`, `npx`/`pnpm exec`/`bunx`, `docker [compose] exec|run`, `kubectl exec`, Laravel Sail, and `bash -c` strings. Git global options such as `--no-pager`, `-P`, `-C` and `-c` are skipped before the subcommand.

1. **Git history or working-tree mutations (`[git-history]`)**:
   - `git commit`, `push`, `pull`, `merge`, `rebase`, `reset`, `restore`, `checkout`, `switch`, `clean`, `cherry-pick`, `revert`, `am`, `bisect` (except `bisect log`)
   - `Edit`, `Write`, `MultiEdit` and `NotebookEdit` on any path inside a `.git` directory (hooks, config)
   - `git tag` (creating or deleting tags; listing tags with bare `git tag`, `-l`, or `--list` remains allowed)
   - `git stash` (bare stash, `pop`, `drop`, `apply`, etc.; `stash list` and `stash show` remain allowed)
   - `git branch` with `-d`, `-D`, `-m`, `-M`, `--delete`, `--move`, `-f` (bare `git branch` remains allowed)
   - `git worktree remove`, `git worktree prune`
   - `git update-ref`, `git filter-branch`

2. **GitHub write operations (`[github-write]`)**:
   - `gh pr create`, `merge`, `close`, `edit`, `comment`, `review`, `ready`, `reopen`
   - `gh release …`
   - `gh repo create`, `delete`, `edit`, `rename`, `archive`, `fork`
   - `gh workflow run`, `enable`, `disable`
   - `gh issue create`, `close`, `edit`, `comment`, `delete`, `reopen`
   - `gh api` with mutating methods (`-X`/`--method` `POST`, `PUT`, `PATCH`, `DELETE`), or with `-f`/`-F`/`--field`/`--input` unless `-X GET` is given

3. **Publishing and deployment (`[publish-deploy]`)**:
   - Package publishing: `npm publish`, `pnpm publish`, `yarn publish`, `bun publish`, `twine upload`, `cargo publish`, `gem push`
   - Container / cluster deployment: `docker push`, `kubectl apply|delete|rollout|scale|patch|replace`, `helm install|upgrade|uninstall|rollback`
   - Infrastructure & cloud: `terraform apply|destroy`, `vercel --prod` / `vercel deploy`, `firebase deploy`, `fly deploy`, `flyctl deploy`
   - Script deployment: any path ending in `deploy.sh`, run directly or through `bash`, `sh`, `zsh`, `source` or `.`
   - Remote sync: `rsync` or `scp` targeting remote paths (`host:path`)

4. **Database migrations (`[migration]`)**:
   - `php artisan migrate…` (except `migrate:status` and `--pretend`), `php artisan db:wipe`
   - `rails db:migrate|drop|reset|rollback|setup|schema:load`, `rake db:migrate…`
   - `prisma migrate dev|deploy|reset`, `prisma db push`
   - `alembic upgrade|downgrade`
   - `manage.py migrate|flush`
   - `knex migrate:*`, `sequelize db:migrate*`, `diesel migration run|revert|redo`, `goose up|down|reset`, `flyway migrate|clean`
   - `dropdb`, `mysqladmin … drop`, `redis-cli … flushall|flushdb`

5. **Destructive SQL (`[db-write]`)**:
   - In segments that run a database client anywhere (`mysql`, `mariadb`, `psql`, `sqlite3`, `sqlcmd`, `clickhouse-client`, `duckdb`, `cockroach sql`, `wp db query`, also after `docker compose exec db`), and in database-like MCP tools (names containing sql, db, postgres, mysql, sqlite, mongo, supabase or query, or arguments named `sql`, `query`, `statement(s)` or `command`). AgentFleet's own memory tools are never scanned:
     - `DROP (TABLE|DATABASE|SCHEMA|INDEX|VIEW|MATERIALIZED VIEW|FUNCTION|PROCEDURE|TRIGGER|ROLE|USER|SEQUENCE|TYPE|EXTENSION)`
     - feeding a SQL file to a client (`psql -f file`, `mysql < dump.sql`), since the file cannot be checked
     - `TRUNCATE`
     - `ALTER TABLE … DROP`
     - `DELETE FROM <name>` with no `WHERE` clause before `;` or command end
     - `UPDATE <name> SET …` with no `WHERE` clause before `;` or command end

6. **Dangerous deletions (`[rm-dangerous]`)**:
   - `rm` with recursive or force flags (`-r`, `-R`, `-f`, `--recursive`, `--force`, `-rf`) targeting root (`/`, `/*`), home (`~`, `~/`, `~/*`, `$HOME`, `${HOME}`), parent directory (`..`, `../…`), repository metadata (`.git`), or wildcard (`*` alone).

## What Stays Allowed

The guard is targeted and non-disruptive:
- Read operations and inspections: `git status`, `git diff`, `git log`, `git show`, `git blame`, `git grep`, `git ls-files`, `git rev-parse`, `git fetch`, `git branch` listing, `git stash list`, `git tag` listing (`-l`, `--sort`, `--points-at`, `--contains`).
- Read-only GitHub and cluster commands: `gh pr view|list|diff`, `gh release list|view|download`, `gh api` GETs, `kubectl apply --dry-run`, `terraform plan`, `helm template|list|status`.
- Exports: `pg_dump`, `mysqldump`.
- Build tools, test runners, and linters: `npm test`, `pytest`, `cargo build`, `eslint`, etc.
- Safe SQL: `psql -c "SELECT 1"`, `mysql -e "DELETE FROM t WHERE id = 1"`.
- Migration checks: `php artisan migrate:status`, `--pretend`, `rails db:migrate:status`, `alembic current|history`, `prisma migrate status`.
- Normal build cleanup: `rm -rf build/ dist/ node_modules/ ./tmp`.
- Local rsync: `rsync -a src/ dst/`.

## Why This Exists

In AgentFleet 2.2.0, fleet reviewer subagents have direct access to Bash and Edit tools to inspect, verify, and diagnose code in the working tree. Subagents operate autonomously with specialized lane prompts. To prevent subagents from inadvertently modifying git history, pushing commits, triggering production deployments, dropping database tables, or modifying remote repositories without the main agent's explicit review and direction, `lane-guard` provides deterministic, engine-level policy enforcement.

When a command is blocked, the subagent receives a denial result instructing it to report the command back to the main agent instead:

```
AgentFleet lane-guard blocked this <label> command in <type>. Fleet lanes may not commit, push, merge, deploy, publish, run migrations or destructive SQL, or revert the working tree. Report the command to the main agent instead; it decides whether to run it.
```

## Model Failover

Each lane in `~/.claude/fleet.json` has a model and a list of `fallbacks`. When a lane run fails with an API error 429 (quota), 5xx or 529 (overloaded), AgentFleet's prompt hook records that model as down in `~/.claude/agentfleet/outages.json`: for 60 minutes after a 429, 15 minutes otherwise, or until a later run on it succeeds.

While a lane's own model is down, `lane-guard` starts each new run of that lane on the first of its fallbacks that is not down, and adds `(failover: <model>)` to the task's description so you can see it. The prompt hook tells Claude to re-dispatch the failed task to the same lane. Only when every model of a lane is down does it name another lane instead.

It leaves a run alone when the caller chose a model, for forks and workflow agents, and for lanes without fallbacks. A run on a model the caller chose is not counted against the lane's own models, whether it fails or succeeds. A project pin (`<project>/.claude/fleet.local.json`, written by `agentfleet project pin`) is laid over the lane's global row, so a pin of only the model keeps the global fallbacks.

It reads only `fleet.json`, the project's `fleet.local.json` and `agentfleet/outages.json`. It skips any of these files whose path goes through a symlink, so if your Claude folder or project is reached through one, failover (or the pin) may be skipped and runs start on their own model as before.

## Turn Limit Warnings

A fleet lane that reaches the `maxTurns` in its agent file is cut off without reporting, and its work is lost. `lane-guard` counts each fleet lane's tool calls and, after a call returns, adds a note the lane reads after the tool result (you do not see it):

- At 70% of its turns (rounded up), once: finish the part it is on, run its check, and report what is done, what remains, and the files it changed, without starting another part.
- At 90% of its turns, once: report now, even if unfinished.

The limit is the `maxTurns` in `<claude folder>/agents/<type>.md`, read once per lane type. Parallel calls in one turn each count, so a note may come a turn early. Blocked calls count but get no note. No note is added when the limit cannot be read (a missing file, no `maxTurns`, or a path through a symlink), and the main conversation and agents that are not fleet lanes are never counted. A failure while working out a note returns the tool's result as it was.

## The Main Conversation

By default `lane-guard` only guards subagents where `event.agentId` is present and whose agent type starts with `fleet-`. Commands executed in the main conversation (where you interact directly with Claude Code) are never blocked. When a subagent's type cannot be resolved, a matching command is blocked rather than allowed. The `PowerShell` tool is checked like `Bash`, the script passed to `powershell -Command` or `pwsh -c` is checked like `bash -c` (from 1.5.0), and secrets in the logged command are redacted.

### Main-session guard (from 1.5.0, off by default)

`agentfleet config main-session-guard on` writes `mainSessionGuard: true` to `<claude folder>/agentfleet/config.json`. lane-guard then asks you, with Claude Code's own question dialog, before the main conversation or a subagent that is not a fleet lane runs a command that discards work or data, or publishes. It is meant for sessions that skip permission prompts, where nothing else checks those commands.

The main conversation may commit, push, open pull requests and switch branches, so it is checked with a narrower rule set than the lanes:

- `[git-discard]`: a force push (`--force`, `-f`, `--force-with-lease`, a `+` refspec) or a push that deletes (`--delete`, `--mirror`, `--prune`, a `:` refspec), `reset --hard`, `clean` without `-n`, `checkout` of files (`--`, `.`, `-f`, `--ours`/`--theirs`, a commit followed by paths, or an argument that looks like a path: `./`, `../`, absolute, a glob or a file extension; a bare name such as `Makefile` is taken as a branch), `restore` of working-tree files, `switch --discard-changes`/`-f`, `branch -D`/`-f`/`-M`/`-C`, `stash drop|clear`, `reflog expire|delete`, `update-ref -d`, `worktree remove --force`, `filter-branch`, `filter-repo`.
- `[github-delete]`: `gh repo delete|archive`, `gh release delete|delete-asset`, `gh issue delete`, `gh api -X DELETE`.
- `[publish-deploy]`, `[migration]`, `[db-write]` and `[rm-dangerous]`, as for lanes. Context Mode tools are allowed, but SQL passed to any MCP tool is still scanned.

Only the answer **Run it** runs the command. **Don't run it**, text typed under Other, a dismissed question, or a session with nobody to ask (`claude -p`) stops it with a message that tells Claude why. The setting is read only when a command matches, so other calls cost nothing; it is off when lane-guard is not installed by AgentFleet or the settings file is missing or unreadable.

The rules are tested against a shared case table (`tests/cases.json`) that AgentFleet's Python lane reports use too, so both agree on what counts as forbidden.

## Installation

Install from Claude Code:

```text
/plugin install lane-guard --marketplace wecansync/agentfleet
```

Or via the command line:

```sh
claude plugin install lane-guard@agentfleet
```

## How to Turn It Off

If you need to disable `lane-guard`:
1. In Claude Code, run `/plugin`
2. Select `lane-guard`
3. Choose `Disable`

Or run Claude Code with the plugin disabled via settings or plugin arguments.

Turning it off also turns off model failover (when a lane's model fails, AgentFleet then names another lane instead) and the turn limit warnings.
