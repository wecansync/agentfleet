import type { CoreEngineInterface, McpToolResult, Timer } from 'claude-code'

type UsageStats = {
  session: null | {
    input_tokens: number
    output_tokens: number
    duration_seconds: number
    lane_runs: number
    lane_input_tokens: number
    lane_output_tokens: number
  }
  today: { since: string; sessions: number; input_tokens: number; output_tokens: number; cache_read_tokens: number; cache_write_tokens: number }
  week: { since: string; sessions: number; input_tokens: number; output_tokens: number; cache_read_tokens: number; cache_write_tokens: number }
}

type CallOutcome =
  | { status: 'result'; result: McpToolResult }
  | { status: 'rejected'; error: unknown }
  | { status: 'timeout' }

const SERVER = 'agent-brain-memory'
const TIMEOUT_MS = 5_000
const DISCONNECTED = "agent-brain isn't connected. Turn it on with `agentfleet config agent-brain on`, then restart Claude Code."
const UPDATE_NEEDED = 'Usage needs agent-brain 0.5.1 or later: run `agent-brain update`.'
const STORE_UNAVAILABLE = "agent-brain's local store is unavailable; run `agent-brain status`."

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown> : null
}

function nonnegative(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : 0
}

export function compactNumber(value: unknown): string {
  const number = nonnegative(value)
  if (number < 1_000) return String(Math.round(number))
  const unit = number >= 1_000_000 ? 1_000_000 : 1_000
  const suffix = unit === 1_000_000 ? 'M' : 'k'
  const scaled = number / unit
  const rounded = Number(scaled.toFixed(scaled >= 10 ? 0 : 1))
  if (unit === 1_000 && rounded >= 1_000) return '1M'
  return `${rounded}${suffix}`
}

function count(value: unknown): string {
  return String(Math.floor(nonnegative(value)))
}

function duration(value: unknown): string {
  const seconds = Math.floor(nonnegative(value))
  if (seconds >= 86_400) {
    const days = Math.floor(seconds / 86_400)
    const hours = Math.floor((seconds % 86_400) / 3_600)
    return `${days}d ${hours}h`
  }
  if (seconds >= 3_600) return `${Math.floor(seconds / 3_600)}h ${Math.floor((seconds % 3_600) / 60)}m`
  if (seconds >= 60) return `${Math.floor(seconds / 60)}m`
  return `${seconds}s`
}

function periodLine(label: 'Today' | 'This week', value: unknown, includeSince: boolean): string {
  const period = record(value) ?? {}
  const sessions = count(period.sessions)
  const cache = label === 'Today' ? ` (cache read ${compactNumber(period.cache_read_tokens)})` : ''
  const details = `${sessions} ${sessions === '1' ? 'session' : 'sessions'}, ${compactNumber(period.input_tokens)} in, ${compactNumber(period.output_tokens)} out${cache}`
  const since = typeof period.since === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(period.since) && Number.isFinite(Date.parse(`${period.since}T00:00:00Z`))
    ? ` (since ${period.since})` : ''
  return `${label}:${label === 'Today' ? '        ' : '    '}${details}${includeSince ? since : ''}`
}

export function formatUsage(value: unknown): string[] {
  const stats = record(value)
  if (stats?.available === false) return [STORE_UNAVAILABLE]

  const lines: string[] = []
  const session = record(stats?.session)
  if (stats?.session === null || !session) {
    lines.push('This session: not recorded yet')
  } else {
    const lanes = nonnegative(session.lane_runs)
    const laneSummary = lanes > 0
      ? `; lanes: ${count(lanes)} runs, ${compactNumber(session.lane_input_tokens)} in, ${compactNumber(session.lane_output_tokens)} out`
      : ''
    lines.push(`This session: ${compactNumber(session.input_tokens)} in, ${compactNumber(session.output_tokens)} out over ${duration(session.duration_seconds)}${laneSummary}`)
  }
  lines.push(periodLine('Today', stats?.today, false))
  lines.push(periodLine('This week', stats?.week, true))
  return lines
}

