export type ForbiddenResult = {
  rule: string;
  label: string;
  // Replaces the default closing sentence of the denial, when a rule needs to explain an alternative.
  hint?: string;
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
 * Pure function to redact secrets from text (for $.ui.log and transcripts).
 */
export function redactSecrets(text: string): string {
  if (!text || typeof text !== 'string') return '';
  // Redact URL credentials: scheme://user:pass@ -> scheme://user:[redacted]@
  let result = text.replace(
    /([A-Za-z][A-Za-z0-9+.-]*:\/\/[^\/\s:@]+:)[^\/\s@]+(@)/g,
    '$1[redacted]$2',
  );
  // Redact credentials, API keys, tokens, and passwords
  result = result.replace(
    /(?:sk-[A-Za-z0-9_-]{8,}|gh[pousr]_[A-Za-z0-9]{8,}|xox[baprs]-[A-Za-z0-9-]{8,}|github_pat_[A-Za-z0-9_]{8,}|[sr]k_(?:live|test)_[A-Za-z0-9]{8,}|(?<=Bearer\s)\S+|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}|AKIA[0-9A-Z]{12,}|[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}|\b(?:\d[ -]*?){13,19}\b|(?<=--password=)\S+|(?<=--password\s)\S+|(?<=\s-p)\S+|(?<=MYSQL_PWD=)\S+|(?<=PGPASSWORD=)\S+|(?<=AWS_SECRET_ACCESS_KEY=)\S+|(?<![A-Za-z0-9_-])(?:password|passwd|secret|token|api[_-]?key|authorization)\s*[=:]\s*\S+)/gi,
    '[redacted]',
  );
  return result;
}

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

const SHELL_KEYWORDS = new Set([
  'do', 'then', 'else', 'elif', 'if', 'while', 'until', 'time', 'fi', 'done',
]);

/**
 * Unwraps leading syntax, shell keywords, VAR=value assignments, and runner wrappers.
 */
