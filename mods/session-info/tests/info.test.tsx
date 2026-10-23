import { expect, mock, test } from 'claude-code/testing'
import type { Engine } from 'claude-code/testing'
import type { AgentInfo, On } from 'claude-code'
import { EMPTY, branchFromHead, fit, gitDir, parent, parts, uptime } from '../hooks/info'

const props = {
  hasSurvey: false, isWorking: false, maxRows: 12, bodyColumns: 140,
  scroll: { offset: 0, bodyRows: 12 }, view: {},
}
const BAND = { plugin: 'session-info', component: 'AbovePrompt', props } as const
const START = 1_800_000_000_000

const dir = { kind: 'dir', size: 0, mtimeMs: 0, isLink: false } as const
const file = { kind: 'file', size: 10, mtimeMs: 0, isLink: false } as const

// Everything beneath the plugin: a session in /work/repo/src, on branch
// feat/x, with one lane running.
const defaultAgents: AgentInfo[] = [
  { id: 'a', description: 'lane', type: 'fleet-implement', status: 'running' },
  { id: 'b', description: 'done', type: 'fleet-review', status: 'completed' },
]

function engine(on: On, files: Record<string, string> = { '/work/repo/.git/HEAD': 'ref: refs/heads/feat/x\n' },
  folders: string[] = ['/work/repo/.git'], agents: AgentInfo[] = defaultAgents, turns = () => 3) {
  // The engine's own part of the band: an empty row.
  on('ui.render', ($, e) => {
    const { Box } = $.ui.resolve(e)
    return <Box />
  })
  on('session.start', (_, e) => ({ cwd: e.cwd }))
  on('session.model', () => ({ value: 'claude-opus-5.5[1m]' }))
  on('session.turns', () => ({ value: turns() }))
  on('session.cwd', () => ({ value: '/work/repo/src' }))
  on('session.usage', () => ({ value: { startedAt: START, context: { window: 1_000_000 }, rateLimits: [], cost: { usd: 1.5 } } }))
  on('agent.list', () => ({ value: [...agents] }))
  on('fs.stat', (_, e) => folders.includes(e.path) ? { value: dir }
    : e.path in files ? { value: file } : { deny: `no such file: ${e.path}` })
  on('fs.read', (_, e) => e.path in files ? { value: files[e.path]! } : { deny: `no such file: ${e.path}` })
}

async function rowText($: Engine, surface: 'terminal' | 'desktop', columns = 140): Promise<string> {
  const ui = await $.ui.mount({ ...BAND, props: { ...props, bodyColumns: columns }, surface })
  const texts = await ui.findAll({ type: 'Text' })
  await ui.unmount()
  return texts.map(found => found.text).join('')
}

test('the band shows the model, uptime, lanes, branch, prompts and cost on every surface', async ($, on) => {
  const clock = mock.clock(on, { now: START })
  engine(on)
  await $.session.start({ cwd: '/work/repo/src', surface: 'terminal', isInteractive: true })
  await clock.advance(65 * 60_000)
  for (const surface of ['terminal', 'desktop'] as const) {
    const text = await rowText($, surface)
    expect(text).toContain('◆ claude-opus-5.5[1m]')
    expect(text).toContain('up 1h 05m')
    expect(text).toContain('1 agent running')
    expect(text).toContain('⎇ feat/x')
    expect(text).toContain('3 prompts')
    expect(text).toContain('$1.50 at list price')
  }
})

test('a narrow row leaves out the least useful parts first', async ($, on) => {
  mock.clock(on, { now: START })
  engine(on)
  await $.session.start({ cwd: '/work/repo/src', surface: 'terminal', isInteractive: true })
  const text = await rowText($, 'terminal', 50)
  expect(text).toContain('◆ claude-opus-5.5[1m]')
  expect(text).toContain('up <1m')
  expect(text).not.toContain('at list price')
  expect(text).not.toContain('prompts')
})

test('a worktree reads its own HEAD through the .git file', async ($, on) => {
  mock.clock(on, { now: START })
  engine(on, {
    '/work/repo/.git': 'gitdir: /work/main/.git/worktrees/repo\n',
    '/work/main/.git/worktrees/repo/HEAD': 'ref: refs/heads/fix/wt\n',
  }, [])
  await $.session.start({ cwd: '/work/repo/src', surface: 'terminal', isInteractive: true })
  expect(await rowText($, 'terminal')).toContain('⎇ fix/wt')
})

test('outside a repository there is no branch, and the rest still shows', async ($, on) => {
  mock.clock(on, { now: START })
  engine(on, {}, [])
  await $.session.start({ cwd: '/work/repo/src', surface: 'terminal', isInteractive: true })
  const text = await rowText($, 'terminal')
  expect(text).not.toContain('⎇')
  expect(text).toContain('◆ claude-opus-5.5[1m]')
})

