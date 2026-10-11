# lane-guard

Claude Code mod for AgentFleet that guards fleet subagent lanes from running destructive or publishing commands.

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

## Main Conversation Is Never Blocked

`lane-guard` only guards subagents where `event.agentId` is present and whose agent type starts with `fleet-`. Commands executed in the main conversation (where you interact directly with Claude Code) are never blocked. When a subagent's type cannot be resolved, a matching command is blocked rather than allowed. The `PowerShell` tool is checked like `Bash`, and secrets in the logged command are redacted.

The rules are tested against a shared case table (`tests/cases.json`) that AgentFleet's Python lane reports use too, so both agree on what counts as forbidden.

## Installation

Install from Claude Code:

```text
/plugin install lane-guard --marketplace wecansync/claude-agents-config
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
