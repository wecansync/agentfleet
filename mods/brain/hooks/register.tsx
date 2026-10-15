import type { Register } from 'claude-code'
import { runBrain } from './brain'

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    await $.command.register({
      name: 'brain',
      description: 'Show agent-brain token usage and the latest project memories',
    })
    return next(e)
  })

  on('command.run', { command: 'brain' }, async $ => runBrain($))
}
