import { test, expect } from 'claude-code/testing';
import { cases, type TestCase } from './cases.js';
import { forbiddenReason, mcpForbidden, redactSecrets } from '../hooks/rules.js';

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
    ['sk-1234567890abcdef', '[redacted]'],
    ['ghp_1234567890abcdef', '[redacted]'],
    ['xoxb-1234567890abcdef', '[redacted]'],
    ['user@example.com', '[redacted]'],
  ];

  for (const [input, expected] of cases) {
    expect(redactSecrets(input)).toBe(expected);
  }
});
