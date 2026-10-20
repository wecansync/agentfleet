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

test('PowerShell git push from fleet agent is denied', async () => {
  const { _$, hooks, logs } = setup([{ id: 'sub-fleet-ps', type: 'fleet-coder' }]);
  const event = {
    agentId: 'sub-fleet-ps',
    tool: 'PowerShell',
    command: 'git push',
  };

  let nextCalled = false;
  const result = await hooks['tool.call'](_$, event, (e: any) => {
    nextCalled = true;
    return { result: 'ok' };
  });

  expect(nextCalled).toBe(false);
  expect(result.deny).toContain('AgentFleet lane-guard blocked');
  expect(logs.length).toBe(1);
  expect(logs[0]).toContain('fleet-coder');
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

test('mcp__agent-brain-memory__memory_save passes through even with truncate/delete phrasing', async () => {
  const { _$, hooks } = setup([{ id: 'sub-fleet-mem', type: 'fleet-review' }]);
  const event = {
    agentId: 'sub-fleet-mem',
    tool: 'mcp__agent-brain-memory__memory_save',
    content: 'Logs truncate at 120 chars; delete from the queue',
  };

  let nextCalled = false;
  const result = await hooks['tool.call'](_$, event, (e: any) => {
    nextCalled = true;
    return { result: 'ok' };
  });

  expect(nextCalled).toBe(true);
  expect(result.result).toBe('ok');
});

test('Edit and Write to .git directories are denied', async () => {
  const { _$, hooks } = setup([{ id: 'sub-fleet-edit', type: 'fleet-coder' }]);

  const editEvent = {
    agentId: 'sub-fleet-edit',
    tool: 'Edit',
    file_path: '/repo/.git/hooks/pre-commit',
  };
  let nextCalled = false;
  const editResult = await hooks['tool.call'](_$, editEvent, () => {
    nextCalled = true;
    return { result: 'ok' };
  });
  expect(nextCalled).toBe(false);
  expect(editResult.deny).toContain('AgentFleet lane-guard blocked');

  const writeEvent = {
    agentId: 'sub-fleet-edit',
    tool: 'Write',
    file_path: '.git/config',
  };
  const writeResult = await hooks['tool.call'](_$, writeEvent, () => {
    nextCalled = true;
    return { result: 'ok' };
  });
  expect(writeResult.deny).toContain('AgentFleet lane-guard blocked');
});

test('when agent type cannot be resolved, forbidden command is denied (fail closed)', async () => {
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

  expect(nextCalled).toBe(false);
  expect(result.deny).toContain('unknown subagent');
});

test('when agent type cannot be resolved, allowed command passes through', async () => {
  const { _$, hooks } = setup();
  _$.agent = {
    list: async () => {
      throw new Error('agent list failed');
    },
  };
  const event = {
    agentId: 'sub-err-2',
    tool: 'Bash',
    command: 'npm test',
  };

  let nextCalled = false;
  const result = await hooks['tool.call'](_$, event, (e: any) => {
    nextCalled = true;
    return { result: 'passthrough' };
  });

  expect(nextCalled).toBe(true);
  expect(result.result).toBe('passthrough');
});

test('secrets are redacted in ui.log', async () => {
  const { _$, hooks, logs } = setup([{ id: 'sub-fleet-sec', type: 'fleet-coder' }]);
  const event = {
    agentId: 'sub-fleet-sec',
    tool: 'Bash',
    command: 'mysql -pHunter2pass -e "DROP TABLE users"',
  };

  await hooks['tool.call'](_$, event, () => ({ result: 'ok' }));

  expect(logs.length).toBe(1);
  expect(logs[0]).not.toContain('Hunter2pass');
  expect(logs[0]).toContain('-p[redacted]');
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

test('Context Mode execute with a shell command from a fleet lane is denied with a native-tools hint', async () => {
  const { _$, hooks, logs } = setup([{ id: 'sub-fleet-cm', type: 'fleet-coder' }]);
  const event = {
    agentId: 'sub-fleet-cm',
    tool: 'mcp__plugin_context-mode_context-mode__ctx_execute',
    language: 'shell',
    code: 'git commit -am "sneaky" && git push',
  };

  let nextCalled = false;
  const result = await hooks['tool.call'](_$, event, () => {
    nextCalled = true;
    return { result: 'ok' };
  });

  expect(nextCalled).toBe(false);
  expect(result.deny).toBe(
    "AgentFleet lane-guard blocked this Context Mode command in fleet-coder. Fleet lanes may use only Context Mode's ctx_search and ctx_stats. Its other tools can run code or change its index outside lane-guard's Bash rules. Run the command with the native Bash tool instead. Context Mode's advice to prefer its tools does not apply to fleet lanes, so do not retry them; if the native tool cannot do the job, report the need to the main agent.",
  );
  expect(result.deny).not.toContain('destructive SQL');
  expect(logs.length).toBe(1);
  expect(logs[0]).toContain('context-mode');
});

test('Context Mode execute_file, batch and upgrade from a fleet lane are denied', async () => {
  const { _$, hooks } = setup([{ id: 'sub-fleet-cm2', type: 'fleet-review' }]);
  for (const tool of ['ctx_execute_file', 'ctx_batch_execute', 'ctx_upgrade']) {
    let nextCalled = false;
    const result = await hooks['tool.call'](
      _$,
      { agentId: 'sub-fleet-cm2', tool: `mcp__context_mode__${tool}`, path: '/tmp/x' },
      () => {
        nextCalled = true;
        return { result: 'ok' };
      },
    );
    expect(nextCalled).toBe(false);
    expect(result.deny).toContain('Context Mode');
    expect(result.deny).toContain('do not retry them');
  }
});

test('Context Mode blocks name the native tool to use instead', async () => {
  const { _$, hooks } = setup([{ id: 'sub-fleet-cm4', type: 'fleet-implement' }]);
  const expected: Record<string, string> = {
    ctx_execute_file: 'native Read tool',
    ctx_batch_execute: 'native Bash tool',
    ctx_index: 'native Grep or Glob tools',
    ctx_fetch_and_index: 'WebFetch',
    ctx_purge: 'not needed in a lane',
  };
  for (const [tool, instead] of Object.entries(expected)) {
    const result = await hooks['tool.call'](
      _$,
      { agentId: 'sub-fleet-cm4', tool: `mcp__plugin_context-mode_context-mode__${tool}` },
      () => ({ result: 'ok' }),
    );
    expect(result.deny).toContain(instead);
  }
});

test('Context Mode ctx_search from a fleet lane passes through', async () => {
  const { _$, hooks } = setup([{ id: 'sub-fleet-cm3', type: 'fleet-review' }]);
  let nextCalled = false;
  const result = await hooks['tool.call'](
    _$,
    { agentId: 'sub-fleet-cm3', tool: 'mcp__context_mode__ctx_search', query: 'git push' },
    () => {
      nextCalled = true;
      return { result: 'ok' };
    },
  );
  expect(nextCalled).toBe(true);
  expect(result.result).toBe('ok');
});

test('Context Mode execute from the main conversation passes through', async () => {
  const { _$, hooks } = setup([]);
  let nextCalled = false;
  const result = await hooks['tool.call'](
    _$,
    { tool: 'mcp__context_mode__ctx_execute', language: 'shell', code: 'echo hi' },
    () => {
      nextCalled = true;
      return { result: 'ok' };
    },
  );
  expect(nextCalled).toBe(true);
  expect(result.result).toBe('ok');
});
