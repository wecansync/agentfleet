import type { Failover, JevStats, Outage, Snapshot, Suggestion } from '../types'

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
// Models recorded as down; lane-guard starts their lanes on `switchedTo`.
export function parseFailovers(source: string | null | undefined, now: number): Failover[] {
  const data = jsonRecord(source)
  if (data?.format !== 'agentfleet.outages.v1') return []
  const result: Failover[] = []
  for (const [key, value] of Object.entries(record(data.models) ?? {})) {
    const item = record(value)
    const model = text(key, modelPattern)
    const lane = text(item?.lane, lanePattern)
    const switchedTo = text(item?.switchedTo, modelPattern)
    const until = timestamp(item?.until)
    const status = item?.status
    if (model && lane && until && typeof status === 'number' && Number.isInteger(status) && status >= 400 && status <= 599 && Date.parse(until) > now) {
      result.push({ model, lane, switchedTo, status, until })
    }
  }
  return result.sort((a, b) => a.lane.localeCompare(b.lane) || a.model.localeCompare(b.model))
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
    ? { name, catalog, seen, pending, new: parseNew(item?.new) } : null
}

export function parseNew(value: unknown): Suggestion['new'] {
  if (!Array.isArray(value) || value.length > 3) return []
  const result: Suggestion['new'] = []
  for (const raw of value) {
    const item = record(raw)
    const id = text(item?.id, /^[a-z0-9][a-z0-9-]{0,127}$/)
    const title = text(item?.title, /^.{1,160}$/u)
    const kind = text(item?.kind, /^(plugin|mod|hook|create-skill|claude-md)$/)
    const why = text(item?.why, /^.{1,500}$/u)
    if (id && title && kind && why && !result.some(item => item.id === id)) result.push({ id, title, kind, why })
  }
  return result
}

export function parseJev(source: string | null | undefined): JevStats | null {
  const data = jsonRecord(source)
  const totals = record(data?.totals)
  const count = (n: unknown): n is number => typeof n === 'number' && Number.isSafeInteger(n) && n >= 0
  if (data?.format !== 'agentfleet.jev-stats.v1' || !timestamp(data.updated) || !totals ||
      !['compactions', 'jev', 'fallbacks', 'tokensSaved'].every(k => count(totals[k])) || !Array.isArray(data.recent)) return null
  let latest: Record<string, unknown> | null = null
  for (const raw of data.recent) {
    const item = record(raw)
    if (!item || !timestamp(item.at) || (typeof item.outcome !== 'string' || !['jev', 'fallback', 'no-key'].includes(item.outcome)) ||
        (typeof item.trigger !== 'string' || !['auto', 'manual', 'plugin'].includes(item.trigger)) ||
        !['tokensBefore', 'tokensAfter', 'messagesBefore', 'messagesKept'].every(k => item[k] === null || count(item[k])) ||
        !count(item.ms) ||
        !(item.reason === null || text(item.reason, /^.{0,1000}$/u) !== null)) return null
    if (!latest || String(item.at) > String(latest.at)) latest = item
  }
  return { compactions: totals.compactions as number, jev: totals.jev as number, fallbacks: totals.fallbacks as number,
    at: latest ? latest.at as string : null, before: latest ? latest.tokensBefore as number | null : null,
    after: latest ? latest.tokensAfter as number | null : null }
}

const optionKeys23 = ['compact', 'jev', 'reviewerAccess', 'learn', 'statusBand', 'laneGuard', 'autoUpdate']
// The instruction check is an action offered by the wizard, not a new option.
const optionKeys24 = [...optionKeys23, 'workStyle', 'agentBrain']
const optionKeys25 = [...optionKeys24, 'askFirst', 'tokenWeather']

export function parseNewOptions(source: string | null | undefined, version: string | null): number {
  const minor = releaseMinor(version)
  const optionKeys = minor >= 5 ? optionKeys25 : minor === 4 ? optionKeys24 : optionKeys23
  const data = jsonRecord(source)
  if (data?.format !== 'agentfleet.wizard.v1' || !Array.isArray(data.asked) ||
      !data.asked.every(key => typeof key === 'string')) return optionKeys.length
  // Unknown string keys belong to newer bundles and do not count as reviewed options here.
  const asked = data.asked
  return optionKeys.filter(key => !asked.includes(key)).length
}

// The 2.x minor of a final release (a later major counts as the newest list);
// prereleases and unknown versions count as 2.3, the first wizard release.
function releaseMinor(version: string | null): number {
  if (!version || !versionPattern.test(version) || /-/.test(version)) return 3
  const [major, minor] = version.split(/[.+-]/).slice(0, 2).map(Number)
  return major > 2 ? Number.MAX_SAFE_INTEGER : major === 2 ? minor : 3
}

export function hasWizardOptions(version: string | null): boolean {
  if (!version || !versionPattern.test(version)) return false
  const [major, minor, patch] = version.split(/[.+-]/).slice(0, 3).map(Number)
  return major > 2 || (major === 2 && (minor > 3 || (minor === 3 &&
    (patch > 0 || (patch === 0 && !version.split('+')[0].includes('-'))))))
}

