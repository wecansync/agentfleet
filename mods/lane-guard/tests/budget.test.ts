import { test, expect } from 'claude-code/testing';
import type { CoreEngineInterface, On } from 'claude-code';
import { register } from '../hooks/register.js';
import { addNote, budgetNote, recordCall, turnLimit } from '../hooks/budget.js';

const ROOT = '/opt/claude/agentfleet/mods/lane-guard';
const NOTE_70 = (count: number, limit: number) =>
  `AgentFleet: you have used about ${count} of your ${limit} turns. Finish the part you are on, run its check, and report what is done, what remains, and the files you changed. Do not start another part.`;
const NOTE_90 = (count: number, limit: number) =>
  `AgentFleet: about ${count} of ${limit} turns used. Report now, even if unfinished: what is done, what remains, and the files you changed.`;

// The cache is per lane type and per agent id, so each test uses its own.
function agentFile(maxTurns: string): string {
  return `---\nname: x\nmodel: "sonnet"\nmaxTurns: ${maxTurns}\n---\n\nBody\n`;
}

function setup(agents: Array<{ id: string; type: string }>, files: Record<string, string> = {}) {
  const reads: string[] = [];
  const _$: Partial<CoreEngineInterface> = {
    plugin: { name: 'lane-guard', root: ROOT },
    agent: { list: async () => agents } as any,
    ui: { log: () => {} } as any,
    fs: {
      stat: async (path: string) => {
        if (!(path in files)) throw new Error('missing');
        return { kind: 'file', size: files[path].length, realPath: path };
      },
      read: async (path: string) => {
        reads.push(path);
        return files[path];
      },
    } as any,
  };
  const hooks: Record<string, Function> = {};
  const on: On = ((event: string, ...args: any[]) => {
    hooks[event] = args[args.length - 1];
  }) as any;
  register(on, {} as any);
  return { _$, hooks, reads };
}

// Runs `count` calls and returns each result's context.
async function run(hooks: Record<string, Function>, _$: any, event: Record<string, unknown>, count: number, answer: any = { result: 'ok' }) {
  const contexts: Array<readonly string[] | undefined> = [];
  for (let i = 0; i < count; i++) {
    const r = await hooks['tool.call'](_$, event, () => ({ ...answer }));
    contexts.push(r.context);
  }
  return contexts;
}

test('a fleet lane gets one note at 70% and one at 90% of its turns', async () => {
  const { _$, hooks } = setup(
    [{ id: 'b-1', type: 'fleet-bone' }],
    { '/opt/claude/agents/fleet-bone.md': agentFile('10') },
  );
  const contexts = await run(hooks, _$, { agentId: 'b-1', tool: 'Bash', command: 'npm test' }, 12);
  contexts.forEach((context, i) => {
    const count = i + 1;
    if (count === 7) expect(context).toEqual([NOTE_70(7, 10)]);
    else if (count === 9) expect(context).toEqual([NOTE_90(9, 10)]);
    else expect(context).toBeUndefined();
  });
});

test('thresholds round up, and a note is added after the tool\'s own context', async () => {
  const { _$, hooks } = setup(
    [{ id: 'b-2', type: 'fleet-btwo' }],
    { '/opt/claude/agents/fleet-btwo.md': agentFile('45') },
  );
  const contexts = await run(hooks, _$, { agentId: 'b-2', tool: 'Read' }, 41, { result: 'ok', context: ['from the tool'] });
  expect(contexts[30]).toEqual(['from the tool']);
  expect(contexts[31]).toEqual(['from the tool', NOTE_70(32, 45)]);
  expect(contexts[39]).toEqual(['from the tool']);
  expect(contexts[40]).toEqual(['from the tool', NOTE_90(41, 45)]);
});

test('the result is passed through unchanged apart from the note', async () => {
  const { _$, hooks } = setup(
    [{ id: 'b-3', type: 'fleet-bthree' }],
    { '/opt/claude/agents/fleet-bthree.md': agentFile('10') },
  );
  const answer = { result: { out: 1 }, ref: 7, text: 'done', isReadOnly: true };
  let seen: any = null;
  let calls = 0;
  for (let i = 0; i < 7; i++) {
    seen = await hooks['tool.call'](_$, { agentId: 'b-3', tool: 'Read' }, () => {
      calls += 1;
      return { ...answer };
    });
  }
  expect(calls).toBe(7);
  expect(seen).toEqual({ ...answer, context: [NOTE_70(7, 10)] });
});

test('the main loop and non-fleet agents are untouched', async () => {
  const { _$, hooks, reads } = setup(
    [{ id: 'b-4', type: 'general-purpose' }],
    { '/opt/claude/agents/general-purpose.md': agentFile('2') },
  );
  const main = await run(hooks, _$, { tool: 'Bash', command: 'npm test' }, 5);
  const other = await run(hooks, _$, { agentId: 'b-4', tool: 'Bash', command: 'npm test' }, 5);
  expect([...main, ...other].every(context => context === undefined)).toBe(true);
  expect(reads).toEqual([]);
});

