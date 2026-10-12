export type Entry = {
  at: string; outcome: 'jev' | 'fallback' | 'no-key'; trigger: string;
  tokensBefore: number | null; tokensAfter: number | null;
  messagesBefore: number | null; messagesKept: number | null;
  ms: number; reason: string | null;
};
export type SessionEntry = Entry & { reductionRatio: number | null; durationMs: number };
export type Totals = { compactions: number; jev: number; fallbacks: number; tokensSaved: number };
export type Stats = { format: 'agentfleet.jev-stats.v1'; updated: string; totals: Totals; recent: Entry[] };
export function emptyStats(): Stats {
  return { format: 'agentfleet.jev-stats.v1', updated: '', totals: { compactions: 0, jev: 0, fallbacks: 0, tokensSaved: 0 }, recent: [] };
}
export function addEntry(stats: Stats, entry: Entry): Stats {
  return { format: stats.format, updated: entry.at, totals: {
    compactions: stats.totals.compactions + 1,
    jev: stats.totals.jev + Number(entry.outcome === 'jev'),
    fallbacks: stats.totals.fallbacks + Number(entry.outcome !== 'jev'),
    tokensSaved: stats.totals.tokensSaved + (entry.outcome === 'jev' && entry.tokensBefore !== null && entry.tokensAfter !== null ? Math.max(0, entry.tokensBefore - entry.tokensAfter) : 0),
  }, recent: [entry, ...stats.recent].slice(0, 20) };
}
export function claudeDirectory(root: string): string | null {
  const match = /^(.*)\/agentfleet\/mods\/fast-jev-compaction$/.exec(root.replace(/\\/g, '/').replace(/\/+$/, ''));
  const dir = match?.[1];
  return dir && dir !== '/' && !/^[A-Za-z]:$/.test(dir) ? dir : null;
}
export function compactPercent(source: string | null): number | 'off' {
  try { const value = JSON.parse(source ?? '{}').compactPercent;
    return value === 'off' || (Number.isInteger(value) && value >= 20 && value <= 95) ? value : 70;
  } catch { return 70; }
}
function tokens(n: number): string { return n >= 1000 ? `${Math.round(n / 1000)}k` : String(Math.round(n)); }
export function bandText(entry: SessionEntry): string {
  if (entry.outcome !== 'jev') return `JEV fell back to Claude's summary: ${entry.reason ?? 'no API key configured'}`;
  const parts: string[] = [];
  if (entry.tokensBefore !== null && entry.tokensAfter !== null) parts.push(`${tokens(entry.tokensBefore)} → ~${tokens(entry.tokensAfter)} tokens`);
  else if (entry.tokensAfter !== null) parts.push(`~${tokens(entry.tokensAfter)} tokens after`);
  if (entry.reductionRatio !== null) parts.push(`−${Math.round(entry.reductionRatio * 100)}%`);
  if (entry.messagesKept !== null && entry.messagesBefore !== null) parts.push(`kept ${entry.messagesKept} of ${entry.messagesBefore} messages word-for-word`);
  parts.push(`${(entry.durationMs / 1000).toFixed(1)} s`);
  return `JEV compacted this conversation: ${parts.join(', ')}`;
}
export function commandText(key: boolean, percent: number | 'off', stats: Stats): string {
  const t = stats.totals;
  return [`JEV: on; API key configured: ${key ? 'yes' : 'no'}`,
    `Auto-compact: ${percent === 'off' ? 'Claude Code default' : `${percent}% of each model's context`}`,
    `This session: ${t.compactions} compactions, ${t.jev} JEV, ${t.fallbacks} fallbacks, ${t.tokensSaved} tokens saved (estimated)`,
    ...stats.recent.slice(0, 5).map(e => `${e.at} ${e.outcome} (${e.trigger})${e.reason ? `: ${e.reason}` : ''}${e.tokensBefore !== null && e.tokensAfter !== null ? `; ${e.tokensBefore} → ~${e.tokensAfter} tokens` : ''}; ${e.ms} ms`),
  ].join('\n');
}
/** Reconstruct only the documented fields; never echo arbitrary persisted text. */
export function parseStats(source: string | null): Stats {
  const empty = emptyStats();
  try {
    const v = JSON.parse(source ?? '{}');
    if (v.format !== empty.format || !v.totals || !Array.isArray(v.recent)) return empty;
    for (const k of ['compactions', 'jev', 'fallbacks', 'tokensSaved'] as const) {
      if (!Number.isSafeInteger(v.totals[k]) || v.totals[k] < 0) return empty;
      empty.totals[k] = v.totals[k];
    }
    // Lifetime counters survive reloads; untrusted historical text is deliberately not replayed.
    empty.recent = v.recent.slice(0, 20).filter((e: any) =>
      ['jev', 'fallback', 'no-key'].includes(e.outcome) && ['auto', 'manual', 'plugin'].includes(e.trigger) &&
      typeof e.at === 'string' && /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$/.test(e.at) &&
      ['tokensBefore', 'tokensAfter', 'messagesBefore', 'messagesKept'].every(k => e[k] === null || (Number.isSafeInteger(e[k]) && e[k] >= 0)) && Number.isSafeInteger(e.ms) && e.ms >= 0
    ).map((e: Entry) => ({ at: e.at, outcome: e.outcome, trigger: e.trigger, tokensBefore: e.tokensBefore, tokensAfter: e.tokensAfter, messagesBefore: e.messagesBefore, messagesKept: e.messagesKept, ms: e.ms, reason: e.outcome === 'jev' ? null : 'built-in summary' }));
    return empty;
  } catch { return emptyStats(); }
}
