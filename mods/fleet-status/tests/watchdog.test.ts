import { test, expect } from 'claude-code/testing'
import type { LaneActivity } from '../types'
import { countCall, STALL_CALLS, stallNotices } from '../hooks/watchdog'

function calls(tools: string[], start: Record<string, LaneActivity> = {}, id = 'a1'): Record<string, LaneActivity> {
  return tools.reduce((activity, tool) => countCall(activity, id, tool), start)
}

test('counts calls and resets the streak on a file change', () => {
  const activity = calls(['Read', 'Grep', 'Edit', 'Bash'])
  expect(activity.a1).toEqual({ calls: 4, sinceEdit: 1 })
})

test('a running write lane past the threshold without edits is reported once per lane', () => {
  const activity = calls(Array(STALL_CALLS).fill('Read'))
  const agents = [
    { id: 'a1', type: 'fleet-implement', status: 'running' },
    { id: 'a1x', type: 'fleet-implement', status: 'running' },
  ]
  expect(stallNotices(agents, activity)).toEqual([
    `fleet-implement has made ${STALL_CALLS} tool calls without changing a file: check it in /fleet-tasks, or stop it if it is looping.`,
  ])
})

test('a lane that edited earlier is reported as stalled since its last change', () => {
  const activity = calls(['Write', ...Array(STALL_CALLS).fill('Bash')])
  const lines = stallNotices([{ id: 'a1', type: 'fleet-tests', status: 'running' }], activity)
  expect(lines[0]).toContain('since its last file change')
})

test('below the threshold, finished lanes, read-only lanes and other agents are not reported', () => {
  const busy = calls(Array(STALL_CALLS).fill('Read'))
  expect(stallNotices([{ id: 'a1', type: 'fleet-implement-fast', status: 'running' }], calls(Array(STALL_CALLS - 1).fill('Read')))).toEqual([])
  for (const agent of [
    { id: 'a1', type: 'fleet-implement', status: 'completed' },
    { id: 'a1', type: 'fleet-research-codebase', status: 'running' },
    { id: 'a1', type: 'fleet-review', status: 'running' },
    { id: 'a1', type: 'general-purpose', status: 'running' },
    { id: 'bad id', type: 'fleet-implement', status: 'running' },
  ]) expect(stallNotices([agent], busy)).toEqual([])
})
