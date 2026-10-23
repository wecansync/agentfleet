// Session info: one row above the prompt with the current model and effort,
// how long this Claude Code has been running, the subagents running now, the
// git branch, the prompts sent and the session's cost at list price.
//
// session.start: note when this session began (kept in $.state, so a mod
// reload does not reset the uptime), read everything once and start a
// one-minute timer for the uptime and the branch.
// turn.step (main loop only): note the effort the request asks for. The
// model always comes from $.session.model(), as /model shows it, so the row
// does not switch to a fallback's name for one request.
// prompt.submit, turn.complete, agent.spawn: read again, on the clock rather
// than in the hook, so a prompt or a subagent never waits for the row. The
// agent list changes just after a subagent starts or finishes, so those
// re-read it a moment later.
// ui.render (AbovePrompt): the other mods' part of the band, then this row.
//
// The host reads on(...) and $.noun.method(...) from source, so they are
// spelled literally, and helpers that take $ are top-level functions.
import { atom, read, update } from 'claude-code'
import type { CoreEngineInterface, Register } from 'claude-code'
import { EMPTY, SEPARATOR, branchFromHead, fit, gitDir, parent, parts, type Live } from './info'

const launch = atom({ plugin: 'session-info', key: 'launch' } as const, { at: null })
const RUNNING = new Set(['pending', 'running', 'waiting'])
const MAX_DEPTH = 40
const AGENT_SETTLE_MS = 1_000

let live: Live = { ...EMPTY }

// For tests: replace what the band draws.
export function setLive(next: Live): void {
  live = next
}

function changed($: CoreEngineInterface, next: Live): void {
  if (JSON.stringify(next) === JSON.stringify(live)) return
  live = next
  $.ui.invalidate('ui.render')
}

export async function readAgents($: CoreEngineInterface): Promise<number> {
  const agents = await $.agent.list().catch(() => [])
  return agents.filter(agent => RUNNING.has(agent.status)).length
}

// The branch of the repository holding the working directory: the nearest
// `.git` folder, or a worktree's `.git` file pointing at its own HEAD.
export async function readBranch($: CoreEngineInterface): Promise<string | null> {
  let folder: string | null = await $.session.cwd()
  for (let depth = 0; folder && depth < MAX_DEPTH; depth++) {
    const dotGit = `${folder}/.git`
    const found = await $.fs.stat(dotGit).catch(() => null)
    if (found) {
      const head = found.kind === 'dir' ? `${dotGit}/HEAD` : gitHead(await $.fs.read(dotGit), folder)
      if (!head) return null
      return branchFromHead(await $.fs.read(head))
    }
    folder = parent(folder)
  }
  return null
}

function gitHead(dotGitFile: string, folder: string): string | null {
  const target = gitDir(dotGitFile, folder)
  return target ? `${target}/HEAD` : null
}

export async function refresh($: CoreEngineInterface, withBranch: boolean): Promise<void> {
  const [model, prompts, usage, agents, branch] = await Promise.all([
    $.session.model().catch(() => null),
    $.session.turns().catch(() => null),
    $.session.usage().catch(() => null),
    readAgents($),
    withBranch ? readBranch($).catch(() => null) : Promise.resolve(live.branch),
  ])
  changed($, { ...live, model, prompts, costUsd: usage?.cost?.usd ?? null, agents, branch })
}

async function refreshAgents($: CoreEngineInterface): Promise<void> {
  changed($, { ...live, agents: await readAgents($) })
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    const result = await next(e)
    const now = await $.clock.now()
    await update($, launch, current => (current.at === null ? { at: now } : current))
    await refresh($, true).catch(() => {})
    $.clock.every(60_000, async () => {
      await refresh($, true).catch(() => {})
      $.ui.invalidate('ui.render')
    })
    return result
  })

  on('turn.step', async function* ($, e, next) {
    if (!e.agentId) {
      const effort = e.effort === undefined ? null : String(e.effort)
      if (effort !== live.effort) changed($, { ...live, effort })
    }
    return yield* next(e)
  })

  on('prompt.submit', async ($, e, next) => {
    const result = await next(e)
    $.clock.after(0, () => refresh($, true).catch(() => {}))
    return result
  })

  on('turn.complete', async ($, e, next) => {
    const result = await next(e)
    if (e.agentId) $.clock.after(AGENT_SETTLE_MS, () => refreshAgents($).catch(() => {}))
    else $.clock.after(0, () => refresh($, true).catch(() => {}))
    return result
  })

  on('agent.spawn', async ($, e, next) => {
    const result = await next(e)
    $.clock.after(AGENT_SETTLE_MS, () => refreshAgents($).catch(() => {}))
    return result
  })

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    // Other mods draw in this band too: always keep what they drew.
    const others = await next(e)
    if (e.props.hasSurvey) return others
    const { at } = await read($, launch)
    const shown = fit(parts(live, at, await $.clock.now()), (e.props.bodyColumns ?? 80) - 2)
    if (!shown.length) return others
    const { Box, Text } = $.ui.resolve(e)
    const row = (
      <Box flexDirection="row" paddingX={1}>
        {shown.map((part, index) => (
          <Text key={part.key} wrap="truncate-end" color={part.color} bold={part.bold} dimColor={part.dim}>
            {index ? SEPARATOR : ''}{part.text}
          </Text>
        ))}
      </Box>
    )
    return others ? <Box flexDirection="column">{others}{row}</Box> : row
  })
}
