import { test, expect, mock } from 'claude-code/testing'
import type { CoreEngineInterface, On, RenderPropsOf } from 'claude-code'
import { claudeDirectory, snapshotFromSources, notices, summary, parseOutages, parseSuggestion, parseJev } from '../hooks/status'
import { readSnapshot, readHidden } from '../hooks/register'

// Test the installed layout rather than this repository's source layout.


const props: RenderPropsOf['AbovePrompt'] = {
  hasSurvey: false, isWorking: false, maxRows: 12, bodyColumns: 70,
  scroll: { offset: 0, bodyRows: 12 }, view: {},
}
const start = { cwd: '/test', surface: 'terminal' as const, isInteractive: true }

// The kit supports fs interception through ordinary test hooks, not mock.fs.
// Verify every path: a forbidden read fails instead of silently using real disk.
function sources(on: On, files: Record<string, string>, root = '/test/claude/agentfleet/mods/fleet-status') {
  // Plugin identity is immutable in the kit. Supply installed-layout snapshots
  // through state writes while exercising the real read function separately.
  const readFixture = async () => readSnapshot({
    plugin: { name: 'fleet-status', root },
    session: { root: async () => '/test' },
    clock: { now: () => Date.parse('2026-10-09T09:00:00Z') },
    fs: { stat: async (path: string) => ({ kind: 'file', size: 100, realPath: path }), read: async (path: string) => {
      const name = allowed.find(name => path.endsWith(`/${name}`))
      expect(name).toBeDefined()
      if (!name || files[name] === undefined) throw new Error('Missing fixture')
      return files[name]!
    } },
  } as unknown as CoreEngineInterface)
  let started = false
  let initialVersion: string | null = null
  let previousKey = ''
  let hiddenKey: string | null = null
  on('state.set', { plugin: 'fleet-status', key: 'status' }, async (_$, e, next) => {
    const snapshot = await readFixture()
    if (!started) { initialVersion = snapshot.version; started = true }
    const key = JSON.stringify(notices(snapshot, initialVersion))
    hiddenKey = key !== previousKey ? null : e.value.hiddenNoticeKey ?? hiddenKey
    previousKey = key
    return next({ ...e, value: { ...e.value, snapshot, versionAtStart: initialVersion, hiddenNoticeKey: hiddenKey } })
  })
  const allowed = [
    '.claude-agents-config-manifest.json', 'agentfleet/active',
    'agentfleet/auto-update.json', 'fleet-model-proposal.json',
    'agentfleet/config.json', 'agentfleet/outages.json', 'agentfleet/suggestions.json', 'agentfleet/jev-stats.json', 'agentfleet/suggestions-hidden.json', 'agentfleet/wizard.json',
  ]
  on('fs.stat', (_$, e) => {
    // The kit may hand back native Windows paths (C:\...); compare in the POSIX form the mod builds.
    const path = e.path.replace(/^[A-Za-z]:/, '').replace(/\\/g, '/')
    return { value: { kind: path.endsWith('/agentfleet') ? 'directory' : 'file', size: 100, mtimeMs: 0, isLink: false, realPath: path } }
  })
  on('session.root', () => ({ value: '/test' }))
  on('fs.read', (_$, e) => {
    const normalized = e.path.replace(/\\/g, '/')
    const name = allowed.find(name => normalized.endsWith(`/${name}`))
    expect(name).toBeDefined()
    if (!name || files[name] === undefined) return { deny: 'Missing fixture' }
    return { value: files[name]! }
  })
  on('command.register', (_$, e) => ({ value: { command: e.name } }))
  on('session.start', (_$, e) => ({ cwd: e.cwd }))
  on('ui.render', { component: 'AbovePrompt' }, () => ({ type: 'engine', ref: 0 }))
  return mock.clock(on)
}
function healthy() {
  return {
    '.claude-agents-config-manifest.json': '{"version":"2.1.0"}',
    'fleet-model-proposal.json': '{"decision":"approved","missing_lane_models":{}}',
    'agentfleet/auto-update.json': '{"enabled":true}',
  }
}