const ENTRY_START = /^(?:\[#([^\]\r\n]+)\]|\[team#([^\]\r\n]+)\])/u
const DISPLAY_UNSAFE = /[\u0000-\u001f\u007f-\u009f\p{Cf}\u2028\u2029]/gu

type MemoryEntry = { marker: string; kind: string; content: string; critical: boolean }

export function splitMemories(source: string): string[] {
  const entries: string[][] = []
  for (const line of source.split(/\r?\n/u)) {
    if (ENTRY_START.test(line)) entries.push([line])
    else if (entries.length) entries[entries.length - 1]!.push(line)
  }
  return entries.map(lines => lines.join('\n'))
}

function parseMemory(entry: string): MemoryEntry | null {
  if (entry.includes('[superseded by')) return null
  const lines = entry.split('\n')
  const first = lines[0] ?? ''
  const markerMatch = ENTRY_START.exec(first)
  if (!markerMatch) return null
  const marker = first.slice(0, markerMatch[0].length).replace(DISPLAY_UNSAFE, '')
  const remainder = first.slice(markerMatch[0].length)
  const metadata = /^\s*\(([^)]*)\)\s*(.*)$/u.exec(remainder)
  if (!metadata) return null
  const header = metadata[1] ?? ''
  const kind = ((header.split(',')[0] ?? '').trim().split(/\s+/u)[0] ?? '').replace(DISPLAY_UNSAFE, '')
  if (!kind) return null
  const content = [metadata[2] ?? '', ...lines.slice(1)].map(line => line.replace(DISPLAY_UNSAFE, '').trim()).find(line => line.length > 0) ?? ''
  if (!content) return null
  return { marker, kind, content, critical: /\bcritical\b/iu.test(header) }
}

function truncateContent(value: string): string {
  return value.length > 110 ? `${value.slice(0, 109).trimEnd()}…` : value
}

export function formatMemories(source: string): string[] {
  const parsed = splitMemories(source).map(parseMemory).filter((entry): entry is MemoryEntry => entry !== null)
  const ordered = [...parsed.filter(entry => entry.critical), ...parsed.filter(entry => !entry.critical)].slice(0, 10)
  return ordered.map(entry => `${entry.critical ? '★ ' : '  '}${entry.marker} ${entry.kind}: ${truncateContent(entry.content)}`)
}

function textBlocks(result: McpToolResult): string[] {
  return result.content.filter(block => block.type === 'text' && typeof block.text === 'string').map(block => block.text!)
}

function failureMessage(outcome: CallOutcome): string {
  if (outcome.status === 'rejected') {
    if (typeof outcome.error === 'string') return outcome.error
    const item = record(outcome.error)
    return typeof item?.message === 'string' ? item.message : String(outcome.error)
  }
  if (outcome.status === 'result') return textBlocks(outcome.result).join('\n')
  return ''
}

function missingTool(outcome: CallOutcome): boolean {
  return /unknown\s+tool\b/iu.test(failureMessage(outcome))
}

function boundedCall($: CoreEngineInterface, tool: 'usage_stats' | 'memory_list', args: Record<string, unknown> | (() => Promise<Record<string, unknown>>)): Promise<CallOutcome> {
  return new Promise(resolve => {
    let settled = false
    let timer: Timer | null = null
    const finish = (outcome: CallOutcome) => {
      if (settled) return
      settled = true
      timer?.cancel()
      resolve(outcome)
    }
    try {
      timer = $.clock.after(TIMEOUT_MS, () => finish({ status: 'timeout' }))
    } catch (error) {
      finish({ status: 'rejected', error })
    }
    const invoke = async () => {
      const callArgs = typeof args === 'function' ? await args() : args
      return $.mcp.call(SERVER, tool, callArgs)
    }
    Promise.resolve().then(invoke).then(
      result => finish({ status: 'result', result }),
      error => finish({ status: 'rejected', error }),
    )
  })
}

export async function runBrain($: CoreEngineInterface): Promise<{ text: string }> {
  const [usage, memories] = await Promise.all([
    boundedCall($, 'usage_stats', async () => ({ session_id: await $.session.id() })),
    boundedCall($, 'memory_list', {}),
  ])

  const memoryCallWorks = memories.status === 'result' && !memories.result.isError
  const memoriesAvailable = memoryCallWorks && !/no memories for this project/iu.test(textBlocks(memories.result)[0] ?? '')
  if (usage.status === 'rejected' && memories.status === 'rejected') return { text: DISCONNECTED }

  const output = ['agent-brain']
  let localStoreUnavailable = false
  // memory_list answering at all (even with a memory-off error) proves the
  // server is connected, so a failing usage_stats means an older agent-brain.
  if (usage.status === 'rejected' && memories.status === 'result') {
    output.push(UPDATE_NEEDED)
  } else if (usage.status === 'timeout') {
    output.push("agent-brain didn't answer in time; try /brain again.")
  } else if (usage.status === 'result' && !usage.result.isError) {
    const json = textBlocks(usage.result)[0]
    if (json !== undefined) {
      try {
        const parsed = JSON.parse(json) as UsageStats
        localStoreUnavailable = record(parsed)?.available === false
        output.push(...formatUsage(parsed))
      } catch { /* Malformed optional usage data has no displayable lines. */ }
    }
  } else if (usage.status === 'result' && usage.result.isError) {
    output.push(missingTool(usage) ? UPDATE_NEEDED : "agent-brain couldn't read usage; run `agent-brain status`.")
  }

  if (memoriesAvailable && !localStoreUnavailable) {
    const rendered = textBlocks(memories.result)[0] ?? ''
    const lines = formatMemories(rendered)
    if (lines.length) output.push('', 'Latest memories', ...lines)
  }
  return { text: output.join('\n') }
}
