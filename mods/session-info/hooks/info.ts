// Session info: the formatting behind the band, kept free of the engine so
// the tests can drive it directly.

export type Live = {
  model: string | null
  effort: string | null
  prompts: number | null
  costUsd: number | null
  agents: number
  branch: string | null
}

export type Part = { key: string; text: string; color?: string; bold?: boolean; dim?: boolean }

export const SEPARATOR = '  ·  '

export const EMPTY: Live = { model: null, effort: null, prompts: null, costUsd: null, agents: 0, branch: null }

// "<1m", "42m", "3h 05m", "2d 4h".
export function uptime(ms: number): string {
  const minutes = Math.floor(Math.max(0, ms) / 60_000)
  if (minutes < 1) return '<1m'
  if (minutes < 60) return `${minutes}m`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}h ${String(minutes % 60).padStart(2, '0')}m`
  return `${Math.floor(hours / 24)}d ${hours % 24}h`
}

export function money(usd: number): string {
  return usd >= 100 ? `$${Math.round(usd)}` : `$${usd.toFixed(2)}`
}

// The band's parts, most useful first: when the row is too narrow, the last
// ones are left out.
export function parts(live: Live, launchedAt: number | null, now: number): Part[] {
  const out: Part[] = []
  if (live.model) {
    out.push({ key: 'model', text: `◆ ${live.model}`, color: 'cyan', bold: true })
    if (live.effort) out.push({ key: 'effort', text: `effort ${live.effort}`, dim: true })
  }
  if (launchedAt !== null) out.push({ key: 'uptime', text: `up ${uptime(now - launchedAt)}` })
  if (live.agents > 0) {
    out.push({ key: 'agents', text: `${live.agents} ${live.agents === 1 ? 'agent' : 'agents'} running`, color: 'yellow' })
  }
  if (live.branch) out.push({ key: 'branch', text: `⎇ ${live.branch}`, color: 'green' })
  if (live.prompts !== null && live.prompts > 0) {
    out.push({ key: 'prompts', text: `${live.prompts} ${live.prompts === 1 ? 'prompt' : 'prompts'}`, dim: true })
  }
  if (live.costUsd !== null && live.costUsd > 0) {
    out.push({ key: 'cost', text: `${money(live.costUsd)} at list price`, dim: true })
  }
  return out
}

// The leading parts that fit in `columns`, separators included. The model is
// always kept: Text cuts it at the edge on a very narrow row.
export function fit(all: Part[], columns: number): Part[] {
  const kept: Part[] = []
  let used = 0
  for (const part of all) {
    const width = (kept.length ? SEPARATOR.length : 0) + [...part.text].length
    if (kept.length && used + width > columns) break
    kept.push(part)
    used += width
  }
  return kept
}

// A `.git` file in a worktree or submodule: "gitdir: <path>", relative to the
// folder that holds it when not absolute.
export function gitDir(dotGitFile: string, folder: string): string | null {
  const match = /^gitdir:\s*(.+?)\s*$/m.exec(dotGitFile)
  if (!match) return null
  const target = match[1]!
  return isAbsolute(target) ? target : `${folder}/${target}`
}

// HEAD's text: the branch name, or the short commit when HEAD is detached.
export function branchFromHead(head: string): string | null {
  const text = head.trim()
  const ref = /^ref:\s*refs\/heads\/(.+)$/.exec(text)
  if (ref) return ref[1]!
  if (/^[0-9a-f]{40,64}$/i.test(text)) return `detached ${text.slice(0, 7)}`
  return null
}

// The folder above `path`, or null at a filesystem root ("/", "C:\", "\\server\share").
export function parent(path: string): string | null {
  const trimmed = path.replace(/[\\/]+$/, '')
  const cut = Math.max(trimmed.lastIndexOf('/'), trimmed.lastIndexOf('\\'))
  if (cut < 0) return null
  const above = trimmed.slice(0, cut)
  if (above === '') return '/'
  if (/^[A-Za-z]:$/.test(above)) return `${above}\\`
  if (/^[\\/]{2}[^\\/]+$/.test(above)) return null
  return above
}

function isAbsolute(path: string): boolean {
  return path.startsWith('/') || path.startsWith('\\') || /^[A-Za-z]:[\\/]/.test(path)
}
