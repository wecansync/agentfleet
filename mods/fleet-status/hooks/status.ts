import type { Outage, Snapshot, Suggestion } from '../types'

// Only the installer's known layout may select a Claude directory.
export function claudeDirectory(root: string): string | null {
  const normalized = root.replace(/\\/g, '/').replace(/\/+$/, '')
  const match = /^(.*)\/agentfleet\/mods\/fleet-status$/.exec(normalized)
  const directory = match?.[1]
  return directory && directory !== '/' && !/^[A-Za-z]:$/.test(directory) ? directory : null
}

export function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown> : null
}
export function text(value: unknown, pattern: RegExp): string | null {
  if (typeof value !== 'string') return null
  // Check before trimming: leading/trailing controls are hostile too.
  if (/[\u0000-\u001f\u007f-\u009f\p{Cf}\u2028\u2029]/u.test(value)) return null
  const trimmed = value.trim()
  const safe = trimmed.replace(/[\u0000-\u001f\u007f-\u009f\p{Cf}\u2028\u2029]/gu, '')
  return pattern.test(safe) ? safe : null
}
const versionPattern = /^\d+\.\d+\.\d+([-+][0-9A-Za-z.-]{1,32})?$/

export const lanePattern = /^[a-z][a-z0-9-]{0,63}$/
export const modelPattern = /^[A-Za-z0-9][A-Za-z0-9._:/@+\[\]-]{0,127}$/
export function jsonRecord(source: string | null | undefined): Record<string, unknown> | null {
  try { return record(JSON.parse(source ?? 'null')) } catch { return null }
}
export function timestamp(value: unknown): string | null {
  const safe = text(value, /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,3})?Z$/)
  return safe && Number.isFinite(Date.parse(safe)) ? safe : null
}
export function utcTime(value: string): string { return value.slice(11, 16) + ' UTC' }
export function parseOutages(source: string | null | undefined, now: number): Outage[] {
  const data = jsonRecord(source)
  if (data?.format !== 'agentfleet.outages.v1') return []
  const result: Outage[] = []
  for (const [key, value] of Object.entries(record(data.lanes) ?? {})) {
    const item = record(value)
    const lane = text(key, lanePattern)
    const model = text(item?.model, modelPattern) ?? 'unknown model'
    const alternative = text(item?.alternative, lanePattern)
    const message = text(item?.message, /^.{0,1000}$/u)
    const at = timestamp(item?.at), until = timestamp(item?.until)
    const status = item?.status
    if (lane && message !== null && at && until && typeof status === 'number' && Number.isInteger(status) && status >= 400 && status <= 599 && Date.parse(until) > now) {
      result.push({ lane, model, alternative, message, status, at, until })
    }
  }
  return result.sort((a, b) => a.lane.localeCompare(b.lane))
}
export function parseSuggestion(source: string | null | undefined, project: string | null): Suggestion | null {
  const data = jsonRecord(source)
  if (data?.format !== 'agentfleet.suggestions.v1' || !text(project, /^(?:\/|[A-Za-z]:[\\/]).{0,4095}$/u)) return null
  const projects = record(data.projects)
  const item = record(projects && Object.prototype.hasOwnProperty.call(projects, project!) ? projects[project!] : null)
  const name = text(item?.name, /^[^/\\]{1,128}$/u)
  const catalog = text(item?.catalog, versionPattern)
  const seen = item?.seen === null ? null : text(item?.seen, versionPattern)
  const pending = item?.pending
  return name && catalog && (item?.seen === null || seen !== null) && typeof pending === 'number' && Number.isSafeInteger(pending) && pending >= 0
    ? { name, catalog, seen, pending } : null
}