test('healthy sources show no band and a fresh command summary', async ($, on) => {
  const files = healthy()
  sources(on, files)
  await $.session.start(start)
  const ui = await $.ui.mount({ plugin: 'fleet-status', surface: 'terminal', component: 'AbovePrompt', props })
  expect(await ui.drawn()).toEqual({ type: 'engine', ref: 0 })
  const reply = { text: summary(snapshotFromSources('/test/claude', [files['.claude-agents-config-manifest.json'], null, files['agentfleet/auto-update.json'], files['fleet-model-proposal.json']]), '2.1.0') }
  expect(reply.text).toContain('2.1.0')
  expect(reply.text).toContain('No action needed.')
  files['.claude-agents-config-manifest.json'] = '{"version":"2.2.0"}'
  expect(summary(snapshotFromSources('/test/claude', [files['.claude-agents-config-manifest.json'], null, files['agentfleet/auto-update.json'], files['fleet-model-proposal.json']]), '2.1.0')).toContain('2.2.0')
})

for (const surface of ['terminal', 'desktop'] as const) {
  test(`pending review and Hide on ${surface}; changed notices reappear`, async ($, on) => {
    const files = healthy()
    files['fleet-model-proposal.json'] = '{"decision":"pending","missing_lane_models":{}}'
    const clock = sources(on, files)
    await $.session.start(start)
    const ui = await $.ui.mount({ plugin: 'fleet-status', surface, component: 'AbovePrompt', props })
    expect((await ui.find({ text: 'Model changes are waiting for review.' }))?.text).toContain('/fleet-setup')
    await ui.press({ key: 'hide' })
    expect(await ui.drawn()).toEqual({ type: 'engine', ref: 0 })
    await clock.advance(60_000)
    expect(await ui.drawn()).toEqual({ type: 'engine', ref: 0 })
    files['fleet-model-proposal.json'] = '{"decision":"pending","missing_lane_models":{"ui":{}}}'
    await clock.advance(60_000)
    expect((await ui.find({ text: '1 fleet lane has' }))?.text).toContain('no available model')
    files['fleet-model-proposal.json'] = '{"decision":"pending","missing_lane_models":{}}'
    await clock.advance(60_000)
    expect((await ui.find({ text: 'Model changes are waiting for review.' }))?.text).toContain('/fleet-setup')
    const survey = await $.ui.mount({ plugin: 'fleet-status', surface, component: 'AbovePrompt', props: { ...props, hasSurvey: true } })
    expect(await survey.drawn()).toEqual({ type: 'engine', ref: 0 })
  })
}

test('refresh compares the installed update to the session start version', async ($, on) => {
  const files = healthy()
  files['agentfleet/auto-update.json'] = '{"lastResult":{"version":"2.0.6","ok":true}}'
  const clock = sources(on, files)
  await $.session.start(start)
  const ui = await $.ui.mount({ plugin: 'fleet-status', surface: 'terminal', component: 'AbovePrompt', props })
  expect(await ui.drawn()).toEqual({ type: 'engine', ref: 0 })
  files['.claude-agents-config-manifest.json'] = '{"version":"2.2.0"}'
  // A manual update changes only the manifest; lastResult remains stale.
  await clock.advance(60_000)
  expect((await ui.find({ text: 'AgentFleet 2.2.0 is installed.' }))?.text).toContain('Restart Claude Code')
})

test('manifest missing at start never triggers a restart notice', async ($, on) => {
  const files: Record<string, string> = healthy()
  delete files['.claude-agents-config-manifest.json']
  files['agentfleet/auto-update.json'] = '{"lastResult":{"version":"2.0.6","ok":true}}'
  const clock = sources(on, files)
  await $.session.start(start)
  const ui = await $.ui.mount({ plugin: 'fleet-status', surface: 'terminal', component: 'AbovePrompt', props })
  expect(await ui.drawn()).toEqual({ type: 'engine', ref: 0 })
  files['.claude-agents-config-manifest.json'] = '{"version":"2.2.0"}'
  await clock.advance(60_000)
  expect(await ui.drawn()).toEqual({ type: 'engine', ref: 0 })
  const reply = await $.command.run({ command: 'fleet-status', args: '', origin: { kind: 'composer' }, presentation: { isFullscreen: false, columns: 80 } })
  expect(reply.text).toContain('No action needed.')
})

