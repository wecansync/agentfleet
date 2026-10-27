import { test, expect } from 'claude-code/testing';
import type { CoreEngineInterface, On } from 'claude-code';
import { register } from '../hooks/register.js';
import { forbiddenReason, mcpForbidden } from '../hooks/rules.js';

const ROOT = '/home/u/.claude/agentfleet/mods/lane-guard';
const CONFIG = '/home/u/.claude/agentfleet/config.json';

// Main mode asks only about commands that discard work or data, or publish.
const asked: Array<[string, string]> = [
  ['git push --force origin main', 'git-discard'],
  ['git push -f', 'git-discard'],
  ['git push --force-with-lease', 'git-discard'],
  ['git push origin +main', 'git-discard'],
  ['git push origin :old-branch', 'git-discard'],
  ['git push --delete origin old', 'git-discard'],
  ['git reset --hard HEAD~1', 'git-discard'],
  ['git clean -fd', 'git-discard'],
  ['git checkout -- .', 'git-discard'],
  ['git checkout .', 'git-discard'],
  ['git checkout src/app.ts', 'git-discard'],
  ['git checkout HEAD src/app.ts', 'git-discard'],
  ['git checkout main -- README.md', 'git-discard'],
  ['git checkout --theirs composer.lock', 'git-discard'],
  ['git restore -s HEAD~1 src/app.ts', 'git-discard'],
  ['git branch -f main HEAD~1', 'git-discard'],
  ['git branch -M old existing', 'git-discard'],
  ['git branch -Df feature', 'git-discard'],
  ['powershell -Command "git reset --hard"', 'git-discard'],
  ['pwsh -NoProfile -c "gh repo delete o/r --yes"', 'github-delete'],
  ['git restore src/app.ts', 'git-discard'],
  ['git restore --staged --worktree src/app.ts', 'git-discard'],
  ['git switch --discard-changes main', 'git-discard'],
  ['git branch -D feature', 'git-discard'],
  ['git branch --delete --force feature', 'git-discard'],
  ['git stash drop', 'git-discard'],
  ['git stash clear', 'git-discard'],
  ['git reflog expire --expire=now --all', 'git-discard'],
  ['git worktree remove --force ../wt', 'git-discard'],
  ['git filter-repo --path secret.txt --invert-paths', 'git-discard'],
  ['git -C repo reset --hard', 'git-discard'],
  ['cd app && git reset --hard', 'git-discard'],
  ['bash -c "git push --force"', 'git-discard'],
  ['gh repo delete wecansync/old --yes', 'github-delete'],
  ['gh release delete v1.0.0', 'github-delete'],
  ['gh api -X DELETE repos/o/r/git/refs/heads/x', 'github-delete'],
  ['gh api --method=DELETE repos/o/r', 'github-delete'],
  ['npm publish', 'publish-deploy'],
  ['rsync -a dist/ host:/var/www/', 'publish-deploy'],
  ['php artisan migrate:fresh', 'migration'],
  ['psql -c "DROP TABLE users"', 'db-write'],
  ['rm -rf ~', 'rm-dangerous'],
];

// The main session commits, pushes, opens pull requests and switches branches as asked.
const allowed = [
  'git status',
  'git commit -m "fix: thing"',
  'git push',
  'git push -u origin feat/x',
  'git pull',
  'git merge main',
  'git checkout main',
  'git checkout -b feat/x',
  'git switch -c feat/x',
  'git restore --staged src/app.ts',
  'git restore -S src/app.ts',
  'git checkout --track origin/feat/x',
  'git checkout feat/x',
  'git checkout v1.2.3',
  'git checkout -b feat/x origin/main',
  'git branch feat/x',
  'git branch -m old new',
  'pwsh -NoProfile -Command Get-ChildItem',
  'git reset HEAD~1',
  'git reset --soft HEAD~1',
  'git clean -n',
  'git branch -d merged',
  'git stash',
  'git stash pop',
  'git tag v1.0.0',
  'gh pr create --fill',
  'gh pr merge 12 --merge',
  'gh api repos/o/r/pulls',
  'rm -rf build',
  'ls -la',
];

