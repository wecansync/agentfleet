import { atom, read, update } from 'claude-code'
import type { CoreEngineInterface, Register } from 'claude-code'
import { fallbackText, loadBrain, memoryLine, type BrainView } from './brain'

const PANE = 'brain'
type PaneState = { open: boolean; view: BrainView | null; selected: number; expanded: boolean }
const pane = atom({ plugin: 'brain', key: 'pane' } as const, {
  open: false, view: null, selected: 0, expanded: false,
} as PaneState)

async function closePane($: CoreEngineInterface): Promise<void> {
  await update($, pane, current => ({ ...current, open: false }))
  await $.ui.close({ id: PANE })
}

async function moveSelection($: CoreEngineInterface, delta: number): Promise<void> {
  let selected = 0
  await update($, pane, current => {
    const maximum = Math.max(0, (current.view?.memories.length ?? 0) - 1)
    selected = Math.max(0, Math.min(maximum, current.selected + delta))
    return { ...current, selected }
  })
  await $.ui.focus({ requestId: PANE, key: `memory:${selected}` }).catch(() => undefined)
}

async function activateMemory($: CoreEngineInterface, index: number): Promise<void> {
  await update($, pane, current => ({
    ...current,
    selected: index,
    expanded: current.selected === index ? !current.expanded : true,
  }))
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    await $.command.register({
      name: 'brain',
      description: 'Show agent-brain token usage and project memories in a pane',
    })
    return next(e)
  })

  on('command.run', { command: 'brain' }, async $ => {
    const view = await loadBrain($)
    await update($, pane, () => ({ open: true, view, selected: 0, expanded: false }))
    const opened = await $.ui.open({ id: PANE, title: 'agent-brain', focus: true, closeOnEscape: true })
    if (!opened.isPlaced) {
      await update($, pane, current => ({ ...current, open: false }))
      return { text: fallbackText(view) }
    }
    return { text: 'agent-brain pane opened.' }
  })

  on('ui.close', { id: PANE }, async ($, e, next) => {
    await update($, pane, current => ({ ...current, open: false }))
    return next(e)
  })

  on('ui.focus', { requestId: PANE }, async ($, e, next) => {
    const row = /^memory:(\d+)$/u.exec(e.element ?? '')
    if (row) {
      const selected = Number(row[1])
      await update($, pane, current => ({ ...current, selected }))
    }
    return next(e)
  })

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const { Box, Text, Button } = $.ui.resolve(e)
    const current = await read($, pane)
    if (!current.view) return <Box flexDirection="column" width={e.props.bodyColumns} />
    const width = e.props.bodyColumns
    return (
      <Box flexDirection="column" width={width}>
        {current.view.usage.map((line, index) => <Text key={`usage:${index}`} wrap="wrap">{line}</Text>)}
        {current.view.memoryMessage && <Text key="memory-message" wrap="wrap">{current.view.memoryMessage}</Text>}
        {current.view.memories.length > 0 && <>
          <Text key="memories-heading" bold>{`Memories (${current.view.memories.length} of ${current.view.total})`}</Text>
          {current.view.memories.map((entry, index) => (
            <Box key={`memory-row:${index}`} flexDirection="column">
              <Button key={`memory:${index}`} label={memoryLine(entry, width)} plain autoFocus={index === current.selected || undefined}
                onPress={() => activateMemory($, index)} />
              {current.expanded && current.selected === index && (
                <Box key="expanded-memory" flexDirection="column">
                  <Text bold wrap="wrap">{`${entry.marker} ${entry.kind}`}</Text>
                  {entry.content.split('\n').map((line, lineIndex) => (
                    <Text key={`expanded:${lineIndex}`} wrap="wrap">{line || ' '}</Text>
                  ))}
                </Box>
              )}
            </Box>
          ))}
        </>}
        <Box key="brain-controls">
          <Button key="brain-previous" label="k previous" hotkey="k" plain onPress={() => moveSelection($, -1)} />
          <Button key="brain-next" label="j next" hotkey="j" plain onPress={() => moveSelection($, 1)} />
          <Button key="brain-close" label="Close" role="dismiss" onPress={() => closePane($)} />
        </Box>
      </Box>
    )
  })

}
