import { test, expect, mock } from 'claude-code/testing'
import type { McpToolResult, On } from 'claude-code'
import { compactNumber, formatMemories, formatUsage, runBrain, splitMemories } from '../hooks/brain'

const noData = JSON.stringify({ session: null,
  today: { since: '2026-10-15', sessions: 0, input_tokens: 0, output_tokens: 0, cache_read_tokens: 0, cache_write_tokens: 0 },
  week: { since: '2026-10-12', sessions: 0, input_tokens: 0, output_tokens: 0, cache_read_tokens: 0, cache_write_tokens: 0 },
})
const successful = (text: string): { value: McpToolResult } => ({ value: { content: [{ type: 'text', text }], isError: false } })
const failed = (text: string): { value: McpToolResult } => ({ value: { content: [{ type: 'text', text }], isError: true } })
const commandInput = { command: 'brain', args: '', origin: { kind: 'composer' as const }, presentation: { isFullscreen: false, columns: 80 } }

function setup(on: On, respond: (tool: string, args: Record<string, unknown>) => unknown, clock = true) {
  if (clock) mock.clock(on)
  on('session.id', () => ({ value: 'session-1' }))
  on('command.register', (_$, e) => ({ value: { command: e.name } }))
  on('session.start', (_$, e) => ({ cwd: e.cwd }))
  on('mcp.call', (_$, e) => respond(e.tool, e.args))
}

async function startAndRun($: Parameters<Parameters<typeof test>[2]>[0]) {
  await $.session.start({ cwd: '/test', surface: 'terminal', isInteractive: true })
  return $.command.run(commandInput)
}

const stats = {
  session: { id: 'session-1', input_tokens: 1_200_000, output_tokens: 45_000, duration_seconds: 4_320,
    lane_runs: 3, lane_input_tokens: 300_000, lane_output_tokens: 12_000 },
  today: { since: '2026-10-15', sessions: 4, input_tokens: 3_400_000, output_tokens: 120_000,
    cache_read_tokens: 20_000_000, cache_write_tokens: 50_000 },
  week: { since: '2026-10-12', sessions: 18, input_tokens: 12_000_000, output_tokens: 600_000,
    cache_read_tokens: 80_000_000, cache_write_tokens: 100_000 },
}

test('compact numbers use plain, thousands and millions formats', () => {
  expect([compactNumber(950), compactNumber(12_000), compactNumber(1_200_000), compactNumber(999_999)]).toEqual(['950', '12k', '1.2M', '1M'])
})

test('usage lines include lanes only when present and format a missing session', () => {
  expect(formatUsage(stats)).toEqual([
    'This session: 1.2M in, 45k out over 1h 12m; lanes: 3 runs, 300k in, 12k out',
    'Today:        4 sessions, 3.4M in, 120k out (cache read 20M)',
    'This week:    18 sessions, 12M in, 600k out (since 2026-10-12)',
  ])
  const noLanes = { ...stats, session: { ...stats.session, lane_runs: 0 } }
  expect(formatUsage(noLanes)[0]).toBe('This session: 1.2M in, 45k out over 1h 12m')
  expect(formatUsage({ ...stats, session: null })[0]).toBe('This session: not recorded yet')
  expect(formatUsage({ available: false })).toEqual(["agent-brain's local store is unavailable; run `agent-brain status`."])
})

test('memory splitting keeps multi-line and team entries together', () => {
  expect(splitMemories('intro\n[#2485] (decision, auto, critical, current) Decision\ncontinuation\n[team#design] (fact, current) Team fact'))
    .toEqual(['[#2485] (decision, auto, critical, current) Decision\ncontinuation', '[team#design] (fact, current) Team fact'])
  expect(formatMemories('[#2485] (decision, auto, critical, current) Decision\ncontinued\n[team#design] (fact, current) Team fact'))
    .toEqual(['★ [#2485] decision: Decision', '  [team#design] fact: Team fact'])
})

test('memories put critical entries first, retain newest order and cap at ten', () => {
  const rows = Array.from({ length: 12 }, (_, i) => `[#${i + 1}] (${i === 11 ? 'fact, critical' : 'fact, current'}) Entry ${i + 1}`)
  const output = formatMemories(rows.join('\n'))
  expect(output).toHaveLength(10)
  expect(output[0]).toBe('★ [#12] fact: Entry 12')
  expect(output.slice(1)).toEqual(Array.from({ length: 9 }, (_, i) => `  [#${i + 1}] fact: Entry ${i + 1}`))
})

test('superseded entries are omitted, long content is shortened, and controls are stripped', () => {
  const output = formatMemories(`[#1] (fact, current) ${'x'.repeat(130)}\n[#2] (decision, critical, current) Old [superseded by #3]\n[#3] (decision, current) New`)
  expect(output).toHaveLength(2)
  expect(output[0]).toBe(`  [#1] fact: ${'x'.repeat(109)}…`)
  expect(output[1]).toBe('  [#3] decision: New')
  expect(formatMemories('[#4] (fact, current) Safe' + String.fromCodePoint(0x202e) + 'content')[0]).toBe('  [#4] fact: Safecontent')
})