export function snapshotFromSources(claudeDir: string | null, sources: (string | null)[], project: string | null = null, now = Date.now()): Snapshot {
  function json(source: string | null | undefined): Record<string, unknown> | null {
    try { return record(JSON.parse(source ?? 'null')) } catch { return null }
  }
  const manifest = json(sources[0])
  const profile = sources[1]
  const auto = json(sources[2])
  const proposal = json(sources[3])
  const result = record(auto?.lastResult)
  const configSource = json(sources[4])
  const config = configSource?.format === 'agentfleet.config.v1' ? configSource : null
  const percent = config?.compactPercent
  return {
    compactPercent: percent === 'off' ? 'off' : typeof percent === 'number' && Number.isInteger(percent) && percent >= 40 && percent <= 95 ? percent : 70,
    reviewerAccess: config?.reviewerAccess === 'run' || config?.reviewerAccess === 'read-only' ? config.reviewerAccess : 'full',
    learn: typeof config?.learn === 'boolean' ? config.learn : false,
    outages: parseOutages(sources[5], now),
    suggestion: parseSuggestion(sources[6], project),
    claudeDir,
    version: text(manifest?.version, versionPattern),
    profile: text(profile, /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/),
    enabled: typeof auto?.enabled === 'boolean' ? auto.enabled : null,
    lastCheck: text(auto?.lastCheck, /^[0-9T:.Z+-]{1,40}$/),
    latestSeen: text(auto?.latestSeen, versionPattern),
    lastResult: typeof result?.ok === 'boolean'
      ? { ok: result.ok, version: text(result.version, versionPattern) } : null,
    reported: typeof auto?.reported === 'boolean' ? auto.reported : null,
    decision: proposal?.decision === 'approved' || proposal?.decision === 'pending' ? proposal.decision : null,
    missingLanes: Object.keys(record(proposal?.missing_lane_models) ?? {}).length,
  }
}

export function notices(snapshot: Snapshot, versionAtStart: string | null): string[] {
  const lines: string[] = []
  const result = snapshot.lastResult
  if (snapshot.version !== null && versionAtStart !== null && snapshot.version !== versionAtStart) {
    lines.push(`AgentFleet ${snapshot.version} is installed. Restart Claude Code to use it.`)
  }
  if (result?.ok === false && !snapshot.reported) {
    lines.push(`AgentFleet update to ${result.version ?? 'unknown'} failed. See ${(snapshot.claudeDir ?? '').replace(/[\u0000-\u001f\u007f-\u009f\p{Cf}\u2028\u2029]/gu, '').replace(/\/$/, '')}/agentfleet/auto-update.log`)
  }
  if (snapshot.missingLanes > 0) {
    lines.push(`${snapshot.missingLanes} fleet ${snapshot.missingLanes === 1 ? 'lane has' : 'lanes have'} no available model. Run /fleet-setup.`)
  }
  if (snapshot.decision === 'pending') {
    lines.push('Model changes are waiting for review. Run /fleet-setup.')
  }
  for (const outage of snapshot.outages) {
    const alt = outage.alternative
    lines.push(`fleet-${outage.lane} is failing (${outage.status} at ${utcTime(outage.at)}; ${outage.model}). ${alt ? `Dispatch fleet-${alt} until ${utcTime(outage.until)}.` : `Retry it after ${utcTime(outage.until)}.`}`)
  }
  const suggestion = snapshot.suggestion
  if (suggestion && suggestion.pending > 0 && suggestion.seen !== suggestion.catalog) {
    lines.push(`AgentFleet has ${suggestion.pending} suggestions for ${suggestion.name}: run /fleet-suggest`)
  }
  return lines
}

export function summary(snapshot: Snapshot, versionAtStart: string | null): string {
  const result = snapshot.lastResult
  const action = notices(snapshot, versionAtStart)
  return [
    `AgentFleet version: ${snapshot.version ?? 'unknown'}`,
    `Profile: ${snapshot.profile ?? 'not recorded'}`,
    `Auto-update: ${snapshot.enabled === null ? 'enabled (default)' : snapshot.enabled ? 'enabled' : 'disabled'}`,
    `Auto-compact: ${snapshot.compactPercent === 'off' ? 'off (AgentFleet leaves the window alone)' : `${snapshot.compactPercent}% of the model's context`}`,
    `Reviewer access: ${snapshot.reviewerAccess}`,
    `Suggestions learning: ${snapshot.learn ? 'on' : 'off'}`,
    `Lane outages: ${snapshot.outages.length ? snapshot.outages.map(item => `fleet-${item.lane} ${item.status} until ${utcTime(item.until)}`).join('; ') : 'none'}`,
    ...(snapshot.suggestion ? [`Suggestions for ${snapshot.suggestion.name}: ${snapshot.suggestion.pending} (run /fleet-suggest)`] : []),
    `Last check: ${snapshot.lastCheck ?? 'unknown'}; latest seen: ${snapshot.latestSeen ?? 'unknown'}`,
    `Last result: ${result ? `${result.version ?? 'unknown'} (${result.ok ? 'succeeded' : 'failed'})` : 'none'}`,
    `Model proposals: ${snapshot.decision ?? 'unknown'}; missing lanes: ${snapshot.missingLanes}`,
    ...(action.length ? action : ['No action needed.']),
  ].join('\n')
}
