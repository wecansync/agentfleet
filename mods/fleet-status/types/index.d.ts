export type Outage = { lane: string; model: string; status: number; message: string; at: string; until: string }
export type Suggestion = { name: string; catalog: string; pending: number; seen: string | null }
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
  outages: Outage[]
  suggestion: Suggestion | null
}
export type LaneReport = { at: string; lane: string; files: string[]; rules: string[]; apiError: number | null }
export type LaneTask = { lane: string; status: string; description: string; elapsedSeconds: number | null }
export type TasksState = { open: boolean; lanes: LaneTask[]; reports: LaneReport[]; outages: Outage[] }
export type FleetState = {
  snapshot: Snapshot | null
  versionAtStart: string | null
  hiddenNoticeKey: string | null
}
declare module 'claude-code' {
  interface PluginState {
    'fleet-status': { status: FleetState; tasks: TasksState }
  }
}
