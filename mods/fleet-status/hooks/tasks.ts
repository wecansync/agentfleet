import type { LaneReport, LaneTask, TasksState } from '../types'
import { lanePattern, record, text, timestamp, utcTime } from './status'

export const REPORT_LIMIT_BYTES = 512 * 1024
export function parseReports(source: string | null): LaneReport[] {
  // Also bound processing when a fixture/older host supplies no stat.
  if (!source || source.length > REPORT_LIMIT_BYTES) return []
  const reports: LaneReport[] = []
  for (const line of source.split('\n')) {
    let item: Record<string, unknown> | null
    try { item = record(JSON.parse(line)) } catch { continue }
    const at = timestamp(item?.at), lane = text(item?.lane, lanePattern)
    const session = text(item?.session, /^[A-Za-z0-9._:-]{1,128}$/)
    const agentId = text(item?.agentId, /^[A-Za-z0-9._:-]{1,128}$/)
    const model = text(item?.model, /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/)
    const description = text(item?.description, /^.{0,4096}$/u)
    if (!at || !lane || !session || !agentId || !model || description === null || !Array.isArray(item?.files) || !Array.isArray(item?.forbidden)) continue
    const files = item.files.map(value => text(value, /^.{1,4096}$/u))
    const rules = item.forbidden.map(value => {
      const entry = record(value)
      return text(entry?.command, /^.{0,8192}$/u) !== null ? text(entry?.rule, /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/) : null
    })
    if (files.some(value => value === null) || rules.some(value => value === null)) continue
    const apiError = item.apiError
    if (apiError !== null && !(typeof apiError === 'number' && Number.isInteger(apiError) && apiError >= 400 && apiError <= 599)) continue
    if (!['toolCalls', 'inputTokens', 'outputTokens', 'durationSeconds'].every(key => typeof item![key] === 'number' && Number.isFinite(item![key]) && (item![key] as number) >= 0)) continue
    reports.push({ at, lane, files: files as string[], rules: rules as string[], apiError: apiError as number | null })
    if (reports.length > 20) reports.shift()
  }
  return reports
}

export function formatAgents(agents: unknown[], now: number): LaneTask[] {
  const lanes: LaneTask[] = []
  for (const value of agents) {
    const agent = record(value)
    const type = text(agent?.type, /^fleet-[a-z][a-z0-9-]{0,63}$/)
    const status = text(agent?.status, /^(pending|running|waiting|idle|completed|failed|killed)$/)
    if (!type || !status) continue
    const description = text(agent?.description, /^.{0,4096}$/u) ?? '(description unavailable)'
    // AgentInfo currently has no start timestamp. Accept one only if a future
    // host supplies valid metadata; never invent elapsed time from pane opening.
    const startedAt = timestamp(agent?.startedAt)
    lanes.push({ lane: type.slice(6), status, description: description.length > 100 ? description.slice(0, 99) + '…' : description,
      elapsedSeconds: startedAt && Date.parse(startedAt) <= now ? Math.floor((now - Date.parse(startedAt)) / 1000) : null })
  }
  return lanes
}
export function formatTasks(state: TasksState): { heading: string; lines: string[] }[] {
  return [
    { heading: 'Running lanes', lines: state.lanes.length ? state.lanes.map(item => `${item.lane} · ${item.status}${item.elapsedSeconds === null ? '' : ` · ${item.elapsedSeconds}s`} · ${item.description}`) : ['No fleet lanes in this session.'] },
    { heading: 'Recent lane reports', lines: state.reports.slice(-8).reverse().map(item => `${item.lane} · ${item.files.length} files changed · ${item.rules.length} forbidden${item.rules.length ? ` (${item.rules.join(', ')})` : ''} · API ${item.apiError ?? 'none'}`) },
    { heading: 'Outages', lines: state.outages.length ? state.outages.map(item => `fleet-${item.lane} · ${item.status} · until ${utcTime(item.until)}`) : ['No current outages.'] },
  ].map(section => ({ ...section, lines: section.lines.length ? section.lines : ['No recent lane reports.'] }))
}
