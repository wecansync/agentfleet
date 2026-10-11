# Upstream

This mod is a copy of [fast-jev-compaction](https://github.com/tamaratran/fast-jev-compaction/tree/e3f262a7f4d42bd8dd32ced30d26176f7cb545b0)
by tamaratran, at commit `e3f262a7f4d42bd8dd32ced30d26176f7cb545b0`. It is licensed under the MIT License; see
`LICENSE` in this folder. Only the files the plugin runs are included: the
demo, examples, tests, npm packaging, and bundled type declarations are left out.

Changes from upstream (AgentFleet patch level 0.3.0-agentfleet.2):

- `hooks/fast-jev.ts`: removed the `turn.complete` trigger and `compactAtPercent`
  option entirely. JEV now only answers compactions Claude Code runs itself:
  automatic at the AgentFleet window, `/compact`, or plugin-triggered.

- `hooks/fast-jev.ts`: main conversation only. In `session.compact`, subagents
  (`event.agentId`) and precompute triggers (`event.trigger === 'precompute'`) pass
  through directly to `next(event)`.

- `hooks/fast-jev.ts`: `getApiKey` reads only `options.apiKey` and `$.env.get('TYPESAFE_API_KEY')`.
  The `$.settings.read()` fallback is removed because AgentFleet rollback snapshots
  copy settings.json, so keys must not live there.

- `hooks/fast-jev.ts`: when no key is configured, logs and toasts a notice once per
  session ("fast-jev-compaction: no TypeSafe API key, so Claude Code's built-in summary is used. Set the key with /plugin or TYPESAFE_API_KEY.")
  and passes through; subsequent compactions pass through silently.

- `hooks/fast-jev.ts`: UI logs and notifications redact every occurrence of the
  resolved TypeSafe API key and strip control/format characters, including when
  remote response text appears in a compaction error.

- `.claude-plugin/plugin.json`: userConfig only declares `apiKey` (sensitive).
  Other options (`keepThreshold`, `preserveRecentMessages`, `minReductionRatio`,
  `maxStateTokens`, `maxRequestTokens`, `truncateHeadChars`, `model`) remain supported
  via `resolveHookConfig` with code defaults for advanced users.

Before you install it:

- It needs a TypeSafe API key (`TYPESAFE_API_KEY`, or the plugin's `apiKey`
  option), which is a paid third-party service.
- At each compaction it sends the conversation's text and tool inputs (tool
  results are replaced by short notes; about 25k tokens per request) to
  `https://api.typesafe.ai`.
- JEV only answers compactions initiated by Claude Code or `/compact` (main
  conversation only). Subagents and precomputes are skipped.

AgentFleet lists it in its plugin catalog but does not install it. To use it:

    claude plugin install fast-jev-compaction@agentfleet
