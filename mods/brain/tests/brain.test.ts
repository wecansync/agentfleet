import { test, expect, mock } from 'claude-code/testing'
import type { McpToolResult, On, RenderPropsOf } from 'claude-code'
import { compactNumber, fallbackText, formatUsage, loadBrain, mergeMemories, parseMemoryPage, splitMemories } from '../hooks/brain'

const noData = JSON.stringify({ session: null,
  today: { since: '2026-10-15', sessions: 0, input_tokens: 0, output_tokens: 0, cache_read_tokens: 0, cache_write_tokens: 0 },
  week: { since: '2026-10-12', sessions: 0, input_tokens: 0, output_tokens: 0, cache_read_tokens: 0, cache_write_tokens: 0 },
})
const successful = (text: string): { value: McpToolResult } => ({ value: { content: [{ type: 'text', text }], isError: false } })
const failed = (text: string): { value: McpToolResult } => ({ value: { content: [{ type: 'text', text }], isError: true } })
const commandInput = { command: 'brain', args: '', origin: { kind: 'composer' as const }, presentation: { isFullscreen: false, columns: 80 } }

function setup(on: On, respond: (tool: string, args: Record<string, unknown>) => unknown, clock = true, placed = false) {
  if (clock) mock.clock(on)
  on('session.id', () => ({ value: 'session-1' }))
  on('command.register', (_$, e) => ({ value: { command: e.name } }))
  on('session.start', (_$, e) => ({ cwd: e.cwd }))
  on('mcp.call', async (_$, e) => {
    const result = await respond(e.tool, e.args)
    if (result !== null && typeof result === 'object' && 'value' in result) return result
    if (result !== null && typeof result === 'object' && 'deny' in result) return result
    return { value: result }
  })
  on('ui.open', () => ({ value: { isPlaced: placed } }))
  on('ui.close', () => ({ value: undefined }))
  on('ui.focus', () => ({}))
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

const critPage = [
  '[#2492] (fact, auto, critical; scope: unknown (legacy)) ' + JSON.stringify('AgentFleet smoke-test gotchas (verified 2026-10-15):\n- A scratch install\n- Keep the test isolated'),
  '[#2491] (decision, auto, critical, current) "Always validate changes"',
  '\nShowing 1-2 of 28. Next page: offset=2.',
].join('\n')
const latestPage = [
  '[#2492] (fact, auto, git: current; verification: unverified; scope: unknown (legacy)) ' + JSON.stringify('Duplicate newest copy'),
  '[#2490] (fact, current) Older unquoted entry\nwith a continuation',
  '\nShowing 1-2 of 28.',
].join('\n')

const makeView = (memorySource = '') => ({
  usage: formatUsage(stats),
  memories: parseMemoryPage(memorySource).memories,
  total: parseMemoryPage(memorySource).total ?? 0,
  memoryMessage: null,
})

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
})

test('real 0.5 headers, nested parentheses and escaped JSON content parse alongside old plain entries', () => {
  const parsed = parseMemoryPage([
    '[#2492] (fact, auto, git: current; verification: unverified; scope: unknown (legacy)) ' + JSON.stringify('AgentFleet smoke-test gotchas (verified 2026-10-15):\n- A scratch install\n- Keep the test isolated'),
    '[#2489] (decision, auto, critical, current) Older unquoted content\nwith a continuation',
    '[#2488] (fact, current) Hidden [superseded by #2492]',
  ].join('\n'))
  expect(parsed.memories).toEqual([
    { marker: '[#2492]', kind: 'fact', content: 'AgentFleet smoke-test gotchas (verified 2026-10-15):\n- A scratch install\n- Keep the test isolated', critical: false },
    { marker: '[#2489]', kind: 'decision', content: 'Older unquoted content\nwith a continuation', critical: true },
  ])
})

test('page footer is stripped and its total is retained, including a footer-only page', () => {
  const page = parseMemoryPage('[#7] (fact, current) Safe\n\nShowing 1-1 of 45. Next page: offset=1.')
  expect(page).toEqual({ memories: [{ marker: '[#7]', kind: 'fact', content: 'Safe', critical: false }], total: 45 })
  expect(parseMemoryPage('Showing 31-45 of 45.')).toEqual({ memories: [], total: 45 })
})

