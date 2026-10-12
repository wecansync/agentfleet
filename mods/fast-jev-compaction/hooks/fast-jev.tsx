import { atom, read, update } from 'claude-code';
import type { CoreEngineInterface } from 'claude-code';
import { addEntry, bandText, claudeDirectory, commandText, compactPercent, emptyStats, parseStats } from '../src/stats.js';
import type { Entry, SessionEntry, Stats } from '../src/stats.js';
import { estimateTokens } from '../src/state.js';
import type {
  On,
  PluginOptions,
  Register,
  SessionMessage,
  ToolResultSummary,
  ToolUseSummary,
  TurnCompleteInput,
} from 'claude-code';

import { compact, reductionRatio, resolveOptions } from '../src/compact.js';
import { buildJevRequest, DEFAULT_MODEL, parseJevResponse } from '../src/request.js';
import type {
  CompactOptions,
  CompactResult,
  JevAsker,
  Message,
  ToolResult,
  ToolUse,
} from '../src/types.js';

const HOOK_DEFAULTS = {
  minReductionRatio: 0.25,
  model: DEFAULT_MODEL,
};

export type HookFetchInit = {
  method?: string;
  headers?: Record<string, string>;
  body?: string;
};

export type HookFetchResponse = {
  status: number;
  ok: boolean;
  text: string;
};

/** The shape of `$.http.fetch`, so the hook can be driven without an engine. */
export type HookFetch = (url: string, init?: HookFetchInit) => Promise<HookFetchResponse>;

export type HookConfig = CompactOptions & {
  apiKey?: string;
  minReductionRatio: number;
  model: string;
};

function optionNumber(options: PluginOptions, key: string, fallback: number): number {
  const value = options[key];
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback;
}

function optionString(options: PluginOptions, key: string): string | undefined {
  const value = options[key];
  return typeof value === 'string' && value.length > 0 ? value : undefined;
}

/** Reads the plugin's `userConfig` values; anything missing takes the defaults. */
export function resolveHookConfig(options: PluginOptions): HookConfig {
  const numbers: Partial<Omit<CompactOptions, 'goal'>> = {};
  for (const key of [
    'keepThreshold',
    'preserveRecentMessages',
    'maxStateTokens',
    'maxRequestTokens',
    'truncateHeadChars',
  ] as const) {
    const value = options[key];
    if (typeof value === 'number' && Number.isFinite(value)) numbers[key] = value;
  }
  const config: HookConfig = {
    ...numbers,
    minReductionRatio: optionNumber(
      options,
      'minReductionRatio',
      HOOK_DEFAULTS.minReductionRatio,
    ),
    model: optionString(options, 'model') ?? HOOK_DEFAULTS.model,
  };
  const apiKey = optionString(options, 'apiKey');
  if (apiKey) config.apiKey = apiKey;
  const goal = optionString(options, 'goal');
  if (goal) config.goal = goal;
  return config;
}

/** A `JevAsker` over the engine's `$.http.fetch`. */
export function jevAsker(fetchFn: HookFetch, apiKey: string, model: string): JevAsker {
  return {
    async ask(state, questions) {
      const request = buildJevRequest({ apiKey, model }, state, questions);
      const response = await fetchFn(request.url, {
        method: request.method,
        headers: request.headers,
        body: request.body,
      });
      return parseJevResponse(response.status, response.ok, response.text);
    },
  };
}

function toolUseSummary(tool: ToolUse): ToolUseSummary {
  const summary: ToolUseSummary = {
    tool_use_id: tool.tool_use_id,
    tool: tool.tool,
    input: tool.input,
  };
  if (tool.text !== undefined) summary.text = tool.text;
  if (tool.isError) summary.isError = true;
  return summary;
}

function toolResultSummary(result: ToolResult): ToolResultSummary {
  return {
    tool_use_id: result.tool_use_id,
    text: result.text,
    isError: result.isError ?? false,
  };
}

/**
 * Maps the library's output back onto session messages. Whatever came back
 * unchanged (a message, a tool use, a tool result) is the engine's own object,
 * handle included; anything rebuilt is a fresh message without a handle, so the
 * engine takes the edited content instead of its original.
 */
export function toSessionMessages(
  input: readonly SessionMessage[],
  output: readonly Message[],
): SessionMessage[] {
  const messages = new Map<Message, SessionMessage>();
  const uses = new Map<ToolUse, ToolUseSummary>();
  const results = new Map<ToolResult, ToolResultSummary>();
  for (const message of input) {
    messages.set(message, message);
    for (const tool of message.toolUses) uses.set(tool, tool);
    for (const result of message.toolResults ?? []) results.set(result, result);
  }
  return output.map((message) => {
    const own = messages.get(message);
    if (own) return own;
    const rebuilt: SessionMessage = {
      role: message.role,
      text: message.text,
      toolUses: message.toolUses.map((tool) => uses.get(tool) ?? toolUseSummary(tool)),
    };
    if (message.toolResults && message.toolResults.length > 0) {
      rebuilt.toolResults = message.toolResults.map(
        (result) => results.get(result) ?? toolResultSummary(result),
      );
    }
    return rebuilt;
  });
}