for (const bad of [undefined, '{bad json', '[]', '{"version":7,"lastResult":{"ok":"false"},"decision":true,"missing_lane_models":[]}']) {
  test(`absent or malformed sources are safe: ${bad ?? 'missing'}`, async ($, on) => {
    const files: Record<string, string> = {}
    if (bad !== undefined) {
      for (const name of ['.claude-agents-config-manifest.json', 'agentfleet/auto-update.json', 'fleet-model-proposal.json']) files[name] = bad
    }
    sources(on, files)
    await $.session.start(start)
    const ui = await $.ui.mount({ plugin: 'fleet-status', surface: 'terminal', component: 'AbovePrompt', props })
    expect(await ui.drawn()).toEqual({ type: 'engine', ref: 0 })
    expect((await $.command.run({ command: 'fleet-status', args: '', origin: { kind: 'composer' }, presentation: { isFullscreen: false, columns: 80 } })).text).toContain('No action needed.')
  })
}

test('failed update, missing lanes and pending review retain notice order', async ($, on) => {
  const files = healthy()
  files['agentfleet/auto-update.json'] = '{"lastResult":{"version":"2.2.0","ok":false},"reported":false}'
  files['fleet-model-proposal.json'] = '{"decision":"pending","missing_lane_models":{"ui":{},"review":{}}}'
  const clock = sources(on, files)
  await $.session.start(start)
  const ui = await $.ui.mount({ plugin: 'fleet-status', surface: 'desktop', component: 'AbovePrompt', props })
  const lines = await ui.findAll({ type: 'Text' })
  expect(lines[1]?.text).toContain('update to 2.2.0 failed. See ')
  expect(lines[1]?.text).toContain('/agentfleet/auto-update.log')
  expect(lines[2]?.text).toContain('2 fleet lanes have')
  expect(lines[3]?.text).toContain('waiting for review')
  files['agentfleet/auto-update.json'] = '{"lastResult":{"version":"2.2.0","ok":false},"reported":true}'
  await clock.advance(60_000)
  expect(await ui.find({ text: 'failed. See' })).toBeUndefined()
})

test('nonmatching root attempts no reads and shows no band', async ($, on) => {
  let reads = 0
  const snapshot = await readSnapshot({
    plugin: { name: 'fleet-status', root: '/unrelated/mods/fleet-status' },
    fs: { read: async () => { reads++; throw new Error('Unexpected read') } },
  } as unknown as CoreEngineInterface)
  expect(reads).toBe(0)
  expect(snapshot).toEqual(snapshotFromSources(null, []))
  sources(on, healthy(), '/unrelated/mods/fleet-status')
  await $.session.start(start)
  const ui = await $.ui.mount({ plugin: 'fleet-status', surface: 'terminal', component: 'AbovePrompt', props })
  expect(await ui.drawn()).toEqual({ type: 'engine', ref: 0 })
})

for (const profile of ['normal\nsecret', 'x'.repeat(65), 'safe‮hidden']) {
  test(`unsafe profile defaults: ${JSON.stringify(profile)}`, async () => {
    const snapshot = await readSnapshot({
      plugin: { name: 'fleet-status', root: '/test/claude/agentfleet/mods/fleet-status' },
      session: { root: async () => '/test' },
      clock: { now: () => 0 },
      fs: { stat: async (path: string) => ({ kind: 'file', size: 100, realPath: path }), read: async (path: string) => path.endsWith('/active') ? profile : '{}' },
    } as unknown as CoreEngineInterface)
    expect(snapshot.profile).toBeNull()
    expect(summary(snapshot, null)).toContain('Profile: not recorded')
    expect(summary(snapshot, null)).not.toContain(profile)
  })
}

