export type MemoryEntry = { marker: string; kind: string; content: string; critical: boolean }
export type BrainView = { usage: string[]; memories: MemoryEntry[]; total: number; memoryMessage: string | null }
export type PaneState = { open: boolean; view: BrainView | null; selected: number; expanded: boolean }
declare module 'claude-code' {
  interface PluginState {
    brain: { pane: PaneState }
  }
}