function unwrapTokens(rawWords: string[]): { program: string; args: string[] } | null {
  // Strip leading (, {, ! and trailing ), } from tokens (preserving ${VAR} expansions)
  const words: string[] = [];
  for (const w of rawWords) {
    let cleaned = w;
    if (!w.startsWith('${') && !w.startsWith('$(')) {
      cleaned = cleaned.replace(/^[({!]+/, '').replace(/[)}]+$/, '');
    }
    if (cleaned.length > 0) {
      words.push(cleaned);
    }
  }

  let idx = 0;
  let changed = true;

  while (idx < words.length && changed) {
    changed = false;
    const token = words[idx];

    // Shell keywords
    if (SHELL_KEYWORDS.has(token)) {
      idx++;
      changed = true;
      continue;
    }

    // Leading VAR=value
    if (/^[A-Za-z_][A-Za-z0-9_]*=/.test(token)) {
      idx++;
      changed = true;
      continue;
    }

    const base = token.split('/').pop() || token;

    if (base === 'sudo' || base === 'doas') {
      idx++;
      const valOpts = new Set([
        '-u', '-g', '-p', '-c', '-a', '-C', '-D', '-R', '-t', '-U', '-r', '-h', '-T',
      ]);
      while (idx < words.length && words[idx].startsWith('-')) {
        const flag = words[idx];
        idx++;
        if (flag === '--') break;
        if (valOpts.has(flag) && idx < words.length && !words[idx].startsWith('-')) {
          idx++;
        }
      }
      changed = true;
      continue;
    }

    if (base === 'env') {
      idx++;
      while (idx < words.length) {
        const w = words[idx];
        if (w === '--') {
          idx++;
          break;
        }
        if (w === '-i' || w === '-0') {
          idx++;
        } else if (w === '-u' || w === '--unset' || w === '-C' || w === '--chdir') {
          idx += 2;
        } else if (w.startsWith('-')) {
          idx++;
        } else if (/^[A-Za-z_][A-Za-z0-9_]*=/.test(w)) {
          idx++;
        } else {
          break;
        }
      }
      changed = true;
      continue;
    }

    if (base === 'command' || base === 'nohup') {
      idx++;
      while (idx < words.length && words[idx].startsWith('-') && words[idx] !== '--') idx++;
      if (idx < words.length && words[idx] === '--') idx++;
      changed = true;
      continue;
    }

    if (base === 'exec') {
      idx++;
      while (idx < words.length && words[idx].startsWith('-')) {
        const flag = words[idx];
        idx++;
        if (flag === '--') break;
        if (flag === '-a' && idx < words.length && !words[idx].startsWith('-')) {
          idx++;
        }
      }
      changed = true;
      continue;
    }

    if (base === 'time') {
      idx++;
      while (idx < words.length && words[idx].startsWith('-')) {
        const flag = words[idx];
        idx++;
        if (flag === '--') break;
        if (flag === '-o' && idx < words.length && !words[idx].startsWith('-')) {
          idx++;
        }
      }
      changed = true;
      continue;
    }

    if (base === 'nice') {
      idx++;
      while (idx < words.length && words[idx].startsWith('-')) {
        const flag = words[idx];
        idx++;
        if (flag === '--') break;
        if (flag === '-n' && idx < words.length) {
          idx++;
        }
      }
      changed = true;
      continue;
    }

    if (base === 'ionice') {
      idx++;
      while (idx < words.length && words[idx].startsWith('-')) {
        const flag = words[idx];
        idx++;
        if (flag === '--') break;
        if (['-c', '-n', '-p'].includes(flag) && idx < words.length && !words[idx].startsWith('-')) {
          idx++;
        }
      }
      changed = true;
      continue;
    }

    if (base === 'stdbuf') {
      idx++;
      while (idx < words.length && words[idx].startsWith('-')) {
        const flag = words[idx];
        idx++;
        if (flag === '--') break;
        if (['-i', '-o', '-e'].includes(flag) && idx < words.length && !words[idx].startsWith('-')) {
          idx++;
        }
      }
      changed = true;
      continue;
    }

    if (base === 'timeout') {
      idx++;
      while (idx < words.length && words[idx].startsWith('-')) {
        const flag = words[idx];
        idx++;
        if (flag === '--') break;
        if (['-s', '-k', '--signal', '--kill-after'].includes(flag) && idx < words.length && !words[idx].startsWith('-')) {
          idx++;
        }
      }
      // Consume DURATION argument
      if (idx < words.length && !words[idx].startsWith('-')) {
        idx++;
      }
      changed = true;
      continue;
    }

    if (base === 'xargs') {
      idx++;
      const valOpts = new Set([
        '-I', '-n', '-L', '-P', '-s', '-E', '-a', '-d', '-e',
        '--max-args', '--max-procs', '--max-lines', '--arg-file', '--delimiter',
      ]);
      while (idx < words.length && words[idx].startsWith('-')) {
        const flag = words[idx];
        idx++;
        if (flag === '--') break;
        if (valOpts.has(flag) && idx < words.length && !words[idx].startsWith('-')) {
          idx++;
        }
      }
      changed = true;
      continue;
    }

    if (base === 'find') {
      // Look for -exec or -execdir
      let execIdx = -1;
      for (let j = idx + 1; j < words.length; j++) {
        if (words[j] === '-exec' || words[j] === '-execdir') {
          execIdx = j + 1;
          break;
        }
      }
      if (execIdx !== -1 && execIdx < words.length) {
        idx = execIdx;
        changed = true;
        continue;
      }
    }

    if (base === 'bundle' && words[idx + 1] === 'exec') {
      idx += 2;
      while (idx < words.length && words[idx].startsWith('-')) idx++;
      changed = true;
      continue;
    }

    if ((base === 'uv' || base === 'poetry' || base === 'pipenv') && words[idx + 1] === 'run') {
      idx += 2;
      while (idx < words.length && words[idx].startsWith('-')) {
        const flag = words[idx];
        idx++;
        if (flag === '--') break;
      }
      changed = true;
      continue;
    }

    if (base === 'npx' || base === 'bunx') {
      idx++;
      while (idx < words.length && words[idx].startsWith('-')) {
        const flag = words[idx];
        idx++;
        if (flag === '--') break;
        if (['-p', '--package'].includes(flag) && idx < words.length && !words[idx].startsWith('-')) {
          idx++;
        }
      }
      changed = true;
      continue;
    }

    if (base === 'npm' && words[idx + 1] === 'exec') {
      idx += 2;
      while (idx < words.length && words[idx].startsWith('-') && words[idx] !== '--') idx++;
      if (idx < words.length && words[idx] === '--') idx++;
      changed = true;
      continue;
    }

    if (base === 'pnpm' && (words[idx + 1] === 'exec' || words[idx + 1] === 'dlx')) {
      idx += 2;
      while (idx < words.length && words[idx].startsWith('-') && words[idx] !== '--') idx++;
      if (idx < words.length && words[idx] === '--') idx++;
      changed = true;
      continue;
    }

    if (base === 'yarn' && words[idx + 1] === 'dlx') {
      idx += 2;
      while (idx < words.length && words[idx].startsWith('-') && words[idx] !== '--') idx++;
      if (idx < words.length && words[idx] === '--') idx++;
      changed = true;
      continue;
    }

    if (base === 'docker' || base === 'docker-compose') {
      let cur = idx + 1;
      if (base === 'docker' && words[cur] === 'compose') {
        cur++;
      }
      if (cur < words.length && (words[cur] === 'exec' || words[cur] === 'run')) {
        cur++;
        const valOpts = new Set([
          '-e', '-u', '-w', '-v', '--env', '--user', '--workdir', '--volume',
          '--entrypoint', '-p', '--publish', '--name',
        ]);
        while (cur < words.length && words[cur].startsWith('-')) {
          const flag = words[cur];
          cur++;
          if (flag === '--') break;
          if (valOpts.has(flag) && cur < words.length && !words[cur].startsWith('-')) {
            cur++;
          }
        }
        // Skip SERVICE
        if (cur < words.length && !words[cur].startsWith('-')) {
          cur++;
          idx = cur;
          changed = true;
          continue;
        }
      }
    }

    if (base === 'kubectl' && words[idx + 1] === 'exec') {
      let dashIdx = -1;
      for (let j = idx + 2; j < words.length; j++) {
        if (words[j] === '--') {
          dashIdx = j + 1;
          break;
        }
      }
      if (dashIdx !== -1 && dashIdx < words.length) {
        idx = dashIdx;
        changed = true;
        continue;
      }
    }

    if (base === 'sail') {
      idx++;
      changed = true;
      continue;
    }
  }

  if (idx >= words.length) return null;
  return { program: words[idx], args: words.slice(idx + 1) };
}

