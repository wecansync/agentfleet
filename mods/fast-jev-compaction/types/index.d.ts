declare module 'claude-code' {
  interface PluginState {
    'fast-jev-compaction': { band: { text: string | null } }
  }
}
export type JevBand = { text: string | null };