export type SessionCompaction = {
  result: CompactResult;
  messages: SessionMessage[];
};

/** Runs the library over a session transcript; throws when the key is missing or Jev fails. */
export async function compactSession(
  messages: readonly SessionMessage[],
  config: HookConfig,
  fetchFn: HookFetch,
): Promise<SessionCompaction> {
  if (!config.apiKey) throw new Error('TYPESAFE_API_KEY is not configured');
  const result = await compact(messages, jevAsker(fetchFn, config.apiKey, config.model), config);
  return { result, messages: toSessionMessages(messages, result.messages) };
}

function percent(ratio: number): string {
  return `${Math.round(ratio * 100)}%`;
}

export function summarize(result: CompactResult): string {
  const { stats } = result;
  const parts = [
    stats.kept > 0 ? `${stats.kept} kept` : '',
    stats.resultsDropped > 0 ? `${stats.resultsDropped} results truncated` : '',
    stats.callsDropped > 0 ? `${stats.callsDropped} call_dropped` : '',
    stats.pinned > 0 ? `${stats.pinned} pinned` : '',
  ].filter(Boolean);
  return `${percent(reductionRatio(result))} reduction; ${
    parts.join(', ') || 'no tool calls'
  }; state ~${stats.stateTokens} tokens (${stats.stateStage}) in ${stats.requests} request(s)`;
}

const UI_LOG_MAX_CHARS = 4096;

export function decisionLog(result: CompactResult): string {
  return result.decisions
    .filter((d) => d.reason !== 'pinned')
    .map(
      (d) =>
        `${d.id}:${d.tool}:${d.action}/call=${d.keepCall.toFixed(2)}/result=${d.keepResult.toFixed(2)}`,
    )
    .join(' ');
}

export function decisionLogLines(
  result: CompactResult,
  maxChars: number = UI_LOG_MAX_CHARS,
): string[] {
  const entries = decisionLog(result).split(' ').filter(Boolean);
  if (entries.length === 0) return ['decisions: (none)'];
  const chunks: string[] = [];
  let current = '';
  for (const entry of entries) {
    const next = current ? `${current} ${entry}` : entry;
    if (current && next.length > maxChars - 24) {
      chunks.push(current);
      current = entry;
    } else current = next;
  }
  chunks.push(current);
  return chunks.map((chunk, index) =>
    chunks.length === 1
      ? `decisions: ${chunk}`
      : `decisions (${index + 1}/${chunks.length}): ${chunk}`,
  );
}

async function getApiKey(
  $: {
    env: { get: (name: string) => Promise<string | undefined> };
  },
  config: HookConfig,
): Promise<string | undefined> {
  if (config.apiKey) return config.apiKey;
  const fromEnv = await $.env.get('TYPESAFE_API_KEY');
  if (fromEnv) return fromEnv;
  return undefined;
}

// AgentFleet: redact the resolved credential and strip unsafe log characters.
function safeLog(text: string, apiKey?: string): string {
  const redacted = apiKey ? text.split(apiKey).join('[redacted]') : text;
  return redacted.replace(/[\u0000-\u001f\u007f-\u009f\p{Cf}\u2028\u2029]/gu, '');
}

function notify(
  $: {
    ui: {
      log: (text: string) => void;
    };
  },
  text: string,
  apiKey?: string,
): void {
  const safe = safeLog(text, apiKey);
  $.ui.log(safe);
  // The persistent AbovePrompt band replaces the short-lived toast.
}

const band = atom({ plugin: 'fast-jev-compaction', key: 'band' } as const, { text: null as string | null });

async function readSource($: CoreEngineInterface, path: string): Promise<string | null> {
  try {
    const stat = await $.fs.stat(path, { resolve: true });
    if (stat.kind !== 'file' || stat.realPath !== path || stat.size > 512 * 1024) return null;
    return await $.fs.read(path);
  } catch { return null; }
}

async function persist($: CoreEngineInterface, stats: Stats): Promise<void> {
  const dir = claudeDirectory($.plugin.root);
  if (!dir) return;
  const path = `${dir}/agentfleet/jev-stats.json`;
  try {
    const parent = await $.fs.stat(`${dir}/agentfleet`, { resolve: true });
    if (parent.kind !== 'directory' || parent.realPath !== `${dir}/agentfleet`) return;
    if (await $.fs.exists(path)) {
      const stat = await $.fs.stat(path, { resolve: true });
      if (stat.kind !== 'file' || stat.realPath !== path || stat.size > 512 * 1024) return;
    }
    await $.fs.write(path, JSON.stringify(stats));
  } catch { /* Optional persistence must not affect compaction. */ }
}

async function contextTokens($: CoreEngineInterface): Promise<number | null> {
  try { const n = (await $.session.usage()).context?.tokens;
    return typeof n === 'number' && Number.isFinite(n) && n >= 0 ? n : null;
  } catch { return null; }
}

async function showBand($: CoreEngineInterface, text: string | null): Promise<void> {
  await update($, band, () => ({ text }));
}

