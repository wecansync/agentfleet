import { test, expect, mock } from 'claude-code/testing';
import type { CoreEngineInterface, On, PluginOptions, SessionMessage } from 'claude-code';
import { register } from '../hooks/fast-jev.js';

function setup(options: PluginOptions = {}) {
  let envs: Record<string, string | undefined> = {};
  let settings: Record<string, unknown> = {};
  const _$: Partial<CoreEngineInterface> = {
    env: { get: async (name: string) => envs[name] },
    settings: {
      read: async () => {
        settingsReadCalled = true;
        return settings;
      }
    },
    ui: {
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
    _$, logs, toasts, fetches, hooks,
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
  expect(toasts.length).toBe(1);

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
