import type { AgentSpawnInput, CoreEngineInterface, On, Register, ToolCallInput } from 'claude-code';
import { addNote, budgetNote, recordCall, turnLimit } from './budget.js';
import { claudeDirectory, failoverModel, laneName, markDescription, SOURCE_LIMIT_BYTES } from './failover.js';
import { forbiddenReason, mcpForbidden, redactSecrets, type ForbiddenResult, type GuardMode } from './rules.js';

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

type Hit = { forbidden: ForbiddenResult; rawCommand: string };

// The command a tool call would run, if it breaks the given rules.
function check(e: ToolCallInput, mode: GuardMode): Hit | null {
  if (e.tool === 'Bash' || e.tool === 'PowerShell') {
    const rawCommand = (e as any).command ?? '';
    const forbidden = forbiddenReason(rawCommand, mode);
    return forbidden ? { forbidden, rawCommand } : null;
  }
  if (typeof e.tool === 'string' && e.tool.startsWith('mcp__')) {
    const { tool, tool_use_id, agentId, ...mcpArgs } = e as any;
    const forbidden = mcpForbidden(mcpArgs, e.tool, mode);
    if (!forbidden) return null;
    const rawCommand =
      typeof (mcpArgs as any).query === 'string'
        ? (mcpArgs as any).query
        : typeof (mcpArgs as any).sql === 'string'
          ? (mcpArgs as any).sql
          : typeof (mcpArgs as any).command === 'string'
            ? (mcpArgs as any).command
            : JSON.stringify(mcpArgs);
    return { forbidden, rawCommand };
  }
  if (
    mode === 'lane' &&
    (e.tool === 'Edit' || e.tool === 'Write' || e.tool === 'MultiEdit' || e.tool === 'NotebookEdit') &&
    checkGitEdit(e)
  ) {
    return {
      forbidden: { rule: 'git-history', label: 'git history or working-tree' },
      rawCommand: (e as any).file_path ?? (e as any).path ?? (e as any).notebook_path ?? '.git',
    };
  }
  return null;
}

// Logging is best-effort: a failure to log must never decide a tool call.
function log($: CoreEngineInterface, text: string): void {
  try {
    $.ui.log(text);
  } catch {
    // The decision stands without the log line.
  }
}

function shown(rawCommand: string, length: number): string {
  return redactSecrets(rawCommand)
    .slice(0, length)
    .replace(/[\u0000-\u001f\u007f-\u009f\p{Cf}\p{Cc}]/gu, '');
}

// `agentfleet config main-session-guard on` writes mainSessionGuard to the
// preferences; read only when a command matches, so other calls cost nothing.
async function mainGuardOn($: CoreEngineInterface): Promise<boolean> {
  const base = claudeDirectory($.plugin.root);
  if (!base) return false;
  try {
    return JSON.parse((await readSource($, `${base}/agentfleet/config.json`)) ?? '{}')?.mainSessionGuard === true;
  } catch {
    return false;
  }
}

const RUN = 'Run it';
const STOP = "Don't run it";

// Asks the user before the main session, or a subagent that is not a fleet
// lane, runs a command that discards work or data, or publishes. Anything but
// an explicit yes, including no one to ask (a -p run), stops the command.
async function confirm($: CoreEngineInterface, hit: Hit, who: string | undefined): Promise<{ deny: string } | null> {
  const command = shown(hit.rawCommand, 300);
  const where = who ? ` in ${who}` : '';
  const label = hit.forbidden.label;
  let answer: string;
  try {
    answer = await $.ui.ask(`AgentFleet guard: run this ${label} command${where}? ${command}`, {
      options: [RUN, STOP],
      header: 'Guard',
    });
  } catch {
    log($, `lane-guard stopped [${hit.forbidden.rule}]${where}, nobody confirmed it: ${shown(hit.rawCommand, 120)}`);
    return {
      deny: `AgentFleet's guard stopped this ${label} command: nobody confirmed it. Ask the user before you try it again.`,
    };
  }
  if (answer === RUN) return null;
  log($, `lane-guard stopped [${hit.forbidden.rule}]${where}, the user declined: ${shown(hit.rawCommand, 120)}`);
  if (answer === STOP) {
    return { deny: `The user declined to run this ${label} command. Do not retry it; ask the user how to go on.` };
  }
  return {
    deny: `The user did not confirm this ${label} command and answered: "${shown(answer, 500)}". Do not run the command as written; follow the answer.`,
  };
}

async function decide($: CoreEngineInterface, e: ToolCallInput): Promise<{ deny: string } | Hit | null> {
  const type = e.agentId ? await agentType($, e.agentId) : undefined;

  // The main session and subagents that are not fleet lanes: the opt-in guard.
  if (!e.agentId || (type && !type.startsWith('fleet-'))) {
    const hit = check(e, 'main');
    return hit && (await mainGuardOn($)) ? hit : null;
  }

  const hit = check(e, 'lane');
  if (!hit) {
    return null;
  }
  const { forbidden, rawCommand } = hit;

  const effectiveType = type || 'unknown subagent';
  const sanitized = shown(rawCommand, 120);

  log($, `lane-guard blocked [${forbidden.rule}] in ${effectiveType}: ${sanitized}`);
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
    let decision: { deny: string } | Hit | null = null;
    try {
      decision = await decide($, e);
    } catch {
      decision = null;
    }
    let denied: { deny: string } | null = null;
    if (decision && 'forbidden' in decision) {
      // Once a command needs the user's yes, any failure stops it.
      try {
        denied = await confirm($, decision, e.agentId ? await agentType($, e.agentId) : undefined);
      } catch {
        denied = { deny: `AgentFleet's guard stopped this ${decision.forbidden.label} command: nobody confirmed it. Ask the user before you try it again.` };
      }
    } else {
      denied = decision;
    }
    const result = denied ?? (await next(e));
    return withBudgetNote($, e, result);
  });
};