test('critical memories precede latest entries and duplicate markers are removed', () => {
  const critical = parseMemoryPage(critPage).memories
  const newest = parseMemoryPage(latestPage).memories
  expect(mergeMemories(critical, newest).map(memory => memory.marker)).toEqual(['[#2492]', '[#2491]', '[#2490]'])
  expect(mergeMemories(critical, newest)[0]?.critical).toBe(true)
})

test('display strips controls and format characters from headers and content', () => {
  const parsed = parseMemoryPage('[#4] (fact, current) Safe' + String.fromCodePoint(0x202e) + 'content\u0085').memories
  expect(parsed[0]?.content).toBe('Safecontent')
})

test('memory pages use critical and latest argument pages concurrently', async ($, on) => {
  const calls: Record<string, unknown>[] = []
  setup(on, (tool, args) => {
    if (tool === 'usage_stats') return successful(JSON.stringify(stats))
    calls.push(args)
    return successful(args.priority === 'critical' ? critPage : latestPage)
  })
  const reply = await startAndRun($)
  expect(reply.text).toContain('Memories (3 of 28)')
  expect(reply.text).toContain('★ [#2492] fact: AgentFleet smoke-test gotchas (verified 2026-10-15):')
  expect(calls).toEqual([{ priority: 'critical', limit: 10 }, { limit: 30 }])
})

test('argument compatibility failures show the update guidance without retrying unpaged', async ($, on) => {
  const memoryArgs: Record<string, unknown>[] = []
  setup(on, (tool, args) => {
    if (tool === 'usage_stats') return successful(noData)
    memoryArgs.push(args)
    return { deny: 'Invalid params: unexpected or additional properties: limit, offset, priority, kind' }
  })
  const reply = await startAndRun($)
  expect(memoryArgs).toEqual([{ priority: 'critical', limit: 10 }, { limit: 30 }])
  expect(reply.text).toContain('Run `agent-brain update` for the full /brain view.')
  expect(reply.text).not.toContain('memories for this project')
})

test('memory-off, not-entitled and no-memory gate responses are silently omitted', async ($, on) => {
  const messages = [
    'memory is not enabled: run memory enable',
    'memory is a Pro feature: upgrade your account',
    'memory is unavailable for this team project (membership or organization subscription inactive)',
    'no memories for this project',
  ]
  setup(on, tool => tool === 'usage_stats' ? successful(noData) : failed(messages.shift() ?? 'memory is not enabled'))
  const reply = await startAndRun($)
  expect(reply.text).not.toContain('Memories (')
  expect(reply.text).not.toContain("Couldn't load memories")
  expect(reply.text).not.toContain('Pro')
  expect(reply.text).not.toContain('account')
})

test('other memory failures and unparseable results show the list instruction', async ($, on) => {
  setup(on, tool => tool === 'usage_stats' ? successful(noData) : failed('memory backend timed out'))
  expect((await startAndRun($)).text).toContain("Couldn't load memories; run `agent-brain memory list`.")
})

test('an empty or unparseable successful memory response is not reported as an empty list', async ($, on) => {
  setup(on, tool => successful(tool === 'usage_stats' ? noData : 'not a memory entry'))
  expect((await startAndRun($)).text).toContain("Couldn't load memories; run `agent-brain memory list`.")
})

test('memories with unavailable usage still open with a usage message', async ($, on) => {
  setup(on, tool => tool === 'usage_stats' ? failed('database is locked') : successful('[#1] (fact, current) Available'), true, true)
  await $.session.start({ cwd: '/test', surface: 'terminal', isInteractive: true })
  const reply = await $.command.run(commandInput)
  expect(reply.text).toBe('agent-brain pane opened.')
})

