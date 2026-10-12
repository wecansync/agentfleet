import { test, expect, mock } from 'claude-code/testing';
import type { CoreEngineInterface, On, PluginOptions, SessionMessage } from 'claude-code';
import { register } from '../hooks/fast-jev.js';
import { bandText } from '../src/stats.js';

function setup(options: PluginOptions = {}) {
  let envs: Record<string, string | undefined> = {};
  let settings: Record<string, unknown> = {};
  let state: any = { text: null };
  const files: Record<string, string> = {};
  const _$: Partial<CoreEngineInterface> = {
    plugin: { root: '/usr/local/claude/agentfleet/mods/fast-jev-compaction' },
    clock: { now: async () => 1800000000000 },
    session: { usage: async () => ({ context: { tokens: 12000 } }) },
    state: {
      get: async () => ({ value: state, version: 0 }),
      set: async (_ref: any, value: any) => { state = value; return { isSet: true }; },
    },
    command: { register: async () => {} },
    fs: {
      exists: async (path: string) => path in files,
      stat: async (path: string) => {
        if (path.endsWith('/agentfleet')) return { kind: 'directory', realPath: path, size: 0 };
        if (!(path in files)) throw new Error('missing');
        return { kind: 'file', realPath: path, size: files[path].length };
      },
      read: async (path: string) => files[path],
      write: async (path: string, text: string) => { files[path] = text; },
    },
    env: { get: async (name: string) => envs[name] },
    settings: {
      read: async () => {
        settingsReadCalled = true;
        return settings;
      }
    },
    ui: {
      resolve: () => ({ Box: 'Box', Text: 'Text', Button: 'Button' }),
      log: (text: string) => logs.push(text),
      toast: (text: string) => toasts.push(text),
    },
    http: {
      fetch: async (url: string, init?: any) => {
        fetches.push({ url, init });
        return {
          status: 200,
          ok: true,
          text: JSON.stringify({
            answers: {
              call_t1: { noul: 0.9 },
              result_t1: { noul: 0.1 },
            },
          }),
        };
      },
    },
  };

  const logs: string[] = [];
  const toasts: string[] = [];
  const fetches: any[] = [];
  const hooks: Record<string, Function> = {};
  let settingsReadCalled = false;

  const on: On = (event: string, ...args: any[]) => {
    const handler = args[args.length - 1];
    hooks[event] = handler;
  };

  register(on, options);

  return {
    _$, logs, toasts, fetches, hooks, files,
    getState: () => state,
    setEnv: (k: string, v: string) => { envs[k] = v; },
    getSettingsReadCalled: () => settingsReadCalled,
  };
}

test('agentId passthrough', async () => {
  const { _$, hooks, logs } = setup({});
  expect(hooks['session.compact']).toBeDefined();

  let nextCalled = false;
  const event = {
    agentId: 'sub-agent-123',
    trigger: 'manual',
    messages: []
  };

  await hooks['session.compact'](_$, event, (e: any) => {
    nextCalled = true;
    return { messages: e.messages };
  });

  expect(nextCalled).toBe(true);
  expect(logs.length).toBe(0);
  expect((await hooks['command.run'](_$, {})).text).toContain('0 compactions');
});

test('precompute passthrough', async () => {
  const { _$, hooks, logs } = setup({});

  let nextCalled = false;
  const event = {
    trigger: 'precompute',
    messages: []
  };

  await hooks['session.compact'](_$, event, (e: any) => {
    nextCalled = true;
    return { messages: e.messages };
  });

  expect(nextCalled).toBe(true);
  expect(logs.length).toBe(0);
  expect((await hooks['command.run'](_$, {})).text).toContain('0 compactions');
});

test('no key -> one notice then silent passthrough', async () => {
  const { _$, hooks, logs, toasts } = setup({});

  const event = {
    trigger: 'manual',
    messages: []
  };

  // First call
  let nextCalled1 = false;
  await hooks['session.compact'](_$, event, (e: any) => {
    nextCalled1 = true;
    return { messages: e.messages };
  });

  expect(nextCalled1).toBe(true);
  expect(logs.length).toBe(1);
  expect(logs[0]).toContain('no TypeSafe API key');
  expect(toasts.length).toBe(0);

  // Second call
  let nextCalled2 = false;
  await hooks['session.compact'](_$, event, (e: any) => {
    nextCalled2 = true;
    return { messages: e.messages };
  });

  expect(nextCalled2).toBe(true);
  // Log count shouldn't increase
  expect(logs.length).toBe(1);
});

test('key from env used, settings.read never called', async () => {
  const { _$, hooks, logs, fetches, setEnv, getSettingsReadCalled } = setup({
    preserveRecentMessages: 0,
  });
  setEnv('TYPESAFE_API_KEY', 'test-key-123');

  const event = {
    trigger: 'manual',
    messages: [
      { role: 'user', text: 'initial goal', toolUses: [] },
      {
        role: 'assistant',
        text: 'calling tool',
        toolUses: [{ tool_use_id: 'call_1', tool: 'Bash', input: { command: 'ls' } }],
      },
      {
        role: 'user',
        text: '',
        toolUses: [],
        toolResults: [{ tool_use_id: 'call_1', text: 'result text '.repeat(100) }],
      },
      { role: 'assistant', text: 'done', toolUses: [] },
    ],
  };

  await hooks['session.compact'](_$, event, (e: any) => {
    return { messages: e.messages };
  });

  expect(getSettingsReadCalled()).toBe(false);
  expect(fetches.length).toBeGreaterThan(0);
  expect(fetches[0].init?.headers?.['authorization']).toContain('test-key-123');
});