test('main mode flags commands that discard work, data, or publish', () => {
  for (const [command, rule] of asked) {
    expect([command, forbiddenReason(command, 'main')?.rule]).toEqual([command, rule]);
  }
});

test('main mode leaves everyday git and GitHub work alone', () => {
  for (const command of allowed) {
    expect([command, forbiddenReason(command, 'main')]).toEqual([command, null]);
  }
});

test('lane mode is unchanged: a plain push and a PR stay blocked', () => {
  expect(forbiddenReason('git push')?.rule).toBe('git-history');
  expect(forbiddenReason('gh pr create --fill')?.rule).toBe('github-write');
  expect(forbiddenReason('git push --force')?.rule).toBe('git-history');
});

test('main mode lets Context Mode tools through but still scans their SQL', () => {
  expect(mcpForbidden({ code: 'print(1)' }, 'mcp__plugin_context-mode_context-mode__ctx_execute', 'main')).toBe(null);
  expect(mcpForbidden({ code: 'print(1)' }, 'mcp__plugin_context-mode_context-mode__ctx_execute')?.rule).toBe('context-mode');
  expect(mcpForbidden({ sql: 'DROP TABLE users' }, 'mcp__postgres__query', 'main')?.rule).toBe('db-write');
});

function setup(options: { guard?: boolean | 'missing'; answer?: string | Error; agents?: Array<{ id: string; type: string }>; logThrows?: boolean } = {}) {
  const logs: string[] = [];
  const questions: Array<{ question: string; options: any }> = [];
  const files: Record<string, string> = {};
  if (options.guard !== 'missing') {
    files[CONFIG] = JSON.stringify({ format: 'agentfleet.config.v1', mainSessionGuard: options.guard ?? true });
  }
  const _$ = {
    plugin: { root: ROOT },
    agent: { list: async () => options.agents ?? [] },
    fs: {
      stat: async (path: string) => {
        if (!(path in files)) throw new Error('missing');
        return { kind: 'file', realPath: path, size: files[path]!.length };
      },
      read: async (path: string) => files[path],
    },
    ui: {
      log: (msg: string) => {
        if (options.logThrows) throw new Error('log failed');
        logs.push(msg);
      },
      ask: async (question: string, opts: any) => {
        questions.push({ question, options: opts });
        if (options.answer instanceof Error) throw options.answer;
        return options.answer ?? 'Run it';
      },
    },
  } as unknown as Partial<CoreEngineInterface>;
  const hooks: Record<string, Function> = {};
  const on: On = (event: string, ...args: any[]) => {
    hooks[event] = args[args.length - 1];
  };
  register(on, {} as any);
  const call = async (event: Record<string, unknown>) => {
    let ran = false;
    const result = await hooks['tool.call']!(_$, event, () => {
      ran = true;
      return { result: 'ok' };
    });
    return { ran, result };
  };
  return { call, logs, questions };
}

test('guard off: the main session runs a force push without a question', async () => {
  const { call, questions } = setup({ guard: false });
  const { ran } = await call({ tool: 'Bash', command: 'git push --force' });
  expect(ran).toBe(true);
  expect(questions.length).toBe(0);
});

test('no preferences file: the guard is off', async () => {
  const { call, questions } = setup({ guard: 'missing' });
  const { ran } = await call({ tool: 'Bash', command: 'git reset --hard' });
  expect(ran).toBe(true);
  expect(questions.length).toBe(0);
});

test('guard on: an everyday command runs without a question', async () => {
  const { call, questions } = setup();
  const { ran } = await call({ tool: 'Bash', command: 'git push -u origin feat/x' });
  expect(ran).toBe(true);
  expect(questions.length).toBe(0);
});

test('guard on: "Run it" runs the command', async () => {
  const { call, questions } = setup({ answer: 'Run it' });
  const { ran } = await call({ tool: 'Bash', command: 'git reset --hard HEAD~1' });
  expect(ran).toBe(true);
  expect(questions.length).toBe(1);
  expect(questions[0]!.question).toBe('AgentFleet guard: run this work-discarding git command? git reset --hard HEAD~1');
  expect(questions[0]!.options).toEqual({ options: ['Run it', "Don't run it"], header: 'Guard' });
});