test('no note when maxTurns cannot be read', async () => {
  const cases: Array<[string, Record<string, string>]> = [
    ['fleet-bmissing', {}],
    ['fleet-bnolimit', { '/opt/claude/agents/fleet-bnolimit.md': '---\nname: x\n---\n' }],
    ['fleet-bbad', { '/opt/claude/agents/fleet-bbad.md': agentFile('lots') }],
  ];
  for (const [type, files] of cases) {
    const { _$, hooks } = setup([{ id: `id-${type}`, type }], files);
    const contexts = await run(hooks, _$, { agentId: `id-${type}`, tool: 'Bash', command: 'npm test' }, 120);
    expect(contexts.every(context => context === undefined)).toBe(true);
  }
});

test('a plugin root outside the installed layout gives no note', async () => {
  const { _$, hooks } = setup(
    [{ id: 'b-5', type: 'fleet-bfive' }],
    { '/opt/claude/agents/fleet-bfive.md': agentFile('4') },
  );
  _$.plugin = { name: 'lane-guard', root: '/usr/local/src/lane-guard' } as any;
  const contexts = await run(hooks, _$, { agentId: 'b-5', tool: 'Bash', command: 'npm test' }, 6);
  expect(contexts.every(context => context === undefined)).toBe(true);
});

test('maxTurns is read once per lane type', async () => {
  const { _$, hooks, reads } = setup(
    [{ id: 'b-6', type: 'fleet-bsix' }, { id: 'b-7', type: 'fleet-bsix' }],
    { '/opt/claude/agents/fleet-bsix.md': agentFile('10') },
  );
  await run(hooks, _$, { agentId: 'b-6', tool: 'Bash', command: 'npm test' }, 3);
  const second = await run(hooks, _$, { agentId: 'b-7', tool: 'Bash', command: 'npm test' }, 7);
  expect(reads).toEqual(['/opt/claude/agents/fleet-bsix.md']);
  // Each agent has its own count.
  expect(second[6]).toEqual([NOTE_70(7, 10)]);
});

test('denied calls count but get no note', async () => {
  const { _$, hooks } = setup(
    [{ id: 'b-8', type: 'fleet-beight' }],
    { '/opt/claude/agents/fleet-beight.md': agentFile('10') },
  );
  // Six allowed calls, then a denied one at the 70% mark.
  await run(hooks, _$, { agentId: 'b-8', tool: 'Bash', command: 'npm test' }, 6);
  let nextCalled = false;
  const denied = await hooks['tool.call'](_$, { agentId: 'b-8', tool: 'Bash', command: 'git push' }, () => {
    nextCalled = true;
    return { result: 'ok' };
  });
  expect(nextCalled).toBe(false);
  expect(denied.deny).toContain('AgentFleet lane-guard blocked');
  expect(denied.context).toBeUndefined();
  // The denied call was the 7th, so the 70% note is still due on the 8th.
  const [next] = await run(hooks, _$, { agentId: 'b-8', tool: 'Bash', command: 'npm test' }, 1);
  expect(next).toEqual([NOTE_70(8, 10)]);
});

test('a deny returned by a later hook gets no note', async () => {
  const { _$, hooks } = setup(
    [{ id: 'b-9', type: 'fleet-bnine' }],
    { '/opt/claude/agents/fleet-bnine.md': agentFile('1') },
  );
  const r = await hooks['tool.call'](_$, { agentId: 'b-9', tool: 'Bash', command: 'npm test' }, () => ({ deny: 'no' }));
  expect(r).toEqual({ deny: 'no' });
});

test('a failure while working out a note returns the result unchanged, and the tool runs once', async () => {
  const { _$, hooks } = setup(
    [{ id: 'b-10', type: 'fleet-bten' }],
    { '/opt/claude/agents/fleet-bten.md': agentFile('1') },
  );
  _$.fs = { stat: async () => { throw new Error('io'); }, read: async () => { throw new Error('io'); } } as any;
  let calls = 0;
  const r = await hooks['tool.call'](_$, { agentId: 'b-10', tool: 'Bash', command: 'npm test' }, () => {
    calls += 1;
    return { result: 'ok' };
  });
  expect(calls).toBe(1);
  expect(r).toEqual({ result: 'ok' });
});

test('turnLimit reads maxTurns from the frontmatter only', () => {
  expect(turnLimit(agentFile('100'))).toBe(100);
  expect(turnLimit('---\r\nmaxTurns: "60"\r\n---\r\n')).toBe(60);
  expect(turnLimit('---\nname: x\n---\nmaxTurns: 50\n')).toBeNull();
  expect(turnLimit('maxTurns: 50\n')).toBeNull();
  expect(turnLimit(agentFile('0'))).toBeNull();
  expect(turnLimit(agentFile('-5'))).toBeNull();
  expect(turnLimit(null)).toBeNull();
});

test('budgetNote gives each note once and the later one when a call passes both', () => {
  const budget = { count: 0, sent: 0 as 0 | 1 | 2 };
  expect(budgetNote(budget, null)).toBeNull();
  budget.count = 1;
  expect(budgetNote(budget, 1)).toBe(NOTE_90(1, 1));
  expect(budgetNote(budget, 1)).toBeNull();
  expect(recordCall('b-direct').count).toBe(1);
  expect(recordCall('b-direct').count).toBe(2);
  expect(addNote({ deny: 'x' }, 'n')).toEqual({ deny: 'x' });
});
