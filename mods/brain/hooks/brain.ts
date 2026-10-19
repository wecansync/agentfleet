import type { CoreEngineInterface, McpToolResult, Timer } from 'claude-code'

export type UsageStats = {
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

export type MemoryEntry = { marker: string; kind: string; content: string; critical: boolean }
export type BrainView = { usage: string[]; memories: MemoryEntry[]; total: number; memoryMessage: string | null }

type CallOutcome =
  | { status: 'result'; result: McpToolResult }
  | { status: 'rejected'; error: unknown }
  | { status: 'timeout' }

type ParsedPage = { memories: MemoryEntry[]; total: number | null }

const SERVER = 'agent-brain-memory'
const TIMEOUT_MS = 5_000
const DISCONNECTED = "agent-brain isn't connected. Turn it on with `agentfleet config agent-brain on`, then restart Claude Code."
const UPDATE_NEEDED = 'Usage needs agent-brain 0.5.1 or later: run `agent-brain update`.'
const MEMORY_UPDATE_NEEDED = 'Run `agent-brain update` for the full /brain view.'
const MEMORY_LOAD_FAILED = "Couldn't load memories; run `agent-brain memory list`."
const STORE_UNAVAILABLE = "agent-brain's local store is unavailable; run `agent-brain status`."
const ENTRY_START = /^(?:\[#([^\]\r\n]+)\]|\[team#([^\]\r\n]+)\])/u
const DISPLAY_UNSAFE = /[\u0000-\u0009\u000b-\u001f\u007f-\u009f\p{Cf}\u2028\u2029]/gu
const MEMORY_FOOTER = /\r?\n[ \t]*\r?\nShowing (\d+)-(\d+) of (\d+)\.(?: Next page: offset=(\d+)\.)?$/u

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
  const since = typeof period.since === 'string' && /^\d{4}-\d{2}-\d{2}$/u.test(period.since) && Number.isFinite(Date.parse(`${period.since}T00:00:00Z`))
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

function sanitize(value: string): string {
  return value.replace(DISPLAY_UNSAFE, '')
}

export type ModelPrice = { input: number; output: number; cacheRead?: number; cacheWrite?: number }
export type TokenCounts = { input: number; output: number; cacheRead: number; cacheWrite: number }
export type LaneRow = TokenCounts & { at: number; model: string }
export type CostData = {
  prices: Map<string, ModelPrice>
  main: Map<string, Map<string, TokenCounts>>
  lanes: LaneRow[]
}
export type CostEstimate = {
  total: number
  main: number
  lanes: number
  unpriced: string[]
  cacheNotPriced: boolean
}

const COST_FILE_LIMIT_BYTES = 1024 * 1024
const WEEK_MS = 7 * 86_400_000
const MAX_UNPRICED_NAMES = 4

// Only the installer's known layout may select a Claude directory.
export function claudeDirectory(root: string): string | null {
  const normalized = root.replace(/\\/g, '/').replace(/\/+$/, '')
  const match = /^(.*)\/agentfleet\/mods\/brain$/.exec(normalized)
  const directory = match?.[1]
  return directory && directory !== '/' && !/^[A-Za-z]:$/.test(directory) ? directory : null
}

function jsonValue(source: string | null | undefined): unknown {
  try { return JSON.parse(source ?? 'null') } catch { return null }
}

function price(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : undefined
}

export function parsePrices(source: string | null | undefined): Map<string, ModelPrice> {
  const prices = new Map<string, ModelPrice>()
  const table = record(record(jsonValue(source))?.prices)
  if (!table) return prices
  for (const [model, raw] of Object.entries(table)) {
    const entry = record(raw)
    const input = price(entry?.input)
    const output = price(entry?.output)
    if (!entry || input === undefined || output === undefined) continue
    const parsed: ModelPrice = { input, output }
    const cacheRead = price(entry.cacheRead)
    const cacheWrite = price(entry.cacheWrite)
    if (cacheRead !== undefined) parsed.cacheRead = cacheRead
    if (cacheWrite !== undefined) parsed.cacheWrite = cacheWrite
    prices.set(model, parsed)
  }
  return prices
}

function tokenCounts(entry: Record<string, unknown>): TokenCounts {
  return {
    input: nonnegative(entry.inputTokens),
    output: nonnegative(entry.outputTokens),
    cacheRead: nonnegative(entry.cacheReadTokens),
    cacheWrite: nonnegative(entry.cacheWriteTokens),
  }
}

export function parseMainUsage(source: string | null | undefined): Map<string, Map<string, TokenCounts>> {
  const days = new Map<string, Map<string, TokenCounts>>()
  const table = record(record(jsonValue(source))?.days)
  if (!table) return days
  for (const [day, raw] of Object.entries(table)) {
    const models = record(raw)
    if (!models || !/^\d{4}-\d{2}-\d{2}$/u.test(day)) continue
    const perModel = new Map<string, TokenCounts>()
    for (const [model, usage] of Object.entries(models)) {
      const entry = record(usage)
      if (entry) perModel.set(model, tokenCounts(entry))
    }
    days.set(day, perModel)
  }
  return days
}

export function parseLaneRows(sources: readonly (string | null | undefined)[]): LaneRow[] {
  const rows: LaneRow[] = []
  for (const source of sources) {
    for (const line of (source ?? '').split(/\r?\n/u)) {
      if (!line.trim()) continue
      const row = record(jsonValue(line))
      if (!row || typeof row.model !== 'string' || typeof row.at !== 'string') continue
      const at = Date.parse(row.at)
      if (!Number.isFinite(at)) continue
      rows.push({ at, model: row.model, ...tokenCounts(row) })
    }
  }
  return rows
}

// Gateways record their own spelling of a model (claude-opus-5-5 for
// claude-opus-5.5), so prices match ignoring case, a [..] suffix, and . _ -.
export function priceKey(model: string): string {
  return model.replace(/\[[^\]]*\]$/u, '').toLowerCase().replace(/[._]/gu, '-')
}

function findPrice(prices: ReadonlyMap<string, ModelPrice>, model: string): ModelPrice | null {
  const exact = prices.get(model)
  if (exact) return exact
  const key = priceKey(model)
  for (const [candidate, found] of prices) {
    if (priceKey(candidate) === key) return found
  }
  return null
}

export function costWindowStart(since: unknown, now: number): number {
  if (typeof since === 'string') {
    const parsed = Date.parse(since)
    if (Number.isFinite(parsed)) return parsed
  }
  return now - WEEK_MS
}

export function estimateCost(data: CostData, windowStart: number): CostEstimate | null {
  if (data.prices.size === 0) return null
  const firstDay = new Date(windowStart).toISOString().slice(0, 10)
  const unpriced = new Set<string>()
  let cacheNotPriced = false
  const cost = (model: string, tokens: TokenCounts): number => {
    if (tokens.input + tokens.output + tokens.cacheRead + tokens.cacheWrite === 0) return 0
    const found = findPrice(data.prices, model)
    if (!found) { unpriced.add(model); return 0 }
    if ((tokens.cacheRead > 0 && found.cacheRead === undefined) || (tokens.cacheWrite > 0 && found.cacheWrite === undefined)) {
      cacheNotPriced = true
    }
    return (tokens.input * found.input + tokens.output * found.output +
      tokens.cacheRead * (found.cacheRead ?? 0) + tokens.cacheWrite * (found.cacheWrite ?? 0)) / 1_000_000
  }
  let main = 0
  for (const [day, models] of data.main) {
    if (day < firstDay) continue
    for (const [model, tokens] of models) main += cost(model, tokens)
  }
  let lanes = 0
  for (const row of data.lanes) {
    if (row.at >= windowStart) lanes += cost(row.model, row)
  }
  return { total: main + lanes, main, lanes, unpriced: [...unpriced].sort(), cacheNotPriced }
}

const dollars = (value: number): string => `$${value.toFixed(2)}`

export function formatCostLine(estimate: CostEstimate | null): string | null {
  if (!estimate) return null
  let line = `Est. cost:    ${dollars(estimate.total)} this week (main ${dollars(estimate.main)}, lanes ${dollars(estimate.lanes)})`
  if (estimate.cacheNotPriced) line += ' (cache not priced)'
  if (estimate.unpriced.length > 0) {
    const names = estimate.unpriced.slice(0, MAX_UNPRICED_NAMES).map(name => sanitize(name))
    const more = estimate.unpriced.length - names.length
    line += `; no price for ${names.join(', ')}${more > 0 ? `, +${more} more` : ''}`
  }
  return line
}

// Reads the optional cost inputs. Any problem means "no data", never an error.
export async function loadCostData($: CoreEngineInterface): Promise<CostData | null> {
  try {
    const claudeDir = claudeDirectory($.plugin.root)
    if (claudeDir === null) return null
    const base = claudeDir.replace(/\/$/, '')
    const readSource = async (path: string): Promise<string | null> => {
      const knownPath = `${base}/${path}`
      try {
        const stat = await $.fs.stat(knownPath, { resolve: true })
        if (stat.kind !== 'file' || stat.realPath !== knownPath || stat.size > COST_FILE_LIMIT_BYTES) return null
        return await $.fs.read(knownPath)
      } catch { return null }
    }
    const [config, main, laneCurrent, laneOlder] = await Promise.all([
      readSource('agentfleet/config.json'),
      readSource('agentfleet/main-usage.json'),
      readSource('agentfleet/lane-reports.jsonl'),
      readSource('agentfleet/lane-reports.jsonl.1'),
    ])
    const prices = parsePrices(config)
    if (prices.size === 0) return null
    return { prices, main: parseMainUsage(main), lanes: parseLaneRows([laneCurrent, laneOlder]) }
  } catch { return null }
}

async function costLine(data: CostData | null, since: unknown, $: CoreEngineInterface): Promise<string | null> {
  if (!data) return null
  try {
    let now = Date.now()
    try {
      const clock: unknown = await $.clock.now()
      if (typeof clock === 'number' && Number.isFinite(clock)) now = clock
    } catch { /* The system clock is close enough for a weekly window. */ }
    return formatCostLine(estimateCost(data, costWindowStart(since, now)))
  } catch { return null }
}

export function splitMemories(source: string): string[] {
  const entries: string[][] = []
  for (const line of source.split(/\r?\n/u)) {
    if (ENTRY_START.test(line)) entries.push([line])
    else if (entries.length) entries[entries.length - 1]!.push(line)
  }
  return entries.map(lines => lines.join('\n'))
}

function parseHeader(remainder: string): { header: string; body: string } | null {
  const opening = remainder.search(/\S/u)
  if (opening < 0 || remainder[opening] !== '(') return null
  let depth = 0
  for (let index = opening; index < remainder.length; index++) {
    const character = remainder[index]
    if (character === '(') depth++
    else if (character === ')') {
      depth--
      if (depth === 0) return { header: remainder.slice(opening + 1, index), body: remainder.slice(index + 1) }
    }
  }
  return null
}

function headerItems(header: string): string[] {
  const items: string[] = []
  let depth = 0
  let start = 0
  for (let index = 0; index < header.length; index++) {
    const character = header[index]
    if (character === '(') depth++
    else if (character === ')') depth = Math.max(0, depth - 1)
    else if (depth === 0 && (character === ',' || character === ';')) {
      items.push(header.slice(start, index).trim())
      start = index + 1
    }
  }
  items.push(header.slice(start).trim())
  return items
}

function parseMemory(entry: string): MemoryEntry | null {
  const lines = entry.split('\n')
  const first = lines[0] ?? ''
  const markerMatch = ENTRY_START.exec(first)
  if (!markerMatch) return null
  const marker = sanitize(markerMatch[0])
  const parsedHeader = parseHeader(first.slice(markerMatch[0].length))
  if (!parsedHeader) return null
  const header = parsedHeader.header
  const body = [parsedHeader.body, ...lines.slice(1)].join('\n').trim()
  if (/\[superseded by\b/iu.test(body)) return null

  const kind = sanitize((header.trim().split(/[\s,;]/u)[0] ?? ''))
  if (!kind) return null
  let content = body
  if (content.startsWith('"')) {
    try {
      const decoded: unknown = JSON.parse(content)
      if (typeof decoded !== 'string') return null
      content = decoded
    } catch { return null }
  }
  content = sanitize(content).trim()
  if (!content) return null
  const critical = headerItems(header).some(item => item.toLowerCase() === 'critical')
  return { marker, kind, content, critical }
}

function stripFooter(source: string): { text: string; total: number | null } {
  const trimmed = source.replace(/[ \t\r\n]+$/u, '')
  const footer = MEMORY_FOOTER.exec(trimmed)
  if (footer) {
    const total = Number(footer[3])
    return { text: trimmed.slice(0, footer.index), total: Number.isSafeInteger(total) ? total : null }
  }
  const footerOnly = /^Showing (\d+)-(\d+) of (\d+)\.(?: Next page: offset=(\d+)\.)?$/u.exec(trimmed)
  if (!footerOnly) return { text: trimmed, total: null }
  const total = Number(footerOnly[3])
  return { text: '', total: Number.isSafeInteger(total) ? total : null }
}

function parsePage(source: string): ParsedPage {
  const stripped = stripFooter(source)
  return {
    memories: splitMemories(stripped.text).map(parseMemory).filter((entry): entry is MemoryEntry => entry !== null),
    total: stripped.total,
  }
}

export function parseMemoryPage(source: string): { memories: MemoryEntry[]; total: number | null } {
  return parsePage(source)
}

export function mergeMemories(critical: readonly MemoryEntry[], newest: readonly MemoryEntry[]): MemoryEntry[] {
  const seen = new Set<string>()
  const merged: MemoryEntry[] = []
  for (const entry of [...critical, ...newest]) {
    if (seen.has(entry.marker)) continue
    seen.add(entry.marker)
    merged.push(entry)
  }
  return merged
}

function truncate(value: string, maximum: number): string {
  const points = Array.from(value)
  if (points.length <= maximum) return value
  if (maximum <= 1) return '…'.slice(0, maximum)
  return `${points.slice(0, maximum - 1).join('').trimEnd()}…`
}

export function memoryLine(entry: MemoryEntry, width = 110): string {
  const prefix = `${entry.critical ? '★ ' : '  '}${entry.marker} ${entry.kind}: `
  const firstLine = entry.content.split('\n')[0] ?? ''
  return `${prefix}${truncate(firstLine, Math.max(0, width - Array.from(prefix).length))}`
}

function textBlocks(result: McpToolResult): string[] {
  return result.content.filter(block => block.type === 'text' && typeof block.text === 'string').map(block => block.text!)
}

function failureMessage(outcome: CallOutcome): string {
  if (outcome.status === 'rejected') {
    if (typeof outcome.error === 'string') return outcome.error
    const item = record(outcome.error)
    if (typeof item?.message === 'string') return item.message
    if (typeof item?.deny === 'string') return item.deny
    return String(outcome.error)
  }
  if (outcome.status === 'result') return textBlocks(outcome.result).join('\n')
  return ''
}

function isArgumentCompatibilityFailure(outcome: CallOutcome): boolean {
  const message = failureMessage(outcome)
  return /invalid\s+params|unknown\s+field|unexpected\s+(?:or\s+)?additional\s+properties?/iu.test(message) ||
    /(?:unexpected|additional)\s+properties?/iu.test(message)
}

function isSilentMemoryGate(outcome: CallOutcome): boolean {
  return /memory is (?:not enabled|not active|paused\b|a Pro feature|disabled for this project|unavailable for this team project)/iu.test(failureMessage(outcome))
}

function noMemoriesForProject(outcome: CallOutcome): boolean {
  return /no memories for this project/iu.test(failureMessage(outcome))
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

function loadMemories(outcomes: readonly CallOutcome[]): Pick<BrainView, 'memories' | 'total' | 'memoryMessage'> {
  if (outcomes.some(isArgumentCompatibilityFailure)) {
    return { memories: [], total: 0, memoryMessage: MEMORY_UPDATE_NEEDED }
  }
  if (outcomes.some(isSilentMemoryGate)) {
    return { memories: [], total: 0, memoryMessage: null }
  }

  const pages: ParsedPage[] = []
  const hasNoMemoriesResponse = outcomes.some(noMemoriesForProject)
  for (const outcome of outcomes) {
    if (noMemoriesForProject(outcome)) continue
    if (outcome.status !== 'result' || outcome.result.isError) {
      return { memories: [], total: 0, memoryMessage: MEMORY_LOAD_FAILED }
    }
    pages.push(parsePage(textBlocks(outcome.result).join('\n')))
  }

  const critical = pages[0] ?? { memories: [], total: null }
  const newest = pages[1] ?? { memories: [], total: null }
  const memories = mergeMemories(critical.memories, newest.memories)
  if (memories.length === 0) {
    return hasNoMemoriesResponse
      ? { memories: [], total: 0, memoryMessage: null }
      : { memories: [], total: 0, memoryMessage: MEMORY_LOAD_FAILED }
  }
  return {
    memories,
    total: newest.total ?? critical.total ?? memories.length,
    memoryMessage: null,
  }
}

export async function loadBrain($: CoreEngineInterface): Promise<BrainView> {
  const [usage, criticalMemories, newestMemories, costData] = await Promise.all([
    boundedCall($, 'usage_stats', async () => ({ session_id: await $.session.id() })),
    boundedCall($, 'memory_list', { priority: 'critical', limit: 10 }),
    boundedCall($, 'memory_list', { limit: 30 }),
    loadCostData($),
  ])
  let since: unknown = null

  let usageLines: string[] = []
  if (usage.status === 'rejected') {
    const rejectedCalls = [criticalMemories, newestMemories]
    usageLines.push(rejectedCalls.every(outcome => outcome.status === 'rejected')
      ? DISCONNECTED : UPDATE_NEEDED)
  } else if (usage.status === 'timeout') {
    usageLines.push("agent-brain didn't answer in time; try /brain again.")
  } else if (!usage.result.isError) {
    const json = textBlocks(usage.result)[0]
    if (json !== undefined) {
      try {
        const parsed: unknown = JSON.parse(json)
        usageLines = formatUsage(parsed)
        since = record(record(parsed)?.week)?.since
      }
      catch { /* Malformed optional usage data has no displayable lines. */ }
    }
  } else {
    usageLines.push(/unknown\s+tool\b/iu.test(failureMessage(usage))
      ? UPDATE_NEEDED : "agent-brain couldn't read usage; run `agent-brain status`.")
  }
  if (usageLines.length === 0) usageLines.push("agent-brain couldn't read usage; run `agent-brain status`.")
  const cost = await costLine(costData, since, $)
  if (cost) usageLines.push(cost)

  return { usage: usageLines, ...loadMemories([criticalMemories, newestMemories]) }
}

export function fallbackText(view: BrainView, limit = 10): string {
  const lines = ['agent-brain', ...view.usage]
  if (view.memoryMessage) lines.push(view.memoryMessage)
  if (view.memories.length) {
    lines.push('', `Memories (${view.memories.length} of ${view.total})`)
    lines.push(...view.memories.slice(0, limit).map(entry => memoryLine(entry)))
  }
  return lines.join('\n')
}