/**
 * Extracts first non-option subcommand and following args for CLI tools like kubectl, helm, terraform, docker.
 */
function extractSubcommand(args: string[]): { subcmd: string; subargs: string[] } {
  const valOpts = new Set([
    '-n', '--namespace', '--context', '--kubeconfig', '-s', '--server',
    '--cluster', '--user', '-chdir', '--kube-context', '-H', '--host',
  ]);
  let i = 0;
  while (i < args.length) {
    const a = args[i];
    if (a === '--') {
      i++;
      break;
    }
    if (valOpts.has(a)) {
      i += 2;
    } else if (
      a.startsWith('-n=') ||
      a.startsWith('--namespace=') ||
      a.startsWith('--context=') ||
      a.startsWith('--kubeconfig=') ||
      a.startsWith('-chdir=') ||
      a.startsWith('--kube-context=') ||
      a.startsWith('-H=') ||
      a.startsWith('--host=')
    ) {
      i += 1;
    } else if (a.startsWith('-')) {
      i += 1;
    } else {
      break;
    }
  }
  if (i >= args.length) return { subcmd: '', subargs: [] };
  return { subcmd: args[i], subargs: args.slice(i + 1) };
}

/**
 * Checks for destructive SQL statements:
 * - DROP (TABLE|DATABASE|SCHEMA|INDEX|VIEW|MATERIALIZED VIEW|FUNCTION|PROCEDURE|TRIGGER|ROLE|USER|SEQUENCE|TYPE|EXTENSION)
 * - TRUNCATE
 * - ALTER TABLE … DROP
 * - DELETE FROM <name> with no WHERE before the next ; or end
 * - UPDATE <name> SET … with no WHERE before the next ; or end
 */