export function snapshotFromSources(claudeDir: string | null, sources: (string | null)[], project: string | null = null, now = Date.now()): Snapshot {
  function json(source: string | null | undefined): Record<string, unknown> | null {
    try { return record(JSON.parse(source ?? 'null')) } catch { return null }
  }
  const manifest = json(sources[0])
  const manifestVersion = text(manifest?.version, versionPattern)
  const profile = sources[1]
  const auto = json(sources[2])
  const proposal = json(sources[3])
  const result = record(auto?.lastResult)
  const configSource = json(sources[4])
  const config = configSource?.format === 'agentfleet.config.v1' ? configSource : null
  const percent = config?.compactPercent
  return {
    compactPercent: percent === 'off' ? 'off' : typeof percent === 'number' && Number.isInteger(percent) && percent >= 20 && percent <= 95 ? percent : 70,
    reviewerAccess: config?.reviewerAccess === 'run' || config?.reviewerAccess === 'read-only' ? config.reviewerAccess : 'full',
    learn: typeof config?.learn === 'boolean' ? config.learn : true,
    workStyle: config?.workStyle === 'direct' || config?.workStyle === 'fleet' ? config.workStyle : 'balanced',
    outages: parseOutages(sources[5], now),
    failovers: parseFailovers(sources[5], now),
    suggestion: parseSuggestion(sources[6], project),
    jev: parseJev(sources[7]),
    newOptions: parseNewOptions(sources[8], manifestVersion),
    claudeDir,
    version: manifestVersion,
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

export function notices(snapshot: Snapshot, versionAtStart: string | null, hiddenIds: string[] = []): string[] {
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
  for (const item of snapshot.failovers) {
    if (item.switchedTo) lines.push(`fleet-${item.lane} runs on ${item.switchedTo} until ${utcTime(item.until)}: ${item.model} failed with ${item.status}.`)
  }
  for (const outage of snapshot.outages) {
    const alt = outage.alternative
    lines.push(`fleet-${outage.lane} is failing (${outage.status} at ${utcTime(outage.at)}; ${outage.model}). ${alt ? `Dispatch fleet-${alt} until ${utcTime(outage.until)}.` : `Retry it after ${utcTime(outage.until)}.`}`)
  }
  const suggestion = snapshot.suggestion
  const first = suggestion?.new.find(item => !hiddenIds.includes(item.id))
  if (suggestion && suggestion.pending > 0 && first) {
    lines.push(`AgentFleet suggests ${first.title} (${first.kind}): ${first.why}. Run /fleet-suggest to install or dismiss it.${suggestion.pending > 1 ? ` (+${suggestion.pending - 1} more)` : ''}`)
  }
  if (hasWizardOptions(snapshot.version) && snapshot.newOptions > 0) {
    lines.push('New AgentFleet options are available: run agentfleet configure in a terminal.')
  }
  return lines
}

export function summary(snapshot: Snapshot, versionAtStart: string | null): string {
  const result = snapshot.lastResult
  const action = notices(snapshot, versionAtStart)
  return [
    `AgentFleet version: ${snapshot.version ?? 'unknown'}`,
    `Profile: ${snapshot.profile ?? 'not recorded'}`,
    ...(hasWizardOptions(snapshot.version) ? [snapshot.newOptions === 0 ? 'Options: all reviewed' : `Options: ${snapshot.newOptions} new (run agentfleet configure)`] : []),
    `Auto-update: ${snapshot.enabled === null ? 'enabled (default)' : snapshot.enabled ? 'enabled' : 'disabled'}`,
    `Auto-compact: ${snapshot.compactPercent === 'off' ? 'off (AgentFleet leaves the window alone)' : `${snapshot.compactPercent}% of each model's context`}`,
    `Reviewer access: ${snapshot.reviewerAccess}`,
    `Work style: ${snapshot.workStyle}`,
    snapshot.jev ? `JEV: ${snapshot.jev.compactions} compactions (${snapshot.jev.jev} verbatim, ${snapshot.jev.fallbacks} fallbacks)${snapshot.jev.at ? `, last at ${utcTime(snapshot.jev.at)}` : ''}${snapshot.jev.before !== null && snapshot.jev.after !== null ? `; ${snapshot.jev.before} → ${snapshot.jev.after} tokens` : ''}` : 'JEV: no compactions recorded',
    `Suggestions learning: ${snapshot.learn ? 'on' : 'off'}`,
    `Lane outages: ${snapshot.outages.length ? snapshot.outages.map(item => `fleet-${item.lane} ${item.status} until ${utcTime(item.until)}`).join('; ') : 'none'}`,
    `Models down: ${snapshot.failovers.length ? snapshot.failovers.map(item => `${item.model} ${item.status} until ${utcTime(item.until)}${item.switchedTo ? ` (fleet-${item.lane} uses ${item.switchedTo})` : ''}`).join('; ') : 'none'}`,
    ...(snapshot.suggestion ? [`Suggestions for ${snapshot.suggestion.name}: ${snapshot.suggestion.pending} (run /fleet-suggest)`] : []),
    `Last check: ${snapshot.lastCheck ?? 'unknown'}; latest seen: ${snapshot.latestSeen ?? 'unknown'}`,
    `Last result: ${result ? `${result.version ?? 'unknown'} (${result.ok ? 'succeeded' : 'failed'})` : 'none'}`,
    `Model proposals: ${snapshot.decision ?? 'unknown'}; missing lanes: ${snapshot.missingLanes}`,
    ...(action.length ? action : ['No action needed.']),
  ].join('\n')
}