test('the brain command formats usage and latest memories from mocked MCP calls', async ($, on) => {
  const memories = '[#2485] (decision, auto, critical, current) User decisions (2026-10-15): agent-brain session-start\n[#2487] (fact, current) Claude Code mod API facts'
  setup(on, tool => successful(tool === 'usage_stats' ? JSON.stringify(stats) : memories))
  const reply = await startAndRun($)
  expect(reply.text).toBe([
    'agent-brain',
    'This session: 1.2M in, 45k out over 1h 12m; lanes: 3 runs, 300k in, 12k out',
    'Today:        4 sessions, 3.4M in, 120k out (cache read 20M)',
    'This week:    18 sessions, 12M in, 600k out (since 2026-10-12)',
    '',
    'Latest memories',
    '★ [#2485] decision: User decisions (2026-10-15): agent-brain session-start',
    '  [#2487] fact: Claude Code mod API facts',
  ].join('\n'))
})

test('memory-list errors omit memories but keep available usage', async ($, on) => {
  setup(on, tool => tool === 'usage_stats' ? successful(noData) : failed('memory error'))
  const reply = await startAndRun($)
  expect(reply.text).not.toContain('Latest memories')
  expect(reply.text).toContain('This session: not recorded yet')
})

test('rejected memory-list calls omit memories but keep available usage', async ($, on) => {
  setup(on, tool => tool === 'usage_stats' ? successful(noData) : { deny: 'Memory service unavailable' })
  const reply = await startAndRun($)
  expect(reply.text).not.toContain('Latest memories')
  expect(reply.text).toContain('This session: not recorded yet')
})

test('projects without memories omit the memories section', async ($, on) => {
  setup(on, tool => successful(tool === 'usage_stats' ? noData : 'no memories for this project'))
  expect((await startAndRun($)).text).not.toContain('Latest memories')
})

test('missing usage tool shows the version guidance when memories work', async ($, on) => {
  setup(on, tool => tool === 'usage_stats' ? { deny: 'Unknown tool: usage_stats' } : successful('[#1] (fact, current) Available'))
  const reply = await startAndRun($)
  expect(reply.text).toContain('Usage needs agent-brain 0.5.1 or later: run `agent-brain update`.')
  expect(reply.text).toContain('Latest memories')
})

test('older agent-brain is identified when usage reports an unknown tool and memories are empty', async ($, on) => {
  setup(on, tool => tool === 'usage_stats' ? failed('Unknown tool: usage_stats') : successful('no memories for this project'))
  expect((await startAndRun($)).text).toContain('Usage needs agent-brain 0.5.1 or later: run `agent-brain update`.')
})

test('unknown-tool MCP result shows version guidance when memories work', async ($, on) => {
  setup(on, tool => tool === 'usage_stats' ? failed('Unknown tool: usage_stats') : successful('[#1] (fact, current) Available'))
  const reply = await startAndRun($)
  expect(reply.text).toContain('Usage needs agent-brain 0.5.1 or later: run `agent-brain update`.')
})

test('two rejected calls report that agent-brain is not connected', async ($, on) => {
  setup(on, () => ({ deny: 'MCP server not connected' }))
  expect((await startAndRun($)).text).toBe("agent-brain isn't connected. Turn it on with `agentfleet config agent-brain on`, then restart Claude Code.")
})

test('unknown-tool rejection for usage reports the required upgrade when memory-list succeeds', async ($, on) => {
  setup(on, tool => tool === 'usage_stats' ? { deny: 'Unknown tool usage_stats' } : successful('[#1] (fact, current) Available'))
  const reply = await startAndRun($)
  expect(reply.text).toContain('Usage needs agent-brain 0.5.1 or later: run `agent-brain update`.')
  expect(reply.text).toContain('Latest memories')
})

test('an unavailable local store prints its status instruction and hides returned memories', async ($, on) => {
  setup(on, tool => successful(tool === 'usage_stats' ? '{"available":false}' : '[#1] (fact, current) Do not show'))
  const reply = await startAndRun($)
  expect(reply.text).toContain("agent-brain's local store is unavailable; run `agent-brain status`.")
  expect(reply.text).not.toContain('Latest memories')
})

test('timed out MCP calls return without waiting for the server', async ($, on) => {
  const clock = mock.clock(on)
  setup(on, () => new Promise(() => undefined), false)
  await $.session.start({ cwd: '/test', surface: 'terminal', isInteractive: true })
  const pending = $.command.run(commandInput)
  for (let i = 0; i < 20; i++) await Promise.resolve()
  await clock.advance(5_000)
  expect((await pending).text).toBe("agent-brain\nagent-brain didn't answer in time; try /brain again.")
})

test('memory off on an older agent-brain still shows the update guidance', async ($, on) => {
  setup(on, tool => tool === 'usage_stats' ? { deny: 'Unknown tool: usage_stats' } : failed('memory is not active for this project'))
  const reply = await startAndRun($)
  expect(reply.text).toBe('agent-brain\nUsage needs agent-brain 0.5.1 or later: run `agent-brain update`.')
})

test('a usage error other than a missing tool points at agent-brain status', async ($, on) => {
  setup(on, tool => tool === 'usage_stats' ? failed('database is locked') : failed('memory is not active for this project'))
  expect((await startAndRun($)).text).toBe("agent-brain\nagent-brain couldn't read usage; run `agent-brain status`.")
})