function checkDestructiveSql(text: string): ForbiddenResult | null {
  if (
    /\bDROP\s+(?:TABLE|DATABASE|SCHEMA|INDEX|VIEW|MATERIALIZED\s+VIEW|FUNCTION|PROCEDURE|TRIGGER|ROLE|USER|SEQUENCE|TYPE|EXTENSION)\b/i.test(
      text,
    )
  ) {
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
      if (a === '--') {
        gIdx += 1;
        break;
      }
      if (
        [
          '-C', '-c', '--git-dir', '--work-tree', '--namespace',
          '--super-prefix', '--config-env',
        ].includes(a)
      ) {
        gIdx += 2;
      } else if (
        a.startsWith('-C') ||
        a.startsWith('-c') ||
        a.startsWith('--git-dir=') ||
        a.startsWith('--work-tree=') ||
        a.startsWith('--namespace=') ||
        a.startsWith('--super-prefix=') ||
        a.startsWith('--config-env=')
      ) {
        gIdx += 1;
      } else if (a.startsWith('-')) {
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
        'pull',
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

    if (subcmd === 'bisect') {
      if (
        subargs[0] === 'log' ||
        subargs[0] === 'visualize' ||
        subargs[0] === 'view'
      ) {
        return null;
      }
      return RULES.GIT_HISTORY;
    }

    if (subcmd === 'tag') {
      if (subargs.length === 0) return null; // bare git tag
      if (subargs.some((a) => ['-d', '-D', '-f', '--delete', '--force'].includes(a))) {
        return RULES.GIT_HISTORY;
      }
      if (
        subargs.some(
          (a) =>
            a === '-l' ||
            a === '--list' ||
            a.startsWith('-l') ||
            a.startsWith('--list=') ||
            a.startsWith('--sort') ||
            a.startsWith('--points-at') ||
            a.startsWith('--contains') ||
            /^-n\d*$/.test(a),
        )
      ) {
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
            a === '--force' ||
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
      if (subargs.length > 0 && !['list', 'view', 'download'].includes(subargs[0])) {
        return RULES.GITHUB_WRITE;
      }
    } else if (subcmd === 'repo') {
      if (['create', 'delete', 'edit', 'rename', 'archive', 'fork'].includes(subargs[0])) {
        return RULES.GITHUB_WRITE;
      }
    } else if (subcmd === 'workflow') {
      if (['run', 'enable', 'disable'].includes(subargs[0])) {
        return RULES.GITHUB_WRITE;
      }
    } else if (subcmd === 'run') {
      if (['rerun', 'cancel', 'delete'].includes(subargs[0])) {
        return RULES.GITHUB_WRITE;
      }
    } else if (subcmd === 'issue') {
      if (['create', 'close', 'edit', 'comment', 'delete', 'reopen'].includes(subargs[0])) {
        return RULES.GITHUB_WRITE;
      }
    } else if (subcmd === 'api') {
      let method = '';
      let hasFieldOrInput = false;
      for (let i = 0; i < subargs.length; i++) {
        const a = subargs[i];
        if (a === '-X' || a === '--method') {
          method = (subargs[i + 1] ?? '').toUpperCase();
          i++;
        } else if (a.startsWith('-X')) {
          method = a.slice(2).toUpperCase();
        } else if (a.toUpperCase().startsWith('--METHOD=')) {
          method = a.slice(9).toUpperCase();
        } else if (
          a === '-f' ||
          a === '-F' ||
          a === '--field' ||
          a === '--raw-field' ||
          a === '--input' ||
          a.startsWith('-f') ||
          a.startsWith('-F') ||
          a.startsWith('--field=') ||
          a.startsWith('--raw-field=') ||
          a.startsWith('--input=')
        ) {
          hasFieldOrInput = true;
        }
      }
      if (['POST', 'PUT', 'PATCH', 'DELETE'].includes(method)) {
        return RULES.GITHUB_WRITE;
      }
      if (!method && hasFieldOrInput) {
        return RULES.GITHUB_WRITE;
      }
      if (method && method !== 'GET' && hasFieldOrInput) {
        return RULES.GITHUB_WRITE;
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
  if (base === 'docker') {
    const { subcmd } = extractSubcommand(args);
    if (subcmd === 'push') {
      return RULES.PUBLISH_DEPLOY;
    }
  }
  if (base === 'kubectl') {
    const { subcmd } = extractSubcommand(args);
    if (['apply', 'delete', 'rollout', 'scale', 'patch', 'replace'].includes(subcmd)) {
      if (
        subcmd === 'apply' &&
        args.some((a) => a === '--dry-run' || a.startsWith('--dry-run='))
      ) {
        return null;
      }
      return RULES.PUBLISH_DEPLOY;
    }
  }
  if (base === 'helm') {
    const { subcmd } = extractSubcommand(args);
    if (['install', 'upgrade', 'uninstall', 'rollback'].includes(subcmd)) {
      return RULES.PUBLISH_DEPLOY;
    }
    if (['template', 'list', 'status'].includes(subcmd)) {
      return null;
    }
  }
  if (base === 'terraform') {
    const { subcmd } = extractSubcommand(args);
    if (['apply', 'destroy'].includes(subcmd)) {
      return RULES.PUBLISH_DEPLOY;
    }
    if (subcmd === 'plan') {
      return null;
    }
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
  if (
    ['bash', 'sh', 'zsh', 'source', '.'].includes(base) &&
    args.some((a) => a.endsWith('deploy.sh') || a.includes('/deploy.sh'))
  ) {
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
    if (args[0] === 'db:migrate:status' || args[0] === 'db:version') {
      return null;
    }
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

  if (base === 'alembic') {
    if (['upgrade', 'downgrade'].includes(args[0])) {
      return RULES.MIGRATION;
    }
    if (['current', 'history'].includes(args[0])) {
      return null;
    }
  }

  if (program.endsWith('manage.py') && ['migrate', 'flush'].includes(args[0])) {
    return RULES.MIGRATION;
  }
  if (
    ['python', 'python3', 'pypy'].includes(base) &&
    args[0]?.endsWith('manage.py') &&
    ['migrate', 'flush'].includes(args[1])
  ) {
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
  } else if (['npx', 'bunx', 'yarn', 'pnpm'].includes(base) && args[0]?.startsWith('sequelize')) {
    seqArgs = args.slice(1);
  }
  if (seqArgs) {
    if (seqArgs[0]?.startsWith('db:migrate')) {
      return RULES.MIGRATION;
    }
    if (seqArgs[0] === 'db' && seqArgs[1]?.startsWith('migrate')) {
      return RULES.MIGRATION;
    }
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

  // Drop backslash-newline continuations
  command = command.replace(/\\\r?\n/g, '');

  // 1. Scan text inside $( ... ) and backticks
  const subshells = extractSubshellsAndBackticks(command);
  for (const sub of subshells) {
    const reason = forbiddenReason(sub);
    if (reason) return reason;
  }

  // 2. Split into segments on ;, &&, ||, |, &, \n
  const segments = splitIntoSegments(command);

  // 3. Evaluate each segment against all rules
  for (const seg of segments) {
    const tokens = tokenizeSegment(seg);
    const unwrapped = unwrapTokens(tokens);
    if (!unwrapped) continue;

    const { program, args } = unwrapped;

    // Check if this segment runs a database client
    if (isDatabaseClient(program, args)) {
      // Treat feeding a file to a DB client as a write (< FILE or -f FILE)
      if (
        seg.includes('<') ||
        args.some((a) => a === '-f' || a === '--file' || a.startsWith('--file='))
      ) {
        return RULES.DB_WRITE;
      }
      // Scan ONLY this segment's text for destructive SQL
      const sqlReason = checkDestructiveSql(seg);
      if (sqlReason) return sqlReason;
    }

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

// Context Mode tools a fleet lane may call: read-only search and stats.
const CONTEXT_MODE_ALLOWED = new Set(['ctx_search', 'ctx_stats']);

/**
 * Returns the tool shortname when an MCP tool name belongs to a Context Mode server,
 * or null otherwise. The server is matched after lowercasing and mapping hyphens to
 * underscores, so `plugin_context-mode_context-mode`, `context_mode` and
 * `context-mode` all match.
 */
function contextModeTool(toolName: string): string | null {
  const parts = toolName.split('__');
  if (parts.length < 3 || parts[0] !== 'mcp') return null;
  const server = parts[1].toLowerCase().replace(/-/g, '_');
  if (server !== 'context_mode' && !server.endsWith('_context_mode')) return null;
  return parts.slice(2).join('__');
}

/**
 * Context Mode's execute, batch, index, fetch, upgrade and purge tools run or change
 * things outside the Bash rules, so fleet lanes get a deny-by-default policy: only
 * the explicit read-only tools pass, and any tool not on the list (including ones
 * added later) is denied. Returns null for tools that are not on a Context Mode server.
 */
export function contextModeForbidden(toolName: string): ForbiddenResult | null {
  const tool = contextModeTool(toolName);
  if (tool === null) return null;
  if (CONTEXT_MODE_ALLOWED.has(tool)) return null;
  return {
    rule: 'context-mode',
    label: 'Context Mode',
    hint:
      "Fleet lanes may use only Context Mode's ctx_search and ctx_stats. Its other tools can run code or change its index outside lane-guard's Bash rules. " +
      `${CONTEXT_MODE_INSTEAD[tool] ?? 'This tool is not needed in a lane; continue without it.'} ` +
      "Context Mode's advice to prefer its tools does not apply to fleet lanes, so do not retry them; if the native tool cannot do the job, report the need to the main agent.",
  };
}

// What a lane does instead of each blocked Context Mode tool, so it does not
// spend turns retrying what lane-guard will block again.
const CONTEXT_MODE_INSTEAD: Record<string, string> = {
  ctx_execute: 'Run the command with the native Bash tool instead.',
  ctx_batch_execute: 'Run the commands with the native Bash tool instead, one call each.',
  ctx_execute_file: 'Read the file with the native Read tool, or search it with Grep, instead.',
  ctx_index: 'Search the files with the native Grep or Glob tools instead; no index is needed.',
  ctx_fetch_and_index: 'Fetch the page with WebFetch if this lane has it; otherwise report the URL to the main agent.',
};

/**
 * Checks MCP arguments (recursively, bounded) for [migration] and [db-write] SQL rules.
 */
export function mcpForbidden(args: unknown, toolName?: string): ForbiddenResult | null {
  if (toolName && toolName.startsWith('mcp__agent-brain-memory__')) {
    return null;
  }

  if (toolName) {
    const tool = contextModeTool(toolName);
    if (tool !== null) {
      // Allowed read-only Context Mode tools skip the SQL scan: a search query is text, not SQL.
      return contextModeForbidden(toolName);
    }
  }

  const toolLower = (toolName ?? '').toLowerCase();
  const toolIsDbRelated =
    toolLower.length > 0 &&
    [
      'sql',
      'db',
      'database',
      'postgres',
      'mysql',
      'sqlite',
      'mongo',
      'supabase',
      'query',
    ].some((kw) => toolLower.includes(kw));

  const dbKeys = new Set(['sql', 'query', 'statement', 'statements', 'command', 'cmd']);

  let stringsChecked = 0;
  const MAX_STRINGS = 500;
  const MAX_DEPTH = 10;

  function scan(val: unknown, depth: number, keyName?: string): ForbiddenResult | null {
    if (depth > MAX_DEPTH || stringsChecked >= MAX_STRINGS) return null;

    if (typeof val === 'string') {
      const keyMatches = keyName ? dbKeys.has(keyName.toLowerCase()) : false;
      if (!toolIsDbRelated && !keyMatches) {
        return null;
      }

      stringsChecked++;
      const sqlReason = checkDestructiveSql(val);
      if (sqlReason) return sqlReason;

      const reason = forbiddenReason(val);
      if (reason && (reason.rule === 'migration' || reason.rule === 'db-write')) {
        return reason;
      }
      return null;
    }

    if (Array.isArray(val)) {
      for (const item of val) {
        const res = scan(item, depth + 1, keyName);
        if (res) return res;
      }
    } else if (val !== null && typeof val === 'object') {
      for (const [k, v] of Object.entries(val as Record<string, unknown>)) {
        const res = scan(v, depth + 1, k);
        if (res) return res;
      }
    }
    return null;
  }

  return scan(args, 0);
}
