// Turn-budget notes. A fleet lane that runs out of its maxTurns (set in its
// agent file's frontmatter) is cut off without reporting, and its work is
// lost. The tool.call hook counts a lane's calls and, after a call returns,
// adds a note the lane reads after the result: one at 70% of its turns, one
// at 90%. A parallel batch counts each call, so a note may come a turn early.

export type Budget = { count: number, sent: 0 | 1 | 2 }

// Per agent id, for the life of the session.
const budgets = new Map<string, Budget>()

const MAX_TURNS_LIMIT = 100000

// `maxTurns: N` in the frontmatter of an agent file, or null.
export function turnLimit(source: string | null): number | null {
  const match = source ? /^---\r?\n([\s\S]*?)\r?\n---(?:\r?\n|$)/.exec(source) : null
  const value = match ? /^maxTurns:[ \t]*["']?(\d{1,6})["']?[ \t]*$/m.exec(match[1])?.[1] : undefined
  const limit = value ? Number(value) : 0
  return limit >= 1 && limit <= MAX_TURNS_LIMIT ? limit : null
}

// Counts one more call by an agent.
export function recordCall(agentId: string): Budget {
  let budget = budgets.get(agentId)
  if (!budget) {
    budget = { count: 0, sent: 0 }
    budgets.set(agentId, budget)
  }
  budget.count += 1
  return budget
}

// The note due now, or null. Each note is given once per agent; when one call
// passes both thresholds (a small limit), only the later note is given.
export function budgetNote(budget: Budget, limit: number | null): string | null {
  if (limit === null) return null
  const { count } = budget
  if (budget.sent < 2 && count >= Math.ceil(limit * 9 / 10)) {
    budget.sent = 2
    return `AgentFleet: about ${count} of ${limit} turns used. Report now, even if unfinished: what is done, what remains, and the files you changed.`
  }
  if (budget.sent < 1 && count >= Math.ceil(limit * 7 / 10)) {
    budget.sent = 1
    return `AgentFleet: you have used about ${count} of your ${limit} turns. Finish the part you are on, run its check, and report what is done, what remains, and the files you changed. Do not start another part.`
  }
  return null
}

// The result with the note after anything the tool already added. A denied
// call has no result to add to, so it is returned as it came.
export function addNote<T extends { deny?: string, context?: readonly string[] }>(result: T, note: string): T {
  if (result.deny !== undefined) return result
  return { ...result, context: [...(result.context ?? []), note] }
}