test('malformed versions and timestamps are absent and never request restart', async () => {
  const snapshot = snapshotFromSources('/test/claude', [
    '{"version":"2.2.0 injected"}', null,
    '{"latestSeen":"bad","lastCheck":"x","lastResult":{"version":"bad","ok":true}}', '{}',
  ])
  expect(snapshot.version).toBeNull()
  expect(snapshot.latestSeen).toBeNull()
  expect(snapshot.lastCheck).toBeNull()
  expect(snapshot.lastResult?.version).toBeNull()
  expect(summary(snapshot, '2.1.0')).toContain('AgentFleet version: unknown')
  expect(notices(snapshot, '2.1.0')).toEqual([])
})

const wizardKeys23 = ['compact', 'jev', 'reviewerAccess', 'learn', 'statusBand', 'laneGuard', 'autoUpdate']
const wizardKeys24 = [...wizardKeys23, 'workStyle', 'agentBrain']
const wizardSource = (asked: unknown) => JSON.stringify({ format: 'agentfleet.wizard.v1', asked, version: '2.3.0' })
const optionsNotice = 'New AgentFleet options are available: run agentfleet configure in a terminal.'
function optionsSnapshot(source: string | null, version = '2.3.0') {
  return snapshotFromSources('/test/claude', [JSON.stringify({ version }), null, null, null, null, null, null, null, source])
}
for (const [label, source, count] of [
  ['missing', null, 7],
  ['partial', wizardSource(['compact', 'jev', 'compact', 'future']), 5],
  ['full', wizardSource(wizardKeys23), 0],
  ['full with unknown key', wizardSource([...wizardKeys23, 'future']), 0],
  ['invalid JSON', '{bad', 7],
  ['invalid format', '{"format":"bad","asked":[]}', 7],
  ['invalid asked', wizardSource('compact'), 7],
  ['invalid entry', wizardSource([...wizardKeys23, 7]), 7],
] as const) {
  test(`wizard options ${label} and summary`, () => {
    const snapshot = optionsSnapshot(source)
    expect(snapshot.newOptions).toBe(count)
    expect(notices(snapshot, '2.3.0')).toEqual(count ? [optionsNotice] : [])
    expect(summary(snapshot, '2.3.0')).toContain(count ? `Options: ${count} new (run agentfleet configure)` : 'Options: all reviewed')
  })
}
for (const version of ['2.2.99', '2.1.0', '1.99.99', '2.3.0-rc.1', 'bad']) {
  test(`older or unknown manifest ${version} never announces options`, () => {
    const snapshot = optionsSnapshot(null, version)
    expect(notices(snapshot, snapshot.version)).toEqual([])
    expect(summary(snapshot, snapshot.version)).not.toContain('Options:')
  })
}
for (const version of ['2.3.0+build.1', '2.3.1', '2.4.0', '3.0.0']) {
  test(`manifest ${version} announces new options`, () => {
    expect(notices(optionsSnapshot(null, version), version)).toEqual([optionsNotice])
  })
}
test('2.4.0 counts new wizard questions while 2.3.x keeps the legacy seven', () => {
  expect(snapshotFromSources(null, [JSON.stringify({ version: '2.3.1' }), null, null, null, null, null, null, null, null]).newOptions).toBe(7)
  expect(snapshotFromSources(null, [JSON.stringify({ version: '2.3.1' }), null, null, null, null, null, null, null, wizardSource(wizardKeys23)]).newOptions).toBe(0)
  expect(snapshotFromSources(null, [JSON.stringify({ version: '2.4.0' }), null, null, null, null, null, null, null, null]).newOptions).toBe(9)
  expect(snapshotFromSources(null, [JSON.stringify({ version: '2.4.0' }), null, null, null, null, null, null, null, wizardSource(wizardKeys24)]).newOptions).toBe(0)
})

