export type Outage = { lane: string; model: string; alternative: string | null; status: number; message: string; at: string; until: string }
export type Failover = { model: string; lane: string; switchedTo: string | null; status: number; until: string }
export type SuggestionItem = { id: string; title: string; kind: string; why: string }
export type Suggestion = { name: string; catalog: string; pending: number; seen: string | null; new: SuggestionItem[] }
export type JevStats = { compactions: number; jev: number; fallbacks: number; at: string | null; before: number | null; after: number | null }
export type Snapshot = {
  claudeDir: string | null
  version: string | null
  profile: string | null
  enabled: boolean | null
  lastCheck: string | null
  latestSeen: string | null
  lastResult: { version: string | null; ok: boolean } | null
  reported: boolean | null
  decision: 'approved' | 'pending' | null
  missingLanes: number
  compactPercent: number | 'off'
  reviewerAccess: 'full' | 'run' | 'read-only'
  learn: boolean
  workStyle: 'direct' | 'balanced' | 'fleet'
  outages: Outage[]
  failovers: Failover[]
  suggestion: Suggestion | null
  jev: JevStats | null
  newOptions: number
  usageNotices: string[]
}
export type LaneReport = { at: string; lane: string; model: string; files: string[]; rules: string[]; apiError: number | null }
export type LaneTask = { lane: string; status: string; description: string; elapsedSeconds: number | null }
export type TasksState = { open: boolean; lanes: LaneTask[]; reports: LaneReport[]; outages: Outage[] }
export type FleetState = {
  snapshot: Snapshot | null
  versionAtStart: string | null
  hiddenNoticeKey: string | null
  hiddenSuggestionIds: string[]
}
declare module 'claude-code' {
  interface PluginState {
    'fleet-status': { status: FleetState; tasks: TasksState }
  }
}
