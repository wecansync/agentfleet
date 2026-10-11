export type ForbiddenResult = {
  rule: string;
  label: string;
};

const RULES = {
  GIT_HISTORY: { rule: 'git-history', label: 'git history or working-tree' },
  GITHUB_WRITE: { rule: 'github-write', label: 'GitHub write' },
  PUBLISH_DEPLOY: { rule: 'publish-deploy', label: 'publish or deploy' },
  MIGRATION: { rule: 'migration', label: 'database migration' },
  DB_WRITE: { rule: 'db-write', label: 'destructive SQL' },
  RM_DANGEROUS: { rule: 'rm-dangerous', label: 'dangerous rm' },
} as const;

/**
 * Extracts subshells $(...) and backticks `...` from a command string.
 */
function extractSubshellsAndBackticks(command: string): string[] {
  const extracted: string[] = [];
  let i = 0;
  const n = command.length;

  while (i < n) {
    const ch = command[i];
    if (ch === '\\') {
      i += 2;
      continue;
    }
    if (ch === "'") {
      i++;
      while (i < n && command[i] !== "'") i++;
      if (i < n) i++;
      continue;
    }
    if (ch === '"') {
      i++;
      while (i < n && command[i] !== '"') {
        if (command[i] === '\\') {
          i += 2;
        } else if (command[i] === '$' && command[i + 1] === '(') {
          const start = i + 2;
          let depth = 1;
          i += 2;
          while (i < n && depth > 0) {
            if (command[i] === '\\') {
              i += 2;
            } else if (command[i] === '(') {
              depth++;
              i++;
            } else if (command[i] === ')') {
              depth--;
              i++;
            } else if (command[i] === "'") {
              i++;
              while (i < n && command[i] !== "'") i++;
              if (i < n) i++;
            } else if (command[i] === '"') {
              i++;
              while (i < n && command[i] !== '"') {
                if (command[i] === '\\') i += 2;
                else i++;
              }
              if (i < n) i++;
            } else {
              i++;
            }
          }
          extracted.push(command.slice(start, depth === 0 ? i - 1 : i));
        } else if (command[i] === '`') {
          const start = i + 1;
          i++;
          while (i < n && command[i] !== '`') {
            if (command[i] === '\\') i += 2;
            else i++;
          }
          extracted.push(command.slice(start, i));
          if (i < n) i++;
        } else {
          i++;
        }
      }
      if (i < n) i++;
      continue;
    }
    if (ch === '$' && command[i + 1] === '(') {
      const start = i + 2;
      let depth = 1;
      i += 2;
      while (i < n && depth > 0) {
        if (command[i] === '\\') {
          i += 2;
        } else if (command[i] === '(') {
          depth++;
          i++;
        } else if (command[i] === ')') {
          depth--;
          i++;
        } else if (command[i] === "'") {
          i++;
          while (i < n && command[i] !== "'") i++;
          if (i < n) i++;
        } else if (command[i] === '"') {
          i++;
          while (i < n && command[i] !== '"') {
            if (command[i] === '\\') i += 2;
            else i++;
          }
          if (i < n) i++;
        } else {
          i++;
        }
      }
      extracted.push(command.slice(start, depth === 0 ? i - 1 : i));
      continue;
    }
    if (ch === '`') {
      const start = i + 1;
      i++;
      while (i < n && command[i] !== '`') {
        if (command[i] === '\\') i += 2;
        else i++;
      }
      extracted.push(command.slice(start, i));
      if (i < n) i++;
      continue;
    }
    i++;
  }
  return extracted;
}

/**
 * Splits a command string into top-level segments on ;, &&, ||, |, &, \n.
 */