test('wizard notice uses guarded read and stays hidden after refresh', async ($, on) => {
  const files: Record<string, string> = healthy()
  files['.claude-agents-config-manifest.json'] = '{"version":"2.3.0"}'
  files['agentfleet/wizard.json'] = wizardSource(['compact'])
  const clock = sources(on, files)
  await $.session.start(start)
  const ui = await $.ui.mount({ plugin: 'fleet-status', surface: 'terminal', component: 'AbovePrompt', props })
  expect((await ui.find({ text: 'New AgentFleet options are available' }))?.text).toContain(optionsNotice)
  await ui.press({ key: 'hide' })
  await clock.advance(60_000)
  expect(await ui.drawn()).toEqual({ type: 'engine', ref: 0 })
  files['agentfleet/wizard.json'] = wizardSource(wizardKeys23)
  await clock.advance(60_000)
  expect(await ui.drawn()).toEqual({ type: 'engine', ref: 0 })
})

const now = Date.parse('2026-10-09T09:00:00Z')
const outage = { model: 'claude-sonnet-4.6', status: 503, message: 'unavailable', at: '2026-10-09T08:58:32Z', until: '2026-10-09T09:13:32Z' }
const outagesSource = (lanes: Record<string, unknown>) => JSON.stringify({ format: 'agentfleet.outages.v1', lanes })
const suggestion = { name: 'project', catalog: '2.2.0', pending: 3, seen: null, new: [{ id: 'typescript-lsp', title: 'TypeScript LSP', kind: 'plugin', why: 'TypeScript files detected' }] }
const suggestionsSource = (item: unknown) => JSON.stringify({ format: 'agentfleet.suggestions.v1', projects: { '/test': item } })

