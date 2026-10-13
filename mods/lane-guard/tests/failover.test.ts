import { test, expect } from 'claude-code/testing';
import type { CoreEngineInterface, On } from 'claude-code';
import { register } from '../hooks/register.js';
import { claudeDirectory, downModels, failoverModel, laneModels, laneName, markDescription } from '../hooks/failover.js';

const NOW = Date.parse('2026-10-13T10:00:00Z');
const ROOT = '/home/u/.claude/agentfleet/mods/lane-guard';
const FLEET = JSON.stringify({
  lanes: {
    implement: { model: 'codex-luna[1m]', fallbacks: ['claude-haiku-5.5[1m]', 'gemini-3.8-flash[1m]', 'codex-luna[1m]'] },
    docs: { model: 'claude-sonnet-4.6[1m]' },
  },
});

function outages(models: Record<string, { status: number; until: string }>): string {
  return JSON.stringify({ format: 'agentfleet.outages.v1', lanes: {}, models });
}

const LUNA_DOWN = outages({ 'codex-luna[1m]': { status: 503, until: '2026-10-13T10:15:00Z' } });

function setup(files: Record<string, string>, root = ROOT) {
  const logs: string[] = [];
  const reads: string[] = [];
  const _$: Partial<CoreEngineInterface> = {
    plugin: { name: 'lane-guard', root },
    clock: { now: async () => NOW } as any,
    session: { root: async () => '/work/app' } as any,
    ui: { log: (msg: string) => logs.push(msg) } as any,
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
  return { _$, hooks, logs, reads };
}

async function spawn(hooks: Record<string, Function>, _$: any, event: Record<string, unknown>) {
  let seen: any = null;
  await hooks['agent.spawn'](_$, { description: 'Build it', fork: false, ...event }, (e: any) => {
    seen = e;
    return { model: e.model ?? 'resolved', agentId: 'a1' };
  });
  return seen;
}

const files = (outageSource: string) => ({
  '/home/u/.claude/fleet.json': FLEET,
  '/home/u/.claude/agentfleet/outages.json': outageSource,
});

test('a lane whose model is down starts on its first healthy fallback, marked in the description', async () => {
  const { _$, hooks, logs } = setup(files(LUNA_DOWN));
  const seen = await spawn(hooks, _$, { subagentType: 'fleet-implement' });
  expect(seen.model).toBe('claude-haiku-5.5[1m]');
  expect(seen.description).toBe('Build it (failover: claude-haiku-5.5[1m])');
  expect(logs.length).toBe(1);
});

test('fallbacks that are down too are skipped; an expired outage no longer counts', async () => {
  const both = outages({
    'codex-luna[1m]': { status: 503, until: '2026-10-13T10:15:00Z' },
    'claude-haiku-5.5[1m]': { status: 429, until: '2026-10-13T11:00:00Z' },
    'gemini-3.8-flash[1m]': { status: 503, until: '2026-10-13T09:59:00Z' },
  });
  const { _$, hooks } = setup(files(both));
  const seen = await spawn(hooks, _$, { subagentType: 'fleet-implement', description: 'Retry (failover: claude-haiku-5.5[1m])' });
  expect(seen.model).toBe('gemini-3.8-flash[1m]');
  // A re-dispatched task carries one mark, the current one.
  expect(seen.description).toBe('Retry (failover: gemini-3.8-flash[1m])');
});

test('the spawn is left alone when nothing is down, all models are down, or the caller decided', async () => {
  const cases: Array<[string, Record<string, unknown>]> = [
    [outages({}), { subagentType: 'fleet-implement' }],
    [outages({
      'codex-luna[1m]': { status: 503, until: '2026-10-13T10:15:00Z' },
      'claude-haiku-5.5[1m]': { status: 503, until: '2026-10-13T10:15:00Z' },
      'gemini-3.8-flash[1m]': { status: 503, until: '2026-10-13T10:15:00Z' },
    }), { subagentType: 'fleet-implement' }],
    [LUNA_DOWN, { subagentType: 'fleet-implement', model: 'sonnet' }],
    [LUNA_DOWN, { subagentType: 'fleet-implement', fork: true }],
    [LUNA_DOWN, { subagentType: 'fleet-implement', workflow: { runId: 'wf_1', agentIndex: 1 } }],
    [LUNA_DOWN, { subagentType: 'general-purpose' }],
    [outages({ 'claude-sonnet-4.6[1m]': { status: 503, until: '2026-10-13T10:15:00Z' } }), { subagentType: 'fleet-docs' }],
  ];
  for (const [source, event] of cases) {
    const { _$, hooks, logs } = setup(files(source));
    const seen = await spawn(hooks, _$, event);
    expect(seen.model).toBe(event.model);
    expect(seen.description).toBe('Build it');
    expect(logs.length).toBe(0);
  }
});

test('an unknown install layout, a symlinked or missing file, or bad JSON never blocks a spawn', async () => {
  const outside = setup(files(LUNA_DOWN), '/opt/plugins/lane-guard');
  expect((await spawn(outside.hooks, outside._$, { subagentType: 'fleet-implement' })).model).toBe(undefined);
  expect(outside.reads.length).toBe(0);

  const linked = setup(files(LUNA_DOWN));
  (linked._$ as any).fs.stat = async (path: string) => ({ kind: 'file', size: 10, realPath: `/elsewhere${path}` });
  expect((await spawn(linked.hooks, linked._$, { subagentType: 'fleet-implement' })).model).toBe(undefined);

  const broken = setup({ '/home/u/.claude/fleet.json': '{not json', '/home/u/.claude/agentfleet/outages.json': LUNA_DOWN });
  expect((await spawn(broken.hooks, broken._$, { subagentType: 'fleet-implement' })).model).toBe(undefined);

  const failing = setup(files(LUNA_DOWN));
  (failing._$ as any).fs.stat = async () => { throw new Error('denied'); };
  expect((await spawn(failing.hooks, failing._$, { subagentType: 'fleet-implement' })).model).toBe(undefined);
});

test("a project's pinned lane uses its own models, not the global map's", async () => {
  const pin = JSON.stringify({ version: 'delegate-fleet.local.v1', lanes: { implement: { model: 'pinned-a', fallbacks: ['pinned-b'] } } });
  // The global lane model is down, but this project pinned the lane elsewhere.
  const untouched = setup({ ...files(LUNA_DOWN), '/work/app/.claude/fleet.local.json': pin });
  expect((await spawn(untouched.hooks, untouched._$, { subagentType: 'fleet-implement' })).model).toBe(undefined);
  // The pinned model is down: the pin's own fallback is used.
  const pinDown = outages({ 'pinned-a': { status: 503, until: '2026-10-13T10:15:00Z' } });
  const switched = setup({ ...files(pinDown), '/work/app/.claude/fleet.local.json': pin });
  expect((await spawn(switched.hooks, switched._$, { subagentType: 'fleet-implement' })).model).toBe('pinned-b');
  // A file that is not a pin map is ignored.
  const other = setup({ ...files(LUNA_DOWN), '/work/app/.claude/fleet.local.json': '{"lanes":{"implement":{"model":"x"}}}' });
  expect((await spawn(other.hooks, other._$, { subagentType: 'fleet-implement' })).model).toBe('claude-haiku-5.5[1m]');
});

test('pure helpers: lane names, model lists, outage parsing and the Claude directory', () => {
  expect(laneName('fleet-implement-fast')).toBe('implement-fast');
  expect(laneName('fleet-')).toBe(null);
  expect(laneName('general-purpose')).toBe(null);
  expect(laneModels(FLEET, 'implement')).toEqual(['codex-luna[1m]', 'claude-haiku-5.5[1m]', 'gemini-3.8-flash[1m]']);
  expect(laneModels(JSON.stringify({ lanes: { x: { model: 'a b', fallbacks: ['ok-1', 7] } } }), 'x')).toEqual(['ok-1']);
  expect([...downModels(outages({ 'bad id': { status: 503, until: '2026-10-13T10:15:00Z' } }), NOW)]).toEqual([]);
  expect([...downModels('{"format":"other","models":{"a":{"status":503,"until":"2026-10-13T10:15:00Z"}}}', NOW)]).toEqual([]);
  expect(failoverModel(FLEET, LUNA_DOWN, 'missing', NOW)).toBe(null);
  expect(markDescription('Fix it', 'm-1')).toBe('Fix it (failover: m-1)');
  expect(claudeDirectory('/home/u/.claude/agentfleet/mods/lane-guard/')).toBe('/home/u/.claude');
  expect(claudeDirectory('/agentfleet/mods/lane-guard')).toBe(null);
  expect(claudeDirectory('C:/agentfleet/mods/lane-guard')).toBe(null);
});
