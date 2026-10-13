# Upstream

This mod is a copy of `token-weather` from Anthropic's
[claude-code-playground](https://github.com/anthropics/claude-code-playground/tree/e9ab132d4575390ecbadfc54649712432b1a3351/claude-code/mods/token-weather)
repository, at commit `e9ab132d4575390ecbadfc54649712432b1a3351`. Anthropic shares it as-is, without support.

It is licensed under the Apache License 2.0; see `LICENSE` in this folder.

Changes from upstream:

- `README.md`: the screenshot links point to the upstream repository, because
  AgentFleet does not ship the screenshots.
- `hooks/token-weather.mjs`: the AbovePrompt hook always calls `next(e)` and
  draws its forecast after what the other mods drew, instead of replacing the
  band; it reads `hasSurvey` and `bodyColumns` from `e.props`, where Claude
  Code 2.1.296 puts them. The band logic is the exported `withBand` so tests
  can reach it.
- `tests/band.test.ts` (new): tests for the band.
- `.claude-plugin/plugin.json`: version `0.1.0-agentfleet.1`.

Since AgentFleet 2.5.0 it is installed and on by default. Turn it off with
`/plugin`, or with `agentfleet configure`.
