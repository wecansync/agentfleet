// Same-lane model failover. When a fleet lane's own model has a current
// outage (recorded by AgentFleet's prompt hook in agentfleet/outages.json),
// a new run of that lane starts on the first of its fallback models in
// fleet.json that has none. scripts/fleet_watch.py applies the same rule
// (effective_model) to word its notices, and reads the description mark
// below to know which model a switched run used.

export const SOURCE_LIMIT_BYTES = 1024 * 1024

// Ids as gateways and Claude Code spell them (`codex-luna[1m]`, `claude-haiku-4-5`).
const MODEL_ID = /^[A-Za-z0-9][A-Za-z0-9._:/@+\[\]-]{0,99}$/
const LANE = /^fleet-([a-z0-9][a-z0-9-]{0,63})$/
const MARK = /\s*\(failover: [^()\s]{1,100}\)\s*$/

// Only the installer's known layout may select a Claude directory.
export function claudeDirectory(root: string): string | null {
  const normalized = root.replace(/\\/g, '/').replace(/\/+$/, '')
  const match = /^(.*)\/agentfleet\/mods\/lane-guard$/.exec(normalized)
  const directory = match?.[1]
  return directory && directory !== '/' && !/^[A-Za-z]:$/.test(directory) ? directory : null
}

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown> : null
}

function parse(source: string | null): Record<string, unknown> | null {
  if (!source) return null
  try {
    return record(JSON.parse(source))
  } catch {
    return null
  }
}

function modelId(value: unknown): string | null {
  const row = record(value)
  const id = row ? (row.model ?? row.primary ?? row.id) : value
  return typeof id === 'string' && MODEL_ID.test(id.trim()) ? id.trim() : null
}

export function laneName(subagentType: string): string | null {
  return LANE.exec(subagentType)?.[1] ?? null
}

// A lane's row: the project's pin (`agentfleet project pin` writes
// <project>/.claude/fleet.local.json) over the global fleet map.
// A project pin is laid over the global row, as the agent generator merges
// them, so a pin of only `model` keeps the lane's global fallbacks.
function laneRow(fleetSource: string | null, localSource: string | null, lane: string): Record<string, unknown> | null {
  const lanes = record(parse(fleetSource)?.lanes)
  const global = lanes ? record(lanes[lane]) : null
  const local = parse(localSource)
  const pinned = local?.version === 'delegate-fleet.local.v1' ? record(record(local.lanes)?.[lane]) : null
  if (pinned) return { ...(global ?? {}), ...pinned }
  return global
}

// A lane's own model, then its fallbacks, in order and without repeats.
export function laneModels(fleetSource: string | null, lane: string, localSource: string | null = null): string[] {
  const row = laneRow(fleetSource, localSource, lane)
  if (!row) return []
  const fallbacks = Array.isArray(row.fallbacks) ? row.fallbacks : []
  const models: string[] = []
  for (const value of [row.model, ...fallbacks]) {
    const id = modelId(value)
    if (id && !models.includes(id)) models.push(id)
  }
  return models
}

// Models with an outage whose retry deadline has not passed.
export function downModels(outagesSource: string | null, now: number): Set<string> {
  const data = parse(outagesSource)
  const down = new Set<string>()
  if (data?.format !== 'agentfleet.outages.v1') return down
  const models = record(data.models)
  if (!models) return down
  for (const [id, value] of Object.entries(models)) {
    const row = record(value)
    const until = typeof row?.until === 'string' ? Date.parse(row.until) : NaN
    const status = typeof row?.status === 'number' ? row.status : 0
    if (MODEL_ID.test(id) && status > 0 && Number.isFinite(until) && until > now) down.add(id)
  }
  return down
}

// The model to start a lane on instead of its own, or null to leave it be:
// its own model is fine, the lane is unknown, or every model of it is down
// (then the prompt hook names another lane).
export function failoverModel(fleetSource: string | null, outagesSource: string | null, lane: string, now: number, localSource: string | null = null): string | null {
  const models = laneModels(fleetSource, lane, localSource)
  if (models.length < 2) return null
  const down = downModels(outagesSource, now)
  if (!down.has(models[0])) return null
  return models.slice(1).find(model => !down.has(model)) ?? null
}

export function markDescription(description: string, model: string): string {
  return `${description.replace(MARK, '')} (failover: ${model})`
}
