import type { CoreEngineInterface, On, Register, ToolCallInput } from 'claude-code';
import { forbiddenReason, mcpForbidden } from './rules.js';

const agentTypeCache = new Map<string, string>();

async function decide($: CoreEngineInterface, e: ToolCallInput): Promise<{ deny: string } | null> {
  if (!e.agentId) {
    return null;
  }

  let type = agentTypeCache.get(e.agentId);
  if (!type) {
    const list = await $.agent.list();
    for (const info of list) {
      if (info.id && info.type) {
        agentTypeCache.set(info.id, info.type);
      }
    }
    type = agentTypeCache.get(e.agentId);
  }

  if (!type || !type.startsWith('fleet-')) {
    return null;
  }

  let forbidden: { rule: string; label: string } | null = null;
  let rawCommand = '';

  if (e.tool === 'Bash') {
    rawCommand = (e as any).command ?? '';
    forbidden = forbiddenReason(rawCommand);
  } else if (typeof e.tool === 'string' && e.tool.startsWith('mcp__')) {
    const { tool, tool_use_id, agentId, ...mcpArgs } = e as any;
    forbidden = mcpForbidden(mcpArgs);
    rawCommand = typeof (mcpArgs as any).query === 'string'
      ? (mcpArgs as any).query
      : typeof (mcpArgs as any).sql === 'string'
        ? (mcpArgs as any).sql
        : JSON.stringify(mcpArgs);
  }

  if (!forbidden) {
    return null;
  }
  const sanitized = rawCommand
    .slice(0, 120)
    .replace(/[\u0000-\u001f\u007f-\u009f\p{Cf}\p{Cc}]/gu, '');
  $.ui.log(`lane-guard blocked [${forbidden.rule}] in ${type}: ${sanitized}`);
  return {
    deny: `AgentFleet lane-guard blocked this ${forbidden.label} command in ${type}. Fleet lanes may not commit, push, merge, deploy, publish, run migrations or destructive SQL, or revert the working tree. Report the command to the main agent instead; it decides whether to run it.`,
  };
}

export const register: Register = (on: On) => {
  on('tool.call', async ($, e, next) => {
    // Decide inside try, but call next() exactly once outside it: retrying
    // next() from a catch would run the tool a second time if it threw.
    let denied: { deny: string } | null = null;
    try {
      denied = await decide($, e);
    } catch {
      denied = null;
    }
    return denied ?? next(e);
  });
};
