import { atom, read, update } from 'claude-code'
import type { CoreEngineInterface, Register, Timer } from 'claude-code'
import type { FleetState, Snapshot, TasksState } from '../types'
import { claudeDirectory, notices, snapshotFromSources, summary } from './status'
import { formatAgents, formatTasks, parseReports, REPORT_LIMIT_BYTES } from './tasks'

export async function readSnapshot($: CoreEngineInterface): Promise<Snapshot> {
  const claudeDir = claudeDirectory($.plugin.root)
  if (claudeDir === null) return snapshotFromSources(null, [])
  const base = claudeDir.replace(/\/$/, '')
  async function readSource(path: string): Promise<string | null> {
    const knownPath = `${base}/${path}`
    try {
      const stat = await $.fs.stat(knownPath, { resolve: true })
      if (stat.kind !== 'file' || stat.realPath !== knownPath || stat.size > REPORT_LIMIT_BYTES) return null
      return await $.fs.read(knownPath)
    } catch { return null }
  }
  const sources = await Promise.all([
    readSource('.claude-agents-config-manifest.json'),
    readSource('agentfleet/active'),
    readSource('agentfleet/auto-update.json'),
    readSource('fleet-model-proposal.json'),
    readSource('agentfleet/config.json'),
    readSource('agentfleet/outages.json'),
    readSource('agentfleet/suggestions.json'),
  ])
  const project = await $.session.root().catch(() => null)
  return snapshotFromSources(claudeDir, sources, project, await $.clock.now())
}

function refreshed(current: FleetState, snapshot: Snapshot): FleetState {
  const previousKey = JSON.stringify(current.snapshot ? notices(current.snapshot, current.versionAtStart) : [])
  const newKey = JSON.stringify(notices(snapshot, current.versionAtStart))
  return { ...current, snapshot, hiddenNoticeKey: previousKey === newKey ? current.hiddenNoticeKey : null }
}

const status = atom({ plugin: 'fleet-status', key: 'status' } as const, {
  snapshot: null, versionAtStart: null, hiddenNoticeKey: null,
} as FleetState)

const PANE = 'fleet-tasks'
const tasks = atom({ plugin: 'fleet-status', key: 'tasks' } as const, {
  open: false, lanes: [], reports: [], outages: [],
} as TasksState)

export async function readTasks($: CoreEngineInterface): Promise<TasksState> {
  const snapshot = await readSnapshot($)
  let source: string | null = null
  if (snapshot.claudeDir) {
    const path = `${snapshot.claudeDir}/agentfleet/lane-reports.jsonl`
    try {
      const stat = await $.fs.stat(path, { resolve: true })
      // Refuse links outside the known layout (including secrets/profiles).
      if (stat.kind === 'file' && stat.size <= REPORT_LIMIT_BYTES && stat.realPath === path) source = await $.fs.read(path)
    } catch { /* Missing, oversized or unresolvable reports are optional. */ }
  }
  const agents = await $.agent.list().catch(() => [])
  return { open: true, lanes: formatAgents(agents, await $.clock.now()), reports: parseReports(source), outages: snapshot.outages }
}

export const register: Register = on => {
  let paneTimer: Timer | null = null
  on('session.start', async ($, e, next) => {
    const snapshot = await readSnapshot($)
    await update($, status, () => ({ snapshot, versionAtStart: snapshot.version, hiddenNoticeKey: null }))
    await $.command.register({
      name: 'fleet-status',
      description: 'Show AgentFleet status: version, profile, updates, and model proposals',
    })
    await $.command.register({ name: 'fleet-tasks', description: 'Show AgentFleet lanes, recent reports, and outages in a tasks pane' })
    $.clock.every(60_000, async () => {
      const snapshot = await readSnapshot($)
      await update($, status, current => refreshed(current, snapshot))
    })
    return next(e)
  })

  on('command.run', { command: 'fleet-status' }, async $ => {
    const snapshot = await readSnapshot($)
    const current = await read($, status)
    await update($, status, state => refreshed(state, snapshot))
    return { text: summary(snapshot, current.versionAtStart) }
  })

  on('command.run', { command: 'fleet-tasks' }, async $ => {
    paneTimer?.cancel()
    paneTimer = null
    const initial = await readTasks($)
    await update($, tasks, () => initial)
    const opened = await $.ui.open({ id: PANE, title: 'AgentFleet tasks', focus: true, closeOnEscape: true })
    if (opened.isPlaced) {
      paneTimer = $.clock.every(2_000, async () => {
        if (!(await read($, tasks)).open) return
        const fresh = await readTasks($)
        await update($, tasks, current => current.open ? fresh : current)
      })
    } else {
      await update($, tasks, current => ({ ...current, open: false }))
    }
    return { text: opened.isPlaced ? 'AgentFleet tasks pane opened.' : 'AgentFleet tasks pane is unavailable on this surface.' }
  })

  on('ui.close', { id: PANE }, async ($, e, next) => {
    paneTimer?.cancel()
    paneTimer = null
    await update($, tasks, current => ({ ...current, open: false }))
    return next(e)
  })
  on('session.end', async ($, e, next) => {
    paneTimer?.cancel()
    paneTimer = null
    return next(e)
  })
  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const { Box, Text, Button } = $.ui.resolve(e)
    const current = await read($, tasks)
    return (
      <Box flexDirection="column" width={e.props.bodyColumns}>
        <Button key="close-tasks" label="Close" role="dismiss" onPress={async () => {
          paneTimer?.cancel()
          paneTimer = null
          await update($, tasks, state => ({ ...state, open: false }))
          await $.ui.close({ id: PANE })
        }} />
        {formatTasks(current).map(section => (
          <Box key={section.heading} flexDirection="column">
            <Text bold>{section.heading}</Text>
            {section.lines.map((line, index) => <Text key={`${section.heading}-${index}`} wrap="wrap">{line}</Text>)}
          </Box>
        ))}
      </Box>
    )
  })

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    if (e.props.hasSurvey) return next(e)
    const current = await read($, status)
    const lines = current.snapshot ? notices(current.snapshot, current.versionAtStart) : []
    const key = JSON.stringify(lines)
    if (!lines.length || current.hiddenNoticeKey === key) return next(e)
    const { Box, Text, Button } = $.ui.resolve(e)
    return (
      <Box flexDirection="column" width={e.props.bodyColumns}>
        <Box>
          <Text dimColor>AgentFleet </Text>
          <Button key="hide" label="Hide" onPress={() => update($, status, state => ({ ...state, hiddenNoticeKey: key }))} />
        </Box>
        {lines.map((line, index) => <Text key={`notice-${index}`} wrap="wrap">{line}</Text>)}
      </Box>
    )
  })
}