function splitIntoSegments(command: string): string[] {
  const segments: string[] = [];
  let current = '';
  let i = 0;
  const n = command.length;

  while (i < n) {
    const ch = command[i];
    if (ch === '\\') {
      current += command.slice(i, i + 2);
      i += 2;
      continue;
    }
    if (ch === "'") {
      const start = i;
      i++;
      while (i < n && command[i] !== "'") i++;
      if (i < n) i++;
      current += command.slice(start, i);
      continue;
    }
    if (ch === '"') {
      const start = i;
      i++;
      while (i < n && command[i] !== '"') {
        if (command[i] === '\\') i += 2;
        else i++;
      }
      if (i < n) i++;
      current += command.slice(start, i);
      continue;
    }
    if (ch === '$' && command[i + 1] === '(') {
      const start = i;
      let depth = 1;
      i += 2;
      while (i < n && depth > 0) {
        if (command[i] === '\\') i += 2;
        else if (command[i] === '(') { depth++; i++; }
        else if (command[i] === ')') { depth--; i++; }
        else if (command[i] === "'") {
          i++;
          while (i < n && command[i] !== "'") i++;
          if (i < n) i++;
        } else if (command[i] === '"') {
          i++;
          while (i < n && command[i] !== '"') {
            if (command[i] === '\\') i += 2;
            else i++;
          }
          if (i < n) i++;
        } else i++;
      }
      current += command.slice(start, i);
      continue;
    }
    if (ch === '`') {
      const start = i;
      i++;
      while (i < n && command[i] !== '`') {
        if (command[i] === '\\') i += 2;
        else i++;
      }
      if (i < n) i++;
      current += command.slice(start, i);
      continue;
    }

    if (ch === '&' && command[i + 1] === '&') {
      if (current.trim()) segments.push(current.trim());
      current = '';
      i += 2;
    } else if (ch === '|' && command[i + 1] === '|') {
      if (current.trim()) segments.push(current.trim());
      current = '';
      i += 2;
    } else if (ch === ';' || ch === '&' || ch === '|' || ch === '\n') {
      if (current.trim()) segments.push(current.trim());
      current = '';
      i++;
    } else {
      current += ch;
      i++;
    }
  }

  if (current.trim()) segments.push(current.trim());
  return segments;
}

/**
 * Tokenizes a segment into arguments while stripping quotes.
 */
function tokenizeSegment(segment: string): string[] {
  const words: string[] = [];
  let current = '';
  let inWord = false;
  let i = 0;
  const n = segment.length;

  while (i < n) {
    const ch = segment[i];
    if (/\s/.test(ch)) {
      if (inWord) {
        words.push(current);
        current = '';
        inWord = false;
      }
      i++;
      continue;
    }
    if (ch === '\\') {
      inWord = true;
      if (i + 1 < n) current += segment[i + 1];
      i += 2;
      continue;
    }
    if (ch === "'") {
      inWord = true;
      i++;
      while (i < n && segment[i] !== "'") {
        current += segment[i];
        i++;
      }
      if (i < n) i++;
      continue;
    }
    if (ch === '"') {
      inWord = true;
      i++;
      while (i < n && segment[i] !== '"') {
        if (segment[i] === '\\' && i + 1 < n) {
          current += segment[i + 1];
          i += 2;
        } else {
          current += segment[i];
          i++;
        }
      }
      if (i < n) i++;
      continue;
    }
    inWord = true;
    current += ch;
    i++;
  }
  if (inWord) {
    words.push(current);
  }
  return words;
}

/**
 * Unwraps leading VAR=value assignments and wrappers (sudo, command, env, time, nohup, exec).
 */
function unwrapTokens(words: string[]): { program: string; args: string[] } | null {
  let idx = 0;

  while (idx < words.length) {
    const token = words[idx];

    // Leading VAR=value
    if (/^[A-Za-z_][A-Za-z0-9_]*=/.test(token)) {
      idx++;
      continue;
    }

    if (token === 'sudo') {
      idx++;
      while (idx < words.length && words[idx].startsWith('-')) {
        const flag = words[idx];
        idx++;
        if (['-u', '-g', '-p', '-c', '-a', '-C', '-D', '-R', '-t', '-U', '-r', '-h'].includes(flag)) {
          if (idx < words.length && !words[idx].startsWith('-')) idx++;
        }
      }
      continue;
    }

    if (token === 'env') {
      idx++;
      while (idx < words.length) {
        if (words[idx].startsWith('-')) {
          const flag = words[idx];
          idx++;
          if (['-u'].includes(flag)) {
            if (idx < words.length && !words[idx].startsWith('-')) idx++;
          }
        } else if (/^[A-Za-z_][A-Za-z0-9_]*=/.test(words[idx])) {
          idx++;
        } else {
          break;
        }
      }
      continue;
    }

    if (token === 'command') {
      idx++;
      while (idx < words.length && words[idx].startsWith('-')) idx++;
      continue;
    }

    if (token === 'time') {
      idx++;
      while (idx < words.length && words[idx].startsWith('-')) {
        const flag = words[idx];
        idx++;
        if (['-o'].includes(flag)) {
          if (idx < words.length && !words[idx].startsWith('-')) idx++;
        }
      }
      continue;
    }

    if (token === 'nohup') {
      idx++;
      while (idx < words.length && words[idx].startsWith('-')) idx++;
      continue;
    }

    if (token === 'exec') {
      idx++;
      while (idx < words.length && words[idx].startsWith('-')) {
        const flag = words[idx];
        idx++;
        if (['-a'].includes(flag)) {
          if (idx < words.length && !words[idx].startsWith('-')) idx++;
        }
      }
      continue;
    }

    break;
  }

  if (idx >= words.length) return null;
  return { program: words[idx], args: words.slice(idx + 1) };
}

