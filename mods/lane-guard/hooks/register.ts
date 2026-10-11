import type { CoreEngineInterface, On, Register, ToolCallInput } from 'claude-code';
import { forbiddenReason, mcpForbidden, redactSecrets } from './rules.js';

const agentTypeCache = new Map<string, string>();

function hasGitDirSegment(filePath: string): boolean {
  if (!filePath || typeof filePath !== 'string') return false;
  return filePath.split(/[\\/]/).includes('.git');
}

function checkGitEdit(e: any): boolean {
  const direct = e.file_path ?? e.path ?? e.notebook_path;
  if (typeof direct === 'string' && hasGitDirSegment(direct)) return true;
  if (Array.isArray(e.edits)) {
    for (const edit of e.edits) {
      const p = edit?.file_path ?? edit?.path;
      if (typeof p === 'string' && hasGitDirSegment(p)) return true;
    }
  }
  return false;
}

async function decide($: CoreEngineInterface, e: ToolCallInput): Promise<{ deny: string } | null> {
  if (!e.agentId) {
    return null;
  }

  let type = agentTypeCache.get(e.agentId);
  if (!type) {
    try {
      const list = await $.agent.list();
      for (const info of list) {
        if (info.id && info.type) {
          agentTypeCache.set(info.id, info.type);
        }
      }
      type = agentTypeCache.get(e.agentId);
    } catch {
      // Agent list failed; type remains undefined
    }
  }

  // If agent type is resolved and it is NOT a fleet lane, allow
  if (type && !type.startsWith('fleet-')) {
    return null;
  }

  let forbidden: { rule: string; label: string } | null = null;
  let rawCommand = '';

  if (e.tool === 'Bash' || e.tool === 'PowerShell') {
    rawCommand = (e as any).command ?? '';
    forbidden = forbiddenReason(rawCommand);
  } else if (typeof e.tool === 'string' && e.tool.startsWith('mcp__')) {
    const { tool, tool_use_id, agentId, ...mcpArgs } = e as any;
    forbidden = mcpForbidden(mcpArgs, e.tool);
    rawCommand =
      typeof (mcpArgs as any).query === 'string'
        ? (mcpArgs as any).query
        : typeof (mcpArgs as any).sql === 'string'
          ? (mcpArgs as any).sql
          : typeof (mcpArgs as any).command === 'string'
            ? (mcpArgs as any).command
            : JSON.stringify(mcpArgs);
  } else if (
    e.tool === 'Edit' ||
    e.tool === 'Write' ||
    e.tool === 'MultiEdit' ||
    e.tool === 'NotebookEdit'
  ) {
    if (checkGitEdit(e)) {
      forbidden = {
        rule: 'git-history',
        label: 'git history or working-tree',
      };
      rawCommand = (e as any).file_path ?? (e as any).path ?? (e as any).notebook_path ?? '.git';
    }
  }

  if (!forbidden) {
    return null;
  }

  const effectiveType = type || 'unknown subagent';
  const sanitized = redactSecrets(rawCommand)
    .slice(0, 120)
    .replace(/[\u0000-\u001f\u007f-\u009f\p{Cf}\p{Cc}]/gu, '');

  $.ui.log(`lane-guard blocked [${forbidden.rule}] in ${effectiveType}: ${sanitized}`);
  return {
    deny: `AgentFleet lane-guard blocked this ${forbidden.label} command in ${effectiveType}. Fleet lanes may not commit, push, merge, deploy, publish, run migrations or destructive SQL, or revert the working tree. Report the command to the main agent instead; it decides whether to run it.`,
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
