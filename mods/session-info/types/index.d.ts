export type Launch = { at: number | null }
declare module 'claude-code' {
  interface PluginState {
    'session-info': { launch: Launch }
  }
}