/**
 * Checks for destructive SQL statements:
 * - DROP (TABLE|DATABASE|SCHEMA|INDEX|VIEW)
 * - TRUNCATE
 * - ALTER TABLE … DROP
 * - DELETE FROM <name> with no WHERE before the next ; or end
 * - UPDATE <name> SET … with no WHERE before the next ; or end
 */
function checkDestructiveSql(text: string): ForbiddenResult | null {
  if (/\bDROP\s+(?:TABLE|DATABASE|SCHEMA|INDEX|VIEW)\b/i.test(text)) {
    return RULES.DB_WRITE;
  }
  if (/\bTRUNCATE\b/i.test(text)) {
    return RULES.DB_WRITE;
  }
  if (/\bALTER\s+TABLE\b[\s\S]*?\bDROP\b/i.test(text)) {
    return RULES.DB_WRITE;
  }

  const deleteMatches = text.matchAll(/\bDELETE\s+FROM\s+([^\s;]+)([\s\S]*?)(?:;|$)/gi);
  for (const match of deleteMatches) {
    const clause = match[2];
    if (!/\bWHERE\b/i.test(clause)) {
      return RULES.DB_WRITE;
    }
  }

  const updateMatches = text.matchAll(/\bUPDATE\s+([^\s;]+)\s+SET\b([\s\S]*?)(?:;|$)/gi);
  for (const match of updateMatches) {
    const clause = match[2];
    if (!/\bWHERE\b/i.test(clause)) {
      return RULES.DB_WRITE;
    }
  }

  return null;
}

function isDatabaseClient(program: string, args: string[]): boolean {
  const base = program.split('/').pop() || program;
  if (['mysql', 'mariadb', 'psql', 'sqlite3', 'sqlcmd', 'clickhouse-client', 'duckdb'].includes(base)) {
    return true;
  }
  if (base === 'cockroach' && args[0] === 'sql') {
    return true;
  }
  if (base === 'wp' && args[0] === 'db' && args[1] === 'query') {
    return true;
  }
  return false;
}

function isRemoteHostPath(arg: string): boolean {
  if (arg.startsWith('-')) return false;
  // Exclude Windows drives (C:\x, C:/x, and C:x once a shell has eaten the backslash).
  if (/^[A-Za-z]:/.test(arg)) return false;
  const colonIdx = arg.indexOf(':');
  if (colonIdx <= 0) return false;
  const slashIdx = arg.indexOf('/');
  return slashIdx === -1 || colonIdx < slashIdx;
}