test('missing sources use AgentFleet defaults', () => {
  const result = summary(snapshotFromSources(null, []), null)
  for (const line of ['Profile: not recorded', 'Auto-update: enabled (default)', "Auto-compact: 70% of each model's context", 'Reviewer access: full', 'Work style: balanced', 'Suggestions learning: on', 'Lane outages: none']) expect(result).toContain(line)
  expect(summary(snapshotFromSources(null, [null, null, '{"enabled":"yes"}']), null)).toContain('enabled (default)')
})
for (const percent of [20, 40, 70, 95, 'off', 19, 96, 70.5, '70', null]) {
  test(`config compact window ${percent}`, () => {
    const config = JSON.stringify({ format: 'agentfleet.config.v1', compactPercent: percent, reviewerAccess: 'read-only', learn: true })
    const snapshot = snapshotFromSources(null, [null, null, null, null, config])
    expect(snapshot.compactPercent).toBe(percent === 'off' || (typeof percent === 'number' && Number.isInteger(percent) && percent >= 20 && percent <= 95) ? percent : 70)
    expect(snapshot.reviewerAccess).toBe('read-only')
    expect(snapshot.learn).toBe(true)
    if (percent === 'off') expect(summary(snapshot, null)).toContain('off (AgentFleet leaves the window alone)')
  })
}
test('invalid config values and format use defaults', () => {
  for (const config of [{ reviewerAccess: 'root', learn: 'true' }, { reviewerAccess: 'full‮', learn: 1 }, { format: 'bad', compactPercent: 'off' }]) {
    const snapshot = snapshotFromSources(null, [null, null, null, null, JSON.stringify({ format: 'agentfleet.config.v1', ...config })])
    expect(snapshot.reviewerAccess).toBe('full')
    expect(snapshot.learn).toBe(true)
    expect(snapshot.compactPercent).toBe(70)
  }
  expect(snapshotFromSources(null, [null, null, null, null, '{"format":"agentfleet.config.v1","reviewerAccess":"run"}']).reviewerAccess).toBe('run')
})
test('outages expire and alternatives come only from the watcher file', () => {
  const current = parseOutages(outagesSource({ docs: { ...outage, alternative: 'custom-alt' }, ui: outage, tests: { ...outage, until: '2026-10-09T09:00:00Z' } }), now)
  expect(current.map(item => item.lane)).toEqual(['docs', 'ui'])
  expect(current[0]?.alternative).toBe('custom-alt')
  const snapshot = snapshotFromSources(null, [], null, now)
  snapshot.outages = current
  expect(notices(snapshot, null)[0]).toBe('fleet-docs is failing (503 at 08:58 UTC; claude-sonnet-4.6). Dispatch fleet-custom-alt until 09:13 UTC.')
  expect(notices(snapshot, null)[1]).toContain('Retry it after 09:13 UTC.')
  expect(summary(snapshot, null)).toContain('Lane outages: fleet-docs 503 until 09:13 UTC')
})
for (const alternative of [undefined, null, '', 'Bad', '../ui', 'ui\n', 'ui‮', 7]) {
  test(`missing or invalid alternative recommends retry: ${JSON.stringify(alternative)}`, () => {
    const snapshot = snapshotFromSources(null, [null, null, null, null, null, outagesSource({ docs: { ...outage, alternative } })], null, now)
    expect(snapshot.outages[0]?.alternative).toBeNull()
    expect(notices(snapshot, null)[0]).toContain('Retry it after 09:13 UTC.')
  })
}
test('suggestions match only the project and unseen catalog with pending work', () => {
  const source = suggestionsSource(suggestion)
  expect(parseSuggestion(source, '/other')).toBeNull()
  expect(parseSuggestion(source, '/test/nested')).toBeNull()
  for (const item of [suggestion, { ...suggestion, seen: '2.2.0' }, { ...suggestion, pending: 0 }]) {
    const snapshot = snapshotFromSources(null, [null, null, null, null, null, null, suggestionsSource(item)], '/test', now)
    expect(notices(snapshot, null).length).toBe(item.pending > 0 ? 1 : 0)
    expect(summary(snapshot, null)).toContain(`Suggestions for project: ${item.pending} (run /fleet-suggest)`)
  }
})
for (const hostile of ['\u0000', '\u0085', '‮', ' ', ' ', '\n']) {
  test(`new status strings reject hostile character ${JSON.stringify(hostile)}`, () => {
    for (const field of ['message', 'at', 'until']) expect(parseOutages(outagesSource({ docs: { ...outage, [field]: String(outage[field as keyof typeof outage]) + hostile } }), now).length).toBe(0)
    const snapshot = snapshotFromSources(null, [null, null, null, null, null, outagesSource({ docs: { ...outage, model: outage.model + hostile } })], null, now)
    expect(snapshot.outages.length).toBe(1)
    expect(snapshot.outages[0]?.model).toBe('unknown model')
    expect(notices(snapshot, null)[0]).toContain('unknown model')
    expect(notices(snapshot, null)[0]).not.toContain(hostile)
    expect(parseOutages(outagesSource({ ['docs' + hostile]: outage }), now).length).toBe(0)
    for (const field of ['name', 'catalog', 'seen']) expect(parseSuggestion(suggestionsSource({ ...suggestion, [field]: 'safe' + hostile }), '/test')).toBeNull()
    expect(parseSuggestion(suggestionsSource(suggestion), '/test' + hostile)).toBeNull()
  })
}
for (const model of ['codex-sol[1m]', 'claude-opus-4.6[1m]', 'vendor/model-name', 'vendor/model@v1+fast', 'a', 'a'.repeat(128)]) {
  test(`gateway outage model is accepted and displayed: ${model}`, () => {
    const snapshot = snapshotFromSources(null, [null, null, null, null, null, outagesSource({ docs: { ...outage, model } })], null, now)
    expect(snapshot.outages[0]?.model).toBe(model)
    expect(notices(snapshot, null)[0]).toContain(model)
  })
}
for (const model of [undefined, null, 7, '', '/vendor/model', 'a'.repeat(129), 'bad model']) {
  test(`invalid outage model retains notice: ${JSON.stringify(model)}`, () => {
    const snapshot = snapshotFromSources(null, [null, null, null, null, null, outagesSource({ docs: { ...outage, model } })], null, now)
    expect(snapshot.outages.length).toBe(1)
    expect(snapshot.outages[0]?.model).toBe('unknown model')
    expect(notices(snapshot, null)[0]).toContain('unknown model')
  })
}
for (const surface of ['terminal', 'desktop'] as const) {
  test(`outage and suggestions notice sets retain Hide behavior on ${surface}`, async ($, on) => {
    const files: Record<string, string> = healthy()
    files['agentfleet/outages.json'] = outagesSource({ docs: { ...outage, model: 'codex-sol[1m]', alternative: 'ui' } })
    files['agentfleet/suggestions.json'] = suggestionsSource(suggestion)
    const clock = sources(on, files)
    await $.session.start(start)
    const ui = await $.ui.mount({ plugin: 'fleet-status', surface, component: 'AbovePrompt', props })
    expect((await ui.find({ text: 'fleet-docs is failing' }))?.text).toContain('Dispatch fleet-ui')
    expect((await ui.find({ text: 'AgentFleet suggests TypeScript LSP' }))?.text).toContain('/fleet-suggest')
    await ui.press({ key: 'hide' })
    await clock.advance(60_000)
    expect(await ui.drawn()).toEqual({ type: 'engine', ref: 0 })
    files['agentfleet/suggestions.json'] = suggestionsSource({ ...suggestion, pending: 4, new: [{ id: 'php-lsp', title: 'PHP LSP', kind: 'plugin', why: 'PHP files detected' }] })
    await clock.advance(60_000)
    expect((await ui.find({ text: 'AgentFleet suggests PHP LSP' }))?.text).toContain('/fleet-suggest')
    files['agentfleet/suggestions.json'] = suggestionsSource({ ...suggestion, seen: '2.2.0', pending: 0, new: [] })
    await clock.advance(60_000)
    expect(await ui.find({ text: 'AgentFleet suggests' })).toBeUndefined()
  })
}
test('known source symlinks cannot cause reads of secrets or settings', async () => {
  let reads = 0
  const snapshot = await readSnapshot({ plugin: { root: '/test/claude/agentfleet/mods/fleet-status' },
    fs: { stat: async () => ({ kind: 'file', size: 100, realPath: '/test/claude/agentfleet/secrets/key' }), read: async () => { reads++; return '{}' } },
    session: { root: async () => '/test' }, clock: { now: () => now },
  } as unknown as CoreEngineInterface)
  expect(reads).toBe(0)
  expect(snapshot.suggestion).toBeNull()
})

