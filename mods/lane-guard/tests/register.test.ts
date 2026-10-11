import { test, expect } from 'claude-code/testing';
import type { CoreEngineInterface, On } from 'claude-code';
import { register } from '../hooks/register.js';

function setup(agentsList: Array<{ id: string; type: string }> = []) {
  const logs: string[] = [];
  const _$: Partial<CoreEngineInterface> = {
    agent: {
      list: async () => agentsList,
    },
    ui: {
      log: (msg: string) => logs.push(msg),
    },
  };

  const hooks: Record<string, Function> = {};
  const on: On = (event: string, ...args: any[]) => {
    hooks[event] = args[args.length - 1];
  };

  register(on, {} as any);

  return { _$, logs, hooks };
}

test('Bash git push from fleet-review agent is denied', async () => {
  const { _$, hooks, logs } = setup([{ id: 'sub-fleet-1', type: 'fleet-review' }]);
  const event = {
    agentId: 'sub-fleet-1',
    tool: 'Bash',
    command: 'git push',
  };

  let nextCalled = false;
  const result = await hooks['tool.call'](_$, event, (e: any) => {
    nextCalled = true;
    return { result: 'ok' };
  });

  expect(nextCalled).toBe(false);
  expect(result.deny).toBe(
    'AgentFleet lane-guard blocked this git history or working-tree command in fleet-review. Fleet lanes may not commit, push, merge, deploy, publish, run migrations or destructive SQL, or revert the working tree. Report the command to the main agent instead; it decides whether to run it.',
  );
  expect(logs.length).toBe(1);
  expect(logs[0]).toContain('fleet-review');
  expect(logs[0]).toContain('git-history');
});

test('Bash git push from main loop passes through', async () => {
  const { _$, hooks } = setup([{ id: 'sub-fleet-1', type: 'fleet-review' }]);
  const event = {
    tool: 'Bash',
    command: 'git push',
  };

  let nextCalled = false;
  const result = await hooks['tool.call'](_$, event, (e: any) => {
    nextCalled = true;
    return { result: 'ok' };
  });

  expect(nextCalled).toBe(true);
  expect(result.deny).toBeUndefined();
  expect(result.result).toBe('ok');
});

test('Bash git push from non-fleet agent passes through', async () => {
  const { _$, hooks } = setup([{ id: 'sub-general-1', type: 'general-purpose' }]);
  const event = {
    agentId: 'sub-general-1',
    tool: 'Bash',
    command: 'git push',
  };

  let nextCalled = false;
  const result = await hooks['tool.call'](_$, event, (e: any) => {
    nextCalled = true;
    return { result: 'ok' };
  });

  expect(nextCalled).toBe(true);
  expect(result.deny).toBeUndefined();
  expect(result.result).toBe('ok');
});

test('MCP call with DROP TABLE users from a fleet agent is denied', async () => {
  const { _$, hooks, logs } = setup([{ id: 'sub-fleet-2', type: 'fleet-coder' }]);
  const event = {
    agentId: 'sub-fleet-2',
    tool: 'mcp__postgres__query',
    sql: 'DROP TABLE users',
  };

  let nextCalled = false;
  const result = await hooks['tool.call'](_$, event, (e: any) => {
    nextCalled = true;
    return { result: 'ok' };
  });

  expect(nextCalled).toBe(false);
  expect(result.deny).toBe(
    'AgentFleet lane-guard blocked this destructive SQL command in fleet-coder. Fleet lanes may not commit, push, merge, deploy, publish, run migrations or destructive SQL, or revert the working tree. Report the command to the main agent instead; it decides whether to run it.',
  );
  expect(logs.length).toBe(1);
  expect(logs[0]).toContain('fleet-coder');
  expect(logs[0]).toContain('db-write');
});

test('internal error passes through', async () => {
  const { _$, hooks } = setup();
  _$.agent = {
    list: async () => {
      throw new Error('agent list failed');
    },
  };
  const event = {
    agentId: 'sub-err-1',
    tool: 'Bash',
    command: 'git push',
  };

  let nextCalled = false;
  const result = await hooks['tool.call'](_$, event, (e: any) => {
    nextCalled = true;
    return { result: 'passthrough' };
  });

  expect(nextCalled).toBe(true);
  expect(result.result).toBe('passthrough');
});

test('a tool that throws is run once, not retried', async () => {
  const { _$, hooks } = setup([{ id: 'sub-throw-1', type: 'fleet-tests' }]);
  const event = { agentId: 'sub-throw-1', tool: 'Bash', command: 'npm test' };
  let calls = 0;
  await expect(
    hooks['tool.call'](_$, event, () => {
      calls += 1;
      throw new Error('tool failed');
    }),
  ).rejects.toThrow('tool failed');
  expect(calls).toBe(1);
});