export const register: Register = (on: On, options: PluginOptions) => {
  const configured = resolveHookConfig(options);
  let noKeyWarned = false;
  let session = emptyStats();
  let lifetime = emptyStats();
  let loaded = false;
  let writes = Promise.resolve();

  on('session.start', async ($, e, next) => {
    noKeyWarned = false;
    session = emptyStats();
    loaded = false;
    await showBand($, null);
    await $.command.register({ name: 'jev', description: 'Show JEV compaction status and session statistics' });
    return next(e);
  });
  on('prompt.submit', async ($, e, next) => {
    await showBand($, null);
    return next(e);
  });
  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    const other = await next(e);
    const current = await read($, band);
    if (!current.text || e.props.hasSurvey) return other;
    const { Box, Text, Button } = $.ui.resolve(e);
    // No width on the wrapper: the engine rejects another mod's node under a
    // Box with width, so only our own row is sized.
    return <Box flexDirection="column">
      {other}
      <Box width={e.props.bodyColumns}><Text wrap="wrap">{current.text}</Text><Button key="jev-hide" label="Hide" onPress={() => showBand($, null)} /></Box>
    </Box>;
  });
  on('command.run', { command: 'jev' }, async $ => {
    const apiKey = await getApiKey($, configured);
    const dir = claudeDirectory($.plugin.root);
    const source = dir ? await readSource($, `${dir}/agentfleet/config.json`) : null;
    return { text: commandText(Boolean(apiKey), compactPercent(source), session).split('\n').map(line => safeLog(line, apiKey)).join('\n') };
  });

  on('session.compact', async ($, event, next) => {
    if (event.agentId) return next(event);
    if (event.trigger === 'precompute') return next(event);

    const started = await $.clock.now();
    const tokensBefore = await contextTokens($);
    let outcome: Entry['outcome'] = 'fallback';
    let reason: string | null = null;
    let kept: SessionMessage[] | null = null;
    let ratio: number | null = null;
    let tokensAfter: number | null = null;
    let notice = true;
    let apiKey = configured.apiKey;
    try {
      apiKey = await getApiKey($, configured);
      if (!apiKey) {
        notice = !noKeyWarned;
        if (!noKeyWarned) {
          notify(
            $,
            "fast-jev-compaction: no TypeSafe API key, so Claude Code's built-in summary is used. Set the key with /plugin or TYPESAFE_API_KEY."
          );
          noKeyWarned = true;
        }
        outcome = 'no-key';
        reason = 'no TypeSafe API key configured';
        // The first notice was logged above; only its band is shown once.
      } else {
      const config = { ...configured, apiKey };
      const { result, messages } = await compactSession(event.messages, config, async (url, init) => {
        const response = await $.http.fetch(url, init);
        return { status: response.status, ok: response.ok, text: response.text };
      });
      for (const line of decisionLogLines(result)) $.ui.log(safeLog(line, apiKey));
      if (reductionRatio(result) < config.minReductionRatio) {
        notify(
          $,
          `fallback to built-in summary (below ${percent(config.minReductionRatio)} minimum: ${summarize(result)})`,
          apiKey,
        );
        reason = `below ${percent(config.minReductionRatio)} minimum reduction`;
      } else {
        outcome = 'jev';
        kept = messages;
        ratio = reductionRatio(result);
        tokensAfter = estimateTokens(JSON.stringify(result.messages.map(message => ({
          role: message.role, text: message.text,
          toolUses: message.toolUses.map(tool => ({ tool: tool.tool, input: tool.input, text: tool.text })),
          toolResults: message.toolResults?.map(result => ({ text: result.text })),
        }))));
        notify($, `kept ${messages.length}/${event.messages.length} messages, no summary (${summarize(result)})`, apiKey);
      }
      }
    } catch (error) {
      notify(
        $,
        `fallback to built-in summary (${error instanceof Error ? error.message : String(error)})`,
        apiKey,
      );
      // Do not retain service error text: it can contain echoed conversation content.
      reason = 'JEV request or compaction failed';
    }
    const finished = await $.clock.now();
    const ms = Math.max(0, finished - started);
    const entry: Entry = {
      at: new Date(finished).toISOString(), outcome,
      trigger: safeLog(event.trigger, apiKey), tokensBefore, tokensAfter,
      messagesBefore: outcome === 'jev' ? event.messages.length : null,
      messagesKept: kept?.length ?? null, ms, reason,
    };
    const sessionEntry: SessionEntry = { ...entry, reductionRatio: ratio, durationMs: ms };
    session = addEntry(session, sessionEntry);
    const queue = writes.then(async () => {
      if (!loaded) {
        const dir = claudeDirectory($.plugin.root);
        lifetime = parseStats(dir ? await readSource($, `${dir}/agentfleet/jev-stats.json`) : null);
        loaded = true;
      }
      lifetime = addEntry(lifetime, entry);
      await persist($, lifetime);
    });
    writes = queue.catch(() => {});
    await writes;
    if (notice) await showBand($, safeLog(bandText({ ...entry, reductionRatio: ratio, durationMs: ms }), apiKey));
    return kept ? { messages: kept } : next(event);
  });
};

export { resolveOptions };