test('the row yields to a survey', async ($, on) => {
  mock.clock(on, { now: START })
  engine(on)
  await $.session.start({ cwd: '/work/repo/src', surface: 'terminal', isInteractive: true })
  const ui = await $.ui.mount({ ...BAND, props: { ...props, hasSurvey: true }, surface: 'terminal' })
  expect(await ui.find({ type: 'Text', text: /claude-opus/ })).toBeUndefined()
  await ui.unmount()
})

test('the agent count follows a subagent starting, a moment later', async ($, on) => {
  const clock = mock.clock(on, { now: START })
  const agents: AgentInfo[] = []
  engine(on, undefined, undefined, agents)
  on('agent.spawn', () => ({ model: 'codex-luna' }))
  await $.session.start({ cwd: '/work/repo/src', surface: 'terminal', isInteractive: true })
  expect(await rowText($, 'terminal')).not.toContain('running')
  agents.push({ id: 'c', description: 'lane', type: 'fleet-implement', status: 'running' })
  await $.agent.spawn({ tool_use_id: 't1', prompt: 'count the tests', description: 'lane', subagentType: 'fleet-implement',
    provider: { plugin: 'engine', tier: 'core' }, parentModel: 'claude-opus-5.5[1m]', background: true, fork: false })
  await clock.advance(1_000)
  expect(await rowText($, 'terminal')).toContain('1 agent running')
})

test('a prompt refreshes the row without waiting for it', async ($, on) => {
  const clock = mock.clock(on, { now: START })
  let turns = 3
  engine(on, undefined, undefined, undefined, () => turns)
  on('prompt.submit', (_, e) => ({ text: e.text }))
  await $.session.start({ cwd: '/work/repo/src', surface: 'terminal', isInteractive: true })
  turns = 4
  await $.prompt.submit({ text: 'hello', wait: false, origin: { kind: 'composer' } })
  expect(await rowText($, 'terminal')).toContain('3 prompts')
  await clock.advance(0)
  expect(await rowText($, 'terminal')).toContain('4 prompts')
})

test('uptime reads in minutes, hours and days', () => {
  expect(uptime(20_000)).toBe('<1m')
  expect(uptime(42 * 60_000)).toBe('42m')
  expect(uptime(3 * 3_600_000 + 5 * 60_000)).toBe('3h 05m')
  expect(uptime(50 * 3_600_000)).toBe('2d 2h')
  expect(uptime(-5)).toBe('<1m')
})

test('parts come most useful first and skip what is unknown', () => {
  const live = { ...EMPTY, model: 'codex-luna', effort: 'high', prompts: 1, costUsd: 0, agents: 2, branch: 'main' }
  expect(parts(live, 0, 60_000).map(p => p.text))
    .toEqual(['◆ codex-luna', 'effort high', 'up 1m', '2 agents running', '⎇ main', '1 prompt'])
  expect(parts(EMPTY, null, 0)).toEqual([])
})

test('fit keeps the leading parts that fit, and always the first', () => {
  const all = parts({ ...EMPTY, model: 'claude-opus-5.5[1m]', prompts: 9 }, 0, 0)
  expect(fit(all, 10).map(p => p.key)).toEqual(['model'])
  expect(fit(all, 200).map(p => p.key)).toEqual(['model', 'uptime', 'prompts'])
})

test('branch and git folder parsing', () => {
  expect(branchFromHead('ref: refs/heads/feat/a-b\n')).toBe('feat/a-b')
  expect(branchFromHead('0123456789abcdef0123456789abcdef01234567')).toBe('detached 0123456')
  expect(branchFromHead('garbage')).toBeNull()
  expect(gitDir('gitdir: ../main/.git/worktrees/x\n', '/w/x')).toBe('/w/x/../main/.git/worktrees/x')
  expect(gitDir('gitdir: C:\\w\\main\\.git\\worktrees\\x', 'C:\\w\\x')).toBe('C:\\w\\main\\.git\\worktrees\\x')
  expect(gitDir('nothing here', '/w')).toBeNull()
})

test('parent walks up to the root on POSIX and Windows paths', () => {
  expect(parent('/work/repo/src')).toBe('/work/repo')
  expect(parent('/work')).toBe('/')
  expect(parent('/')).toBeNull()
  expect(parent('C:\\Users\\me')).toBe('C:\\Users')
  expect(parent('C:\\Users')).toBe('C:\\')
  expect(parent('C:\\')).toBeNull()
  expect(parent('\\\\server\\share\\dir')).toBe('\\\\server\\share')
  expect(parent('\\\\server\\share')).toBeNull()
})
