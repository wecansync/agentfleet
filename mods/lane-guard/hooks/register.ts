import type { AgentSpawnInput, CoreEngineInterface, On, Register, ToolCallInput } from 'claude-code';
import { addNote, budgetNote, recordCall, turnLimit } from './budget.js';
import { claudeDirectory, failoverModel, laneName, markDescription, SOURCE_LIMIT_BYTES } from './failover.js';
import { forbiddenReason, mcpForbidden, redactSecrets, type ForbiddenResult } from './rules.js';

const agentTypeCache = new Map<string, string>();
const turnLimitCache = new Map<string, number | null>();

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

async function agentType($: CoreEngineInterface, agentId: string): Promise<string | undefined> {
  let type = agentTypeCache.get(agentId);
  if (!type) {
    try {
      const list = await $.agent.list();
      for (const info of list) {
        if (info.id && info.type) {
          agentTypeCache.set(info.id, info.type);
        }
      }
      type = agentTypeCache.get(agentId);
    } catch {
      // Agent list failed; type remains undefined
    }
  }
  return type;
}

async function decide($: CoreEngineInterface, e: ToolCallInput): Promise<{ deny: string } | null> {
  if (!e.agentId) {
    return null;
  }

  const type = await agentType($, e.agentId);

  // If agent type is resolved and it is NOT a fleet lane, allow
  if (type && !type.startsWith('fleet-')) {
    return null;
  }

  let forbidden: ForbiddenResult | null = null;
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
  const closing =
    forbidden.hint ??
    'Fleet lanes may not commit, push, merge, deploy, publish, run migrations or destructive SQL, or revert the working tree. Report the command to the main agent instead; it decides whether to run it.';
  return {
    deny: `AgentFleet lane-guard blocked this ${forbidden.label} command in ${effectiveType}. ${closing}`,
  };
}

async function readSource($: CoreEngineInterface, path: string): Promise<string | null> {
  try {
    const stat = await $.fs.stat(path, { resolve: true });
    if (stat.kind !== 'file' || stat.realPath !== path || stat.size > SOURCE_LIMIT_BYTES) return null;
    return await $.fs.read(path);
  } catch {
    return null;
  }
}

// A lane's maxTurns from its agent file, read once per type.
async function laneTurnLimit($: CoreEngineInterface, type: string): Promise<number | null> {
  if (turnLimitCache.has(type)) return turnLimitCache.get(type) ?? null;
  const base = claudeDirectory($.plugin.root);
  const limit = base && laneName(type) ? turnLimit(await readSource($, `${base}/agents/${type}.md`)) : null;
  turnLimitCache.set(type, limit);
  return limit;
}

// Count a fleet lane's call and, as it nears its maxTurns, add a note to the
// result. Any failure here returns the result as it came.
async function withBudgetNote($: CoreEngineInterface, e: ToolCallInput, result: any): Promise<any> {
  try {
    if (!e.agentId) return result;
    const type = await agentType($, e.agentId);
    if (!type || !type.startsWith('fleet-')) return result;
    const budget = recordCall(e.agentId);
    if (result.deny !== undefined) return result;
    const note = budgetNote(budget, await laneTurnLimit($, type));
    return note ? addNote(result, note) : result;
  } catch {
    return result;
  }
}

// Start a fleet lane on another of its models while its own model is down.
// Returns the rewrite, or null to leave the spawn as the caller made it.
async function failover($: CoreEngineInterface, e: AgentSpawnInput): Promise<Partial<AgentSpawnInput> | null> {
  // A fork inherits its parent's model, a workflow agent cannot be rewritten,
  // and a caller that named a model chose it on purpose.
  if (e.fork || e.workflow || e.isTeammate || e.model) return null;
  const lane = laneName(e.subagentType);
  const base = claudeDirectory($.plugin.root);
  if (!lane || !base) return null;
  const project = await $.session.root().catch(() => null);
  const [fleet, outages, local] = await Promise.all([
    readSource($, `${base}/fleet.json`),
    readSource($, `${base}/agentfleet/outages.json`),
    project ? readSource($, `${project.replace(/[\\/]+$/, '')}/.claude/fleet.local.json`) : Promise.resolve(null),
  ]);
  const model = failoverModel(fleet, outages, lane, await $.clock.now(), local);
  if (!model) return null;
  $.ui.log(`lane-guard: fleet-${lane}'s model is down; starting it on ${model}`);
  return { model, description: markDescription(e.description, model) };
}

export const register: Register = (on: On) => {
  on('agent.spawn', async ($, e, next) => {
    // As for tool.call: decide in try, call next() once outside it.
    let rewrite: Partial<AgentSpawnInput> | null = null;
    try {
      rewrite = await failover($, e);
    } catch {
      rewrite = null;
    }
    return next(rewrite ? { ...e, ...rewrite } : e);
  });
  on('tool.call', async ($, e, next) => {
    // Decide inside try, but call next() exactly once outside it: retrying
    // next() from a catch would run the tool a second time if it threw.
    let denied: { deny: string } | null = null;
    try {
      denied = await decide($, e);
    } catch {
      denied = null;
    }
    const result = denied ?? (await next(e));
    return withBudgetNote($, e, result);
  });
};
