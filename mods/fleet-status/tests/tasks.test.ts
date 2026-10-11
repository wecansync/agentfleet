import { test, expect, mock } from 'claude-code/testing'
import type { CoreEngineInterface, RenderPropsOf } from 'claude-code'
import { formatAgents, formatTasks, parseReports, REPORT_LIMIT_BYTES } from '../hooks/tasks'
import { readTasks } from '../hooks/register'
import { parseOutages } from '../hooks/status'

const report = { at: '2026-10-09T08:58:32Z', session: 's1', agentId: 'a1', lane: 'docs', model: 'claude-sonnet-4.6', description: 'Document change', files: ['README.md'], forbidden: [{ rule: 'read-only', command: 'git commit' }], apiError: 503, toolCalls: 2, inputTokens: 300, outputTokens: 100, durationSeconds: 15 }
const now = Date.parse('2026-10-09T09:00:00Z')

test('reports keep last 20 valid lines, skipping malformed entries', () => {
  const lines = Array.from({ length: 25 }, (_, index) => JSON.stringify({ ...report, lane: `lane-${index}` }))
  lines.splice(23, 0, '{bad', '[]', JSON.stringify({ ...report, files: 'wrong' }))
  const reports = parseReports(lines.join('\n') + '\n')
  expect(reports.length).toBe(20)
  expect(reports[0]?.lane).toBe('lane-5')
  expect(reports[19]?.lane).toBe('lane-24')
  const sections = formatTasks({ open: true, lanes: [], outages: [], reports })
  expect(sections[1]?.lines.length).toBe(8)
  expect(sections[1]?.lines[0]).toContain('lane-24 · 1 files changed · 1 forbidden (read-only) · API 503')
})
for (const model of ['codex-sol[1m]', 'claude-opus-4.6[1m]', 'vendor/model-name', 'vendor/model@v1+fast', 'a', 'a'.repeat(128)]) {
  test(`task models are accepted and displayed: ${model}`, () => {
    const reports = parseReports(JSON.stringify({ ...report, model }))
    const outages = parseOutages(JSON.stringify({ format: 'agentfleet.outages.v1', lanes: { docs: { model, status: 503, message: 'unavailable', at: report.at, until: '2026-10-09T09:13:32Z' } } }), now)
    expect(reports[0]?.model).toBe(model)
    const sections = formatTasks({ open: true, lanes: [], reports, outages })
    expect(sections[1]?.lines[0]).toContain(model)
    expect(sections[2]?.lines[0]).toContain(model)
  })
}
for (const model of [undefined, null, 7, '', '/vendor/model', 'a'.repeat(129), 'bad model']) {
  test(`invalid report model retains report: ${JSON.stringify(model)}`, () => {
    const reports = parseReports(JSON.stringify({ ...report, model }))
    expect(reports.length).toBe(1)
    expect(reports[0]?.model).toBe('unknown model')
    expect(formatTasks({ open: true, lanes: [], reports, outages: [] })[1]?.lines[0]).toContain('unknown model')
  })
}
test('missing and oversized reports are safe', () => {
  expect(parseReports(null)).toEqual([])
  expect(parseReports('x'.repeat(REPORT_LIMIT_BYTES + 1))).toEqual([])
  const sections = formatTasks({ open: true, lanes: [], reports: [], outages: [] })
  expect(sections.map(item => item.heading)).toEqual(['Running lanes', 'Recent lane reports', 'Outages'])
  expect(sections[1]?.lines).toEqual(['No recent lane reports.'])
})
test('pane agent formatting filters types, truncates descriptions and uses elapsed metadata only when available', () => {
  const lanes = formatAgents([
    { type: 'fleet-docs', status: 'running', description: 'x'.repeat(130), startedAt: '2026-10-09T08:59:00Z' },
    { type: 'fleet-ui', status: 'waiting', description: 'Waiting' },
    { type: 'general-purpose', status: 'running', description: 'Not fleet' },
  ], now)
  expect(lanes.length).toBe(2)
  expect(lanes[0]?.description.length).toBe(100)
  expect(lanes[0]?.elapsedSeconds).toBe(60)
  expect(lanes[1]?.elapsedSeconds).toBeNull()
  expect(formatTasks({ open: true, lanes, reports: [], outages: [] })[0]?.lines[0]).toContain('docs · running · 60s')
})
for (const hostile of ['\u0000', '\u0085', '‮', ' ', ' ', '\n']) {
  test(`task fields reject hostile character ${JSON.stringify(hostile)}`, () => {
    for (const field of ['at', 'session', 'agentId', 'lane', 'description']) {
      expect(parseReports(JSON.stringify({ ...report, [field]: String(report[field as keyof typeof report]) + hostile }))).toEqual([])
    }
    const reports = parseReports(JSON.stringify({ ...report, model: report.model + hostile }))
    expect(reports.length).toBe(1)
    expect(reports[0]?.model).toBe('unknown model')
    const outages = parseOutages(JSON.stringify({ format: 'agentfleet.outages.v1', lanes: { docs: { model: report.model + hostile, status: 503, message: 'unavailable', at: report.at, until: '2026-10-09T09:13:32Z' } } }), now)
    const sections = formatTasks({ open: true, lanes: [], reports, outages })
    expect(sections[1]?.lines[0]).toContain('unknown model')
    expect(sections[2]?.lines[0]).toContain('unknown model')
    expect(JSON.stringify(sections)).not.toContain(hostile)
    expect(parseReports(JSON.stringify({ ...report, files: ['safe' + hostile] }))).toEqual([])
    for (const field of ['rule', 'command']) expect(parseReports(JSON.stringify({ ...report, forbidden: [{ ...report.forbidden[0], [field]: 'safe' + hostile }] }))).toEqual([])
    for (const field of ['type', 'status', 'description', 'startedAt']) {
      const agent = { type: 'fleet-ui', status: 'running', description: 'Safe', startedAt: '2026-10-09T08:59:00Z', [field]: 'safe' + hostile }
      const rendered = JSON.stringify(formatAgents([agent], now))
      expect(rendered).not.toContain(hostile)
    }
  })
}
test('stat bounds report reads and rejects paths outside the known layout', async () => {
  for (const size of [REPORT_LIMIT_BYTES + 1, 200]) {
    let reportReads = 0
    const state = await readTasks({ plugin: { root: '/test/claude/agentfleet/mods/fleet-status' },
      fs: { stat: async (path: string) => ({ kind: 'file', size: path.endsWith('.jsonl') ? size : 100, realPath: path }),
        read: async (path: string) => { if (path.endsWith('.jsonl')) { reportReads++; return JSON.stringify(report) } return '{}' } },
      session: { root: async () => '/test' }, clock: { now: () => now }, agent: { list: async () => [] },
    } as unknown as CoreEngineInterface)
    expect(reportReads).toBe(size > REPORT_LIMIT_BYTES ? 0 : 1)
    expect(state.reports.length).toBe(size > REPORT_LIMIT_BYTES ? 0 : 1)
  }
})
for (const surface of ['terminal', 'desktop'] as const) {
  test(`tasks pane mounts, refreshes and stops on close on ${surface}`, async ($, on) => {
    const clock = mock.clock(on)
    let reads = 0
    on('command.register', (_$, e) => ({ value: { command: e.name } }))
    on('session.start', (_$, e) => ({ cwd: e.cwd }))
    on('agent.list', () => { reads++; return { value: [{ id: 'a1', type: 'fleet-docs', status: 'running', description: `Update ${reads}` }] } })
    on('ui.open', () => ({ value: { isPlaced: true } }))
    on('ui.close', () => ({ value: undefined }))
    on('session.root', () => ({ value: '/test' }))
    await $.session.start({ cwd: '/test', surface, isInteractive: true })
    await $.command.run({ command: 'fleet-tasks', args: '', origin: { kind: 'composer' }, presentation: { isFullscreen: true, columns: 80 } })
    const pane = await $.ui.mount({ plugin: 'fleet-status', surface, component: 'Pane', requestId: 'fleet-tasks', props: { bodyColumns: 70, title: 'AgentFleet tasks' } as RenderPropsOf['Pane'] })
    expect((await pane.find({ text: 'Running lanes' }))?.text).toContain('Running lanes')
    expect((await pane.find({ text: 'docs · running' }))?.text).toContain('Update 1')
    await clock.advance(2_000)
    expect((await pane.find({ text: 'docs · running' }))?.text).toContain('Update 2')
    await pane.press({ key: 'close-tasks' })
    await clock.advance(4_000)
    expect(reads).toBe(2)
  })
}