test('installation paths handle custom directories and both separators', () => {
  expect(claudeDirectory('/custom/claude/agentfleet/mods/fleet-status')).toBe('/custom/claude')
  expect(claudeDirectory('C:\\custom\\claude\\agentfleet\\mods\\fleet-status\\')).toBe('C:/custom/claude')
  expect(claudeDirectory('/agentfleet/mods/fleet-status')).toBeNull()
  expect(claudeDirectory('C:\\agentfleet\\mods\\fleet-status')).toBeNull()
  expect(claudeDirectory('/custom/claude/mods/fleet-status')).toBeNull()
})

const jevSource = (changes: Record<string, unknown> = {}) => JSON.stringify({
  format: 'agentfleet.jev-stats.v1', updated: '2026-10-09T09:00:00Z',
  totals: { compactions: 3, jev: 2, fallbacks: 1, tokensSaved: 100 },
  recent: [{ at: '2026-10-09T08:58:00Z', outcome: 'jev', trigger: 'auto', tokensBefore: 500, tokensAfter: 400,
    messagesBefore: 10, messagesKept: 8, ms: 20, reason: null }], ...changes,
})
test('strict JEV summary includes timestamp and token savings', () => {
  const snapshot = snapshotFromSources(null, [null, null, null, null, null, null, null, jevSource()])
  expect(summary(snapshot, null)).toContain('JEV: 3 compactions (2 verbatim, 1 fallbacks), last at 08:58 UTC; 500 → 400 tokens')
  for (const source of [null, '{}', jevSource({ updated: 'bad' }), jevSource({ totals: { compactions: -1 } }), jevSource({ recent: [{}] })]) {
    expect(parseJev(source)).toBeNull()
  }
})
test('JEV summary accepts null per-attempt counts from fallback and no-key entries', () => {
  const fallback = { at: '2026-10-09T08:59:00Z', outcome: 'no-key', trigger: 'auto', tokensBefore: null, tokensAfter: null,
    messagesBefore: null, messagesKept: null, ms: 0, reason: 'built-in summary' }
  expect(parseJev(jevSource({ recent: [fallback] }))).not.toBeNull()
  expect(parseJev(jevSource({ recent: [{ ...fallback, messagesBefore: 'x' }] }))).toBeNull()
})
test('all new suggestion fields reject hostile values and excessive lengths', () => {
  for (const field of ['id', 'title', 'kind', 'why']) {
    for (const value of ['bad\n', 'safe‮', 'a'.repeat(501), 1]) {
      const item = { ...suggestion.new[0], [field]: value }
      const snapshot = snapshotFromSources(null, [null, null, null, null, null, null, suggestionsSource({ ...suggestion, new: [item] })], '/test')
      expect(notices(snapshot, null)).toEqual([])
    }
  }
})
test('Hide tracks ids rather than changed titles or pending counts', async ($, on) => {
  const files: Record<string, string> = healthy()
  files['agentfleet/suggestions.json'] = suggestionsSource(suggestion)
  const clock = sources(on, files)
  await $.session.start(start)
  const ui = await $.ui.mount({ plugin: 'fleet-status', surface: 'terminal', component: 'AbovePrompt', props })
  const drawn = await ui.drawn()
  expect(JSON.stringify(drawn)).toContain('"type":"engine"')
  expect((await ui.find({ text: 'AgentFleet suggests' }))?.text).toContain('(+2 more)')
  await ui.press({ key: 'hide' })
  files['agentfleet/suggestions.json'] = suggestionsSource({ ...suggestion, pending: 7, new: [{ ...suggestion.new[0], title: 'Changed title' }] })
  await clock.advance(60_000)
  expect(await ui.find({ text: 'AgentFleet suggests' })).toBeUndefined()
})