function checkSegment(program: string, args: string[]): ForbiddenResult | null {
  const base = program.split('/').pop() || program;

  // [git-history]
  if (base === 'git') {
    let gIdx = 0;
    while (gIdx < args.length) {
      const a = args[gIdx];
      if (a === '-C' || a === '-c' || a === '--git-dir' || a === '--work-tree') {
        gIdx += 2;
      } else if (
        a.startsWith('-C') ||
        a.startsWith('-c') ||
        a.startsWith('--git-dir=') ||
        a.startsWith('--work-tree=')
      ) {
        gIdx += 1;
      } else {
        break;
      }
    }
    if (gIdx >= args.length) return null;
    const subcmd = args[gIdx];
    const subargs = args.slice(gIdx + 1);

    if (
      [
        'commit',
        'push',
        'merge',
        'rebase',
        'reset',
        'restore',
        'checkout',
        'switch',
        'clean',
        'cherry-pick',
        'revert',
        'am',
        'update-ref',
        'filter-branch',
      ].includes(subcmd)
    ) {
      return RULES.GIT_HISTORY;
    }

    if (subcmd === 'tag') {
      if (subargs.length === 0) return null; // bare git tag
      if (subargs.some((a) => a === '-d' || a === '-D' || a === '-f' || a === '--delete' || a === '--force')) {
        return RULES.GIT_HISTORY;
      }
      if (subargs.some((a) => a === '-l' || a === '--list' || a.startsWith('-l') || a.startsWith('--list='))) {
        return null;
      }
      // Any creation or other tag command
      return RULES.GIT_HISTORY;
    }

    if (subcmd === 'stash') {
      if (subargs[0] === 'list' || subargs[0] === 'show') return null;
      return RULES.GIT_HISTORY;
    }

    if (subcmd === 'branch') {
      if (
        subargs.some(
          (a) =>
            a === '-d' ||
            a === '-D' ||
            a === '-m' ||
            a === '-M' ||
            a === '-f' ||
            a === '--delete' ||
            a === '--move' ||
            a.startsWith('--delete=') ||
            a.startsWith('--move=') ||
            (/^-.*[dDmMf]/.test(a) && !a.startsWith('--')),
        )
      ) {
        return RULES.GIT_HISTORY;
      }
      return null;
    }

    if (subcmd === 'worktree') {
      if (subargs[0] === 'remove' || subargs[0] === 'prune') {
        return RULES.GIT_HISTORY;
      }
      return null;
    }
  }

  // [github-write]
  if (base === 'gh') {
    const subcmd = args[0];
    const subargs = args.slice(1);

    if (subcmd === 'pr') {
      if (['create', 'merge', 'close', 'edit', 'comment', 'review', 'ready', 'reopen'].includes(subargs[0])) {
        return RULES.GITHUB_WRITE;
      }
    } else if (subcmd === 'release') {
      return RULES.GITHUB_WRITE;
    } else if (subcmd === 'repo') {
      if (['create', 'delete', 'edit', 'rename', 'archive', 'fork'].includes(subargs[0])) {
        return RULES.GITHUB_WRITE;
      }
    } else if (subcmd === 'workflow') {
      if (['run', 'enable', 'disable'].includes(subargs[0])) {
        return RULES.GITHUB_WRITE;
      }
    } else if (subcmd === 'issue') {
      if (['create', 'close', 'edit', 'comment', 'delete', 'reopen'].includes(subargs[0])) {
        return RULES.GITHUB_WRITE;
      }
    } else if (subcmd === 'api') {
      for (let i = 0; i < subargs.length; i++) {
        const a = subargs[i];
        if (a === '-X' || a === '--method') {
          const method = subargs[i + 1]?.toUpperCase();
          if (['POST', 'PUT', 'PATCH', 'DELETE'].includes(method)) {
            return RULES.GITHUB_WRITE;
          }
        } else if (a.toUpperCase().startsWith('-X')) {
          const method = a.slice(2).toUpperCase();
          if (['POST', 'PUT', 'PATCH', 'DELETE'].includes(method)) {
            return RULES.GITHUB_WRITE;
          }
        } else if (a.toUpperCase().startsWith('--METHOD=')) {
          const method = a.slice(9).toUpperCase();
          if (['POST', 'PUT', 'PATCH', 'DELETE'].includes(method)) {
            return RULES.GITHUB_WRITE;
          }
        }
      }
    }
  }

  // [publish-deploy]
  if (['npm', 'pnpm', 'yarn', 'bun'].includes(base) && args[0] === 'publish') {
    return RULES.PUBLISH_DEPLOY;
  }
  if (base === 'twine' && args[0] === 'upload') {
    return RULES.PUBLISH_DEPLOY;
  }
  if (base === 'cargo' && args[0] === 'publish') {
    return RULES.PUBLISH_DEPLOY;
  }
  if (base === 'gem' && args[0] === 'push') {
    return RULES.PUBLISH_DEPLOY;
  }
  if (base === 'docker' && args[0] === 'push') {
    return RULES.PUBLISH_DEPLOY;
  }
  if (base === 'kubectl' && ['apply', 'delete', 'rollout', 'scale', 'patch', 'replace'].includes(args[0])) {
    return RULES.PUBLISH_DEPLOY;
  }
  if (base === 'helm' && ['install', 'upgrade', 'uninstall', 'rollback'].includes(args[0])) {
    return RULES.PUBLISH_DEPLOY;
  }
  if (base === 'terraform' && ['apply', 'destroy'].includes(args[0])) {
    return RULES.PUBLISH_DEPLOY;
  }
  if (base === 'vercel' && args.some((a) => a === '--prod' || a === 'deploy')) {
    return RULES.PUBLISH_DEPLOY;
  }
  if (base === 'firebase' && args[0] === 'deploy') {
    return RULES.PUBLISH_DEPLOY;
  }
  if (['fly', 'flyctl'].includes(base) && args[0] === 'deploy') {
    return RULES.PUBLISH_DEPLOY;
  }
  if (program.endsWith('deploy.sh')) {
    return RULES.PUBLISH_DEPLOY;
  }
  if (['rsync', 'scp'].includes(base) && args.some(isRemoteHostPath)) {
    return RULES.PUBLISH_DEPLOY;
  }

  // [migration]
  let artisanArgs: string[] | null = null;
  if (base === 'php' && args[0] === 'artisan') {
    artisanArgs = args.slice(1);
  } else if (base === 'artisan') {
    artisanArgs = args;
  }
  if (artisanArgs && artisanArgs.length > 0) {
    if (artisanArgs[0].startsWith('migrate')) {
      if (artisanArgs[0] === 'migrate:status' || artisanArgs.includes('--pretend')) {
        // Allowed
      } else {
        return RULES.MIGRATION;
      }
    } else if (artisanArgs[0] === 'db:wipe') {
      return RULES.MIGRATION;
    }
  }

  if (['rails', 'rake'].includes(base) && args[0]) {
    if (/^db:(migrate|drop|reset|rollback|setup|schema:load)/.test(args[0])) {
      return RULES.MIGRATION;
    }
  }

  let prismaArgs: string[] | null = null;
  if (base === 'prisma') {
    prismaArgs = args;
  } else if (['npx', 'bunx', 'yarn', 'pnpm'].includes(base) && args[0] === 'prisma') {
    prismaArgs = args.slice(1);
  }
  if (prismaArgs && prismaArgs.length > 0) {
    if (prismaArgs[0] === 'migrate' && ['dev', 'deploy', 'reset'].includes(prismaArgs[1])) {
      return RULES.MIGRATION;
    }
    if (prismaArgs[0] === 'db' && prismaArgs[1] === 'push') {
      return RULES.MIGRATION;
    }
  }

  if (base === 'alembic' && ['upgrade', 'downgrade'].includes(args[0])) {
    return RULES.MIGRATION;
  }

  if (program.endsWith('manage.py') && ['migrate', 'flush'].includes(args[0])) {
    return RULES.MIGRATION;
  }
  if (['python', 'python3', 'pypy'].includes(base) && args[0]?.endsWith('manage.py') && ['migrate', 'flush'].includes(args[1])) {
    return RULES.MIGRATION;
  }

  let knexArgs: string[] | null = null;
  if (base === 'knex') {
    knexArgs = args;
  } else if (['npx', 'bunx', 'yarn', 'pnpm'].includes(base) && args[0] === 'knex') {
    knexArgs = args.slice(1);
  }
  if (knexArgs && knexArgs[0]?.startsWith('migrate:')) {
    return RULES.MIGRATION;
  }

  let seqArgs: string[] | null = null;
  if (base === 'sequelize' || base === 'sequelize-cli') {
    seqArgs = args;
  } else if (['npx', 'bunx', 'yarn', 'pnpm'].includes(base) && args[0] === 'sequelize') {
    seqArgs = args.slice(1);
  }
  if (seqArgs && seqArgs[0]?.startsWith('db:migrate')) {
    return RULES.MIGRATION;
  }

  if (base === 'diesel' && args[0] === 'migration' && ['run', 'revert', 'redo'].includes(args[1])) {
    return RULES.MIGRATION;
  }
  if (base === 'goose' && ['up', 'down', 'reset'].includes(args[0])) {
    return RULES.MIGRATION;
  }
  if (base === 'flyway' && ['migrate', 'clean'].includes(args[0])) {
    return RULES.MIGRATION;
  }
  if (base === 'dropdb') {
    return RULES.MIGRATION;
  }
  if (base === 'mysqladmin' && args.includes('drop')) {
    return RULES.MIGRATION;
  }
  if (base === 'redis-cli' && args.some((a) => ['flushall', 'flushdb'].includes(a.toLowerCase()))) {
    return RULES.MIGRATION;
  }

  // [rm-dangerous]
  if (base === 'rm') {
    const hasRecursiveOrForce = args.some((a) => {
      if (a === '--recursive' || a === '--force') return true;
      if (a.startsWith('-') && !a.startsWith('--')) {
        return /[rRf]/.test(a);
      }
      return false;
    });

    if (hasRecursiveOrForce) {
      const targets = args.filter((a) => !a.startsWith('-'));
      for (const target of targets) {
        if (
          target === '/' ||
          target === '/*' ||
          target === '~' ||
          target === '~/' ||
          target === '~/*' ||
          target === '$HOME' ||
          target === '${HOME}' ||
          target === '..' ||
          target === '../' ||
          target.startsWith('../') ||
          target === '.git' ||
          target === '.git/' ||
          target.startsWith('.git/') ||
          target === '*'
        ) {
          return RULES.RM_DANGEROUS;
        }
      }
    }
  }

  return null;
}