test('guard on: "Don\'t run it" stops the command', async () => {
  const { call, logs } = setup({ answer: "Don't run it" });
  const { ran, result } = await call({ tool: 'Bash', command: 'npm publish' });
  expect(ran).toBe(false);
  expect(result.deny).toBe('The user declined to run this publish or deploy command. Do not retry it; ask the user how to go on.');
  expect(logs[0]).toContain('[publish-deploy]');
});

test('guard on: an answer typed under Other stops the command and is passed on', async () => {
  const { call } = setup({ answer: 'only the staging host' });
  const { ran, result } = await call({ tool: 'Bash', command: 'rsync -a dist/ prod:/srv/' });
  expect(ran).toBe(false);
  expect(result.deny).toContain('answered: "only the staging host"');
});

test('guard on: nobody to ask (dismissed, or a -p run) stops the command', async () => {
  const { call } = setup({ answer: new Error('dismissed') });
  const { ran, result } = await call({ tool: 'Bash', command: 'git clean -fdx' });
  expect(ran).toBe(false);
  expect(result.deny).toContain('nobody confirmed it');
});

test('guard on: a subagent that is not a fleet lane is asked about too, by name', async () => {
  const { call, questions } = setup({ agents: [{ id: 'a1', type: 'general-purpose' }], answer: "Don't run it" });
  const { ran } = await call({ agentId: 'a1', tool: 'Bash', command: 'git push --force' });
  expect(ran).toBe(false);
  expect(questions[0]!.question).toContain('in general-purpose?');
});

test('guard on: fleet lanes keep their own rules and are never asked', async () => {
  const { call, questions } = setup({ agents: [{ id: 'l1', type: 'fleet-implement' }] });
  const { ran, result } = await call({ agentId: 'l1', tool: 'Bash', command: 'git commit -m x' });
  expect(ran).toBe(false);
  expect(result.deny).toContain('blocked this git history or working-tree command in fleet-implement');
  expect(questions.length).toBe(0);
});

test('guard on: secrets in the command are masked in the question', async () => {
  const { call, questions } = setup();
  const token = 'ghp_' + 'a1B2c3D4e5'.repeat(4);
  await call({ tool: 'Bash', command: `GITHUB_TOKEN=${token} gh repo delete o/r --yes` });
  expect(questions.length).toBe(1);
  expect(questions[0]!.question).not.toContain(token);
});

test('guard outside an AgentFleet install (no known Claude directory) stays off', async () => {
  const logs: string[] = [];
  const hooks: Record<string, Function> = {};
  register(((event: string, ...args: any[]) => {
    hooks[event] = args[args.length - 1];
  }) as On, {} as any);
  const _$ = { plugin: { root: '/tmp/dev/lane-guard' }, ui: { log: (m: string) => logs.push(m) } };
  let ran = false;
  await hooks['tool.call']!(_$, { tool: 'Bash', command: 'git reset --hard' }, () => {
    ran = true;
    return { result: 'ok' };
  });
  expect(ran).toBe(true);
});

test('guard on: a failure while logging never lets a declined command run', async () => {
  for (const answer of ["Don't run it", new Error('dismissed'), 'something else']) {
    const { call } = setup({ answer, logThrows: true });
    const { ran, result } = await call({ tool: 'Bash', command: 'git reset --hard' });
    expect([String(answer), ran]).toEqual([String(answer), false]);
    expect(typeof result.deny).toBe('string');
  }
});

test('guard on: "Run it" still runs when logging fails', async () => {
  const { call } = setup({ answer: 'Run it', logThrows: true });
  expect((await call({ tool: 'Bash', command: 'git reset --hard' })).ran).toBe(true);
});

test('a failure while logging a lane block still blocks the lane', async () => {
  const { call } = setup({ agents: [{ id: 'l1', type: 'fleet-implement' }], logThrows: true });
  expect((await call({ agentId: 'l1', tool: 'Bash', command: 'git push' })).ran).toBe(false);
});