test('prompt submission and turn completion refresh without waiting for the timer', async ($, on) => {
  const files: Record<string, string> = healthy()
  files['agentfleet/suggestions.json'] = suggestionsSource(suggestion)
  sources(on, files)
  let prompts = 0, completions = 0
  on('prompt.submit', (_$, e) => { prompts++; return { text: e.text } })
  on('turn.complete', (_$, e) => { completions++; return { text: e.answer } })
  await $.session.start(start)
  const ui = await $.ui.mount({ plugin: 'fleet-status', surface: 'terminal', component: 'AbovePrompt', props })
  expect(await ui.find({ text: 'AgentFleet suggests' })).toBeDefined()
  files['agentfleet/suggestions.json'] = suggestionsSource({ ...suggestion, pending: 0, new: [] })
  await $.prompt.submit({ text: 'Continue with the project' })
  expect(prompts).toBe(1)
  expect(await ui.find({ text: 'AgentFleet suggests' })).toBeUndefined()
  files['agentfleet/suggestions.json'] = suggestionsSource(suggestion)
  await $.turn.complete({ answer: 'Done' } as never)
  expect(completions).toBe(1)
  expect(await ui.find({ text: 'AgentFleet suggests' })).toBeDefined()
})

test('hidden ids persist only in the allowed guarded file', async ($, on) => {
  const files: Record<string, string> = healthy()
  files['agentfleet/suggestions.json'] = suggestionsSource(suggestion)
  sources(on, files)
  let written = ''
  on('fs.exists', () => ({ value: false }))
  on('fs.write', (_$, e) => {
    expect(e.path.replace(/\\/g, '/').endsWith('/agentfleet/suggestions-hidden.json')).toBe(true)
    written = e.text
    return { value: undefined }
  })
  // Source-layout test identity cannot write; exercise the installed directory through state.
  await $.session.start(start)
  const ui = await $.ui.mount({ plugin: 'fleet-status', surface: 'terminal', component: 'AbovePrompt', props })
  await ui.press({ key: 'hide' })
  expect(JSON.parse(written).ids).toEqual(['typescript-lsp'])
  expect(await readHidden({ plugin: { root: '/test/claude/agentfleet/mods/fleet-status' },
    fs: { stat: async (path: string) => ({ kind: 'file', realPath: path, size: 100 }), read: async () => written },
  } as unknown as CoreEngineInterface)).toEqual(['typescript-lsp'])
})