/**
 * Checks a shell command string against all forbidden lane rules.
 */
export function forbiddenReason(command: string): ForbiddenResult | null {
  if (!command || typeof command !== 'string') return null;

  // 1. Scan text inside $( ... ) and backticks
  const subshells = extractSubshellsAndBackticks(command);
  for (const sub of subshells) {
    const reason = forbiddenReason(sub);
    if (reason) return reason;
  }

  // 2. Split into segments on ;, &&, ||, |, &, \n
  const segments = splitIntoSegments(command);

  // 3. Check if any segment's program is a database client
  let hasDbClient = false;
  for (const seg of segments) {
    const tokens = tokenizeSegment(seg);
    const unwrapped = unwrapTokens(tokens);
    if (unwrapped && isDatabaseClient(unwrapped.program, unwrapped.args)) {
      hasDbClient = true;
      break;
    }
  }

  // 4. If a database client was found, scan the whole command text (including heredocs) for destructive SQL
  if (hasDbClient) {
    const sqlReason = checkDestructiveSql(command);
    if (sqlReason) return sqlReason;
  }

  // 5. Evaluate each segment against all rules
  for (const seg of segments) {
    const tokens = tokenizeSegment(seg);
    const unwrapped = unwrapTokens(tokens);
    if (!unwrapped) continue;

    const { program, args } = unwrapped;
    const base = program.split('/').pop() || program;

    // Scan string argument of bash -c / sh -c / zsh -c
    if (['bash', 'sh', 'zsh'].includes(base)) {
      for (let i = 0; i < args.length; i++) {
        const a = args[i];
        if ((a === '-c' || a === '-lc' || /^-.*c$/.test(a)) && i + 1 < args.length) {
          const innerReason = forbiddenReason(args[i + 1]);
          if (innerReason) return innerReason;
        }
      }
    }

    const reason = checkSegment(program, args);
    if (reason) return reason;
  }

  return null;
}

/**
 * Checks MCP arguments (recursively, bounded) for [migration] and [db-write] SQL rules.
 */
export function mcpForbidden(args: unknown): ForbiddenResult | null {
  let stringsChecked = 0;
  const MAX_STRINGS = 500;
  const MAX_DEPTH = 10;

  function scan(val: unknown, depth: number): ForbiddenResult | null {
    if (depth > MAX_DEPTH || stringsChecked >= MAX_STRINGS) return null;
    if (typeof val === 'string') {
      stringsChecked++;
      // Check destructive SQL directly (no client check needed)
      const sqlReason = checkDestructiveSql(val);
      if (sqlReason) return sqlReason;

      // Check migration rules
      const reason = forbiddenReason(val);
      if (reason && (reason.rule === 'migration' || reason.rule === 'db-write')) {
        return reason;
      }
      return null;
    }
    if (Array.isArray(val)) {
      for (const item of val) {
        const res = scan(item, depth + 1);
        if (res) return res;
      }
    } else if (val !== null && typeof val === 'object') {
      for (const item of Object.values(val as Record<string, unknown>)) {
        const res = scan(item, depth + 1);
        if (res) return res;
      }
    }
    return null;
  }

  return scan(args, 0);
}