test('the unplaced surface falls back to usage and up to ten memory rows', async ($, on) => {
  setup(on, tool => successful(tool === 'usage_stats' ? JSON.stringify(stats) : Array.from({ length: 12 }, (_, index) => `[#${index + 1}] (fact, current) Entry ${index + 1}`).join('\n')), true, false)
  const reply = await startAndRun($)
  expect(reply.text).toContain('This session: 1.2M in, 45k out over 1h 12m')
  expect(reply.text).toContain('Memories (12 of 12)')
  expect(reply.text).toContain('[#10] fact: Entry 10')
  expect(reply.text).not.toContain('[#11] fact: Entry 11')
})

test('the pane renders rows and full expanded content; j/k buttons move selection', async ($, on) => {
  const memories = [
    '[#2492] (fact, auto, critical; scope: unknown (legacy)) ' + JSON.stringify('AgentFleet smoke-test gotchas (verified 2026-10-15):\n- A scratch install\n- Keep the test isolated'),
    '[#2491] (decision, current) Keep tests focused',
    '[#2490] (fact, current) Legacy unquoted entry',
  ].join('\n')
  setup(on, tool => successful(tool === 'usage_stats' ? JSON.stringify(stats) : memories))
  await $.session.start({ cwd: '/test', surface: 'terminal', isInteractive: true })
  await $.command.run(commandInput)
  const pane = await $.ui.mount({
    plugin: 'brain', surface: 'terminal', component: 'Pane', requestId: 'brain',
    props: { title: 'agent-brain', isFocused: true, bodyColumns: 70, placement: 'inline', scroll: { offset: 0, bodyRows: 20 }, view: {} } as RenderPropsOf['Pane'],
  })
  const rows = await pane.findAll({ type: 'Button' })
  expect(rows.map(row => row.props.label)).toEqual([
    '★ [#2492] fact: AgentFleet smoke-test gotchas (verified 2026-10-15):',
    '  [#2491] decision: Keep tests focused',
    '  [#2490] fact: Legacy unquoted entry',
    'k previous', 'j next', 'Close',
  ])
  expect((await pane.find({ text: 'Memories (3 of 3)' }))?.text).toContain('Memories (3 of 3)')
  await pane.press({ key: 'memory:0' })
  expect((await pane.find({ text: '- A scratch install' }))?.text).toContain('- A scratch install')
  expect((await pane.find({ text: '- Keep the test isolated' }))?.text).toContain('- Keep the test isolated')
  await pane.press({ key: 'memory:0' })
  expect(await pane.find({ text: '- A scratch install' })).toBeUndefined()
  await pane.press({ key: 'brain-next' })
  await pane.press({ key: 'memory:1' })
  expect((await pane.find({ text: '[#2491] decision' }))?.text).toContain('[#2491] decisionKeep tests focused')
  expect(await pane.find({ text: '- A scratch install' })).toBeUndefined()
})

test('fallback rendering gives a three-memory example and preserves the selected full text', () => {
  const view = makeView([
    '[#2492] (fact, auto, critical; scope: unknown (legacy)) ' + JSON.stringify('AgentFleet smoke-test gotchas (verified 2026-10-15):\n- A scratch install\n- Keep the test isolated'),
    '[#2491] (decision, current) Keep tests focused',
    '[#2490] (fact, current) Legacy unquoted entry',
    '\nShowing 1-3 of 28.',
  ].join('\n'))
  expect(fallbackText(view)).toContain('Memories (3 of 28)')
  expect(view.memories[0]?.content).toContain('\n- A scratch install')
})

test('two rejected MCP calls keep the disconnected usage message', async ($, on) => {
  setup(on, () => ({ deny: 'MCP server not connected' }))
  const reply = await startAndRun($)
  expect(reply.text).toContain("agent-brain isn't connected.")
})

test('a timed out memory call returns without waiting for the server', async ($, on) => {
  const clock = mock.clock(on)
  setup(on, () => new Promise(() => undefined), false)
  await $.session.start({ cwd: '/test', surface: 'terminal', isInteractive: true })
  const pending = $.command.run(commandInput)
  for (let i = 0; i < 20; i++) await Promise.resolve()
  await clock.advance(5_000)
  const reply = await pending
  expect(reply.text).toContain("agent-brain didn't answer in time; try /brain again.")
  expect(reply.text).toContain("Couldn't load memories; run `agent-brain memory list`.")
})
