import { test, expect } from 'claude-code/testing';
import { cases, type TestCase } from './cases.js';
import { contextModeForbidden, forbiddenReason, mcpForbidden, redactSecrets } from '../hooks/rules.js';

for (const tc of cases as TestCase[]) {
  test(`forbiddenReason case: ${tc.command}`, () => {
    const res = forbiddenReason(tc.command);
    if (tc.rule === null) {
      expect(res).toBeNull();
    } else {
      expect(res).not.toBeNull();
      expect(res?.rule).toBe(tc.rule);
    }
  });
}

test('mcpForbidden: destructive SQL detected in DB tool', () => {
  const res = mcpForbidden({ sql: 'DROP TABLE users' }, 'mcp__postgres__query');
  expect(res).not.toBeNull();
  expect(res?.rule).toBe('db-write');
});

test('mcpForbidden: migration detected in DB tool', () => {
  const res = mcpForbidden({ cmd: 'php artisan migrate' }, 'mcp__database__exec');
  expect(res).not.toBeNull();
  expect(res?.rule).toBe('migration');
});

test('mcpForbidden: non-destructive query allowed', () => {
  const res = mcpForbidden({ sql: 'SELECT * FROM users WHERE id = 1' }, 'mcp__postgres__query');
  expect(res).toBeNull();
});

test('mcpForbidden: skips mcp__agent-brain-memory__ entirely', () => {
  const res = mcpForbidden(
    { content: 'Logs truncate at 120 chars; delete from the queue' },
    'mcp__agent-brain-memory__memory_save',
  );
  expect(res).toBeNull();
});

test('mcpForbidden: skips non-DB tools without SQL argument keys', () => {
  const res = mcpForbidden(
    { message: 'Logs truncate at 120 chars; delete from the queue' },
    'mcp__slack__post',
  );
  expect(res).toBeNull();
});

test('mcpForbidden: scans SQL argument key even if tool name is not DB-specific', () => {
  const res = mcpForbidden({ sql: 'DROP TABLE users' });
  expect(res).not.toBeNull();
  expect(res?.rule).toBe('db-write');
});

test('redactSecrets: comprehensive pattern verification', () => {
  const cases: Array<[string, string]> = [
    ['mysql -pHunter2pass', 'mysql -p[redacted]'],
    ['mysqladmin -pHunter2pass status', 'mysqladmin -p[redacted] status'],
    ['--password=secret123', '--password=[redacted]'],
    ['--password secret123', '--password [redacted]'],
    ['MYSQL_PWD=hunter2', 'MYSQL_PWD=[redacted]'],
    ['PGPASSWORD=hunter2', 'PGPASSWORD=[redacted]'],
    ['https://user:pass123@github.com', 'https://user:[redacted]@github.com'],
    ['postgresql://alice:secret123@db:5432/app', 'postgresql://alice:[redacted]@db:5432/app'],
    ['sk_live_1234567890abcdef', '[redacted]'],
    ['sk_test_1234567890abcdef', '[redacted]'],
    ['rk_live_1234567890abcdef', '[redacted]'],
    ['github_pat_1234567890abcdef', '[redacted]'],
    ['AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY', 'AWS_SECRET_ACCESS_KEY=[redacted]'],
    ['Bearer my_token_12345', 'Bearer [redacted]'],
    // Joined at runtime: the bundle's secret scan rejects key-shaped literals.
    ['sk-' + '1234567890abcdef', '[redacted]'],
    ['ghp_' + '1234567890abcdef', '[redacted]'],
    ['xoxb-' + '1234567890abcdef', '[redacted]'],
    ['user@example.com', '[redacted]'],
  ];

  for (const [input, expected] of cases) {
    expect(redactSecrets(input)).toBe(expected);
  }
});

const CONTEXT_MODE_SERVERS = [
  'plugin_context-mode_context-mode',
  'context_mode',
  'context-mode',
  'Context-Mode',
];

for (const server of CONTEXT_MODE_SERVERS) {
  test(`contextModeForbidden: execute and batch tools on ${server} are denied`, () => {
    for (const tool of ['ctx_execute', 'ctx_execute_file', 'ctx_batch_execute']) {
      const res = contextModeForbidden(`mcp__${server}__${tool}`);
      expect(res?.rule).toBe('context-mode');
    }
  });

  test(`contextModeForbidden: index, fetch, upgrade, purge and unknown tools on ${server} are denied`, () => {
    for (const tool of ['ctx_index', 'ctx_fetch_and_index', 'ctx_upgrade', 'ctx_purge', 'ctx_insight', 'ctx_doctor', 'ctx_future_tool']) {
      const res = contextModeForbidden(`mcp__${server}__${tool}`);
      expect(res?.rule).toBe('context-mode');
    }
  });

  test(`contextModeForbidden: ctx_search and ctx_stats on ${server} are allowed`, () => {
    expect(contextModeForbidden(`mcp__${server}__ctx_search`)).toBeNull();
    expect(contextModeForbidden(`mcp__${server}__ctx_stats`)).toBeNull();
  });
}

test('contextModeForbidden: denial hint names the native tool for the blocked one', () => {
  expect(contextModeForbidden('mcp__context_mode__ctx_execute')?.hint).toContain('native Bash tool');
  expect(contextModeForbidden('mcp__context_mode__ctx_execute_file')?.hint).toContain('native Read tool');
  expect(contextModeForbidden('mcp__context_mode__ctx_future_tool')?.hint).toContain('not needed in a lane');
});

test('contextModeForbidden: other servers and look-alike names are not touched', () => {
  expect(contextModeForbidden('mcp__postgres__query')).toBeNull();
  expect(contextModeForbidden('mcp__agent-brain-memory__memory_save')).toBeNull();
  expect(contextModeForbidden('mcp__mycontext_modes__ctx_execute')).toBeNull();
  expect(contextModeForbidden('Bash')).toBeNull();
});

test('mcpForbidden: ctx_search with SQL-looking query text is allowed', () => {
  const res = mcpForbidden({ query: 'DROP TABLE users' }, 'mcp__context_mode__ctx_search');
  expect(res).toBeNull();
});

test('mcpForbidden: context-mode execute tool is denied before the SQL scan', () => {
  const res = mcpForbidden({ code: 'echo hi' }, 'mcp__plugin_context-mode_context-mode__ctx_execute');
  expect(res?.rule).toBe('context-mode');
});