test('no turn.complete hook registered', async () => {
  const { hooks } = setup({});
  expect(hooks['turn.complete']).toBeUndefined();
});

const statsPath = '/usr/local/claude/agentfleet/jev-stats.json';
const configPath = '/usr/local/claude/agentfleet/config.json';
const next = (e: any) => ({ messages: e.messages });

function transcript() {
  return [
    { role: 'user', text: 'goal', toolUses: [] },
    { role: 'assistant', text: '', toolUses: [{ tool_use_id: 'call_1', tool: 'Bash', input: { command: 'ls' } }] },
    { role: 'user', text: '', toolUses: [], toolResults: [{ tool_use_id: 'call_1', text: 'result '.repeat(3000) }] },
    { role: 'assistant', text: 'done', toolUses: [] },
  ];
}

test('success records metadata, displays band, and clears on prompt', async () => {
  const s = setup({ apiKey: 'sk-' + '1234567890'.repeat(2), preserveRecentMessages: 0 });
  const output = await s.hooks['session.compact'](s._$, { trigger: 'auto', messages: transcript() }, next);
  const saved = JSON.parse(s.files[statsPath]);
  expect(saved.recent[0].outcome).toBe('jev');
  expect(saved.recent[0].tokensBefore).toBe(12000);
  expect(saved.recent[0].tokensAfter).toBeGreaterThan(0);
  expect(saved.recent[0].messagesKept).toBe(output.messages.length);
  expect(saved.totals.jev).toBe(1);
  expect(s.getState().text).toContain('JEV compacted this conversation');
  expect(s.getState().text).toContain('messages word-for-word');
  expect(s.getState().text).not.toContain('null');
  expect(s.files[statsPath]).not.toContain('result result');
  const other = { type: 'other-band' };
  const rendered = await s.hooks['ui.render'](s._$, { props: { bodyColumns: 80 } }, () => other);
  expect(rendered.children[0]).toBe(other);
  expect(rendered.children[1].children[0].children[0]).toBe(s.getState().text);
  expect(rendered.children[1].children[1].props.label).toBe('Hide');
  await s.hooks['prompt.submit'](s._$, { text: 'next' }, (e: any) => e);
  expect(s.getState().text).toBe(null);
});

test('fallback and no-key stats, unknown usage omitted', async () => {
  const s = setup({ apiKey: 'fixture-key' });
  s._$.session = { usage: async () => { throw new Error('unavailable'); } } as any;
  await s.hooks['session.compact'](s._$, { trigger: 'manual', messages: [] }, next);
  const entry = JSON.parse(s.files[statsPath]).recent[0];
  expect(entry.outcome).toBe('fallback');
  expect(entry.tokensBefore).toBe(null);
  expect(entry.messagesBefore).toBe(null);
  expect(s.getState().text).toContain("JEV fell back to Claude's summary: below 25%");
  expect(s.getState().text).not.toContain('null');
  const n = setup();
  await n.hooks['session.compact'](n._$, { trigger: 'manual', messages: [] }, next);
  expect(JSON.parse(n.files[statsPath]).recent[0].outcome).toBe('no-key');
  expect(JSON.parse(n.files[statsPath]).totals.fallbacks).toBe(1);
});

test('/jev percent, off, totals and credentials are safe', async () => {
  const key = 'sk-' + '1234567890'.repeat(2);
  const s = setup({ apiKey: key, preserveRecentMessages: 0 });
  await s.hooks['session.compact'](s._$, { trigger: 'plugin', messages: transcript() }, next);
  s.files[configPath] = JSON.stringify({ compactPercent: 83 });
  const result = await s.hooks['command.run'](s._$, {});
  expect(result.text).toContain('JEV: on; API key configured: yes');
  expect(result.text).toContain("83% of each model's context");
  expect(result.text).toContain('1 compactions, 1 JEV, 0 fallbacks');
  for (const text of [result.text, s.getState().text, s.files[statsPath], ...s.logs]) expect(text).not.toContain(key);
  s.files[configPath] = JSON.stringify({ compactPercent: 'off' });
  expect((await s.hooks['command.run'](s._$, {})).text).toContain('Claude Code default');
  const n = setup();
  expect((await n.hooks['command.run'](n._$, {})).text).toContain('API key configured: no');
  expect((await n.hooks['command.run'](n._$, {})).text).toContain("70% of each model's context");
});

test('unknown band values omitted', () => {
  const text = bandText({ at: '', outcome: 'jev', trigger: 'manual', tokensBefore: null, tokensAfter: null, messagesBefore: null, messagesKept: null, ms: 3100, durationMs: 3100, reason: null, reductionRatio: null });
  expect(text).toBe('JEV compacted this conversation: 3.1 s');
});
