import type { LaneActivity } from '../types'
import { record, text } from './status'

// A write lane that keeps calling tools without changing a file may be stuck in
// a loop. Counted in memory from tool calls; no model call and no file.
export const STALL_CALLS = 40
export const EDIT_TOOLS = new Set(['Edit', 'Write', 'MultiEdit', 'NotebookEdit'])
// Lanes whose job is to change files; read-only lanes call tools without edits by design.
const WRITE_LANE = /^fleet-(implement(-[a-z0-9-]+)?|tests|ui|docs)$/

export function countCall(activity: Record<string, LaneActivity>, agentId: string, tool: string): Record<string, LaneActivity> {
  const prior = activity[agentId] ?? { calls: 0, sinceEdit: 0 }
  const edit = EDIT_TOOLS.has(tool)
  return { ...activity, [agentId]: { calls: prior.calls + 1, sinceEdit: edit ? 0 : prior.sinceEdit + 1 } }
}

export function stallNotices(agents: unknown[], activity: Record<string, LaneActivity>): string[] {
  const lines: string[] = []
  for (const value of agents) {
    const agent = record(value)
    const id = text(agent?.id, /^[A-Za-z0-9._:@-]{1,128}$/)
    const type = text(agent?.type, WRITE_LANE)
    if (!id || !type || agent?.status !== 'running') continue
    const seen = activity[id]
    if (!seen || seen.sinceEdit < STALL_CALLS) continue
    const since = seen.calls === seen.sinceEdit ? 'without changing a file' : 'since its last file change'
    lines.push(`${type} has made ${seen.sinceEdit} tool calls ${since}: check it in /fleet-tasks, or stop it if it is looping.`)
  }
  return lines
}
