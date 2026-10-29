# Sidebar reply timing

Prefer the agent's protocol data. Do not introduce a separate agent runtime or
infer work duration from tool activity or token counts.

## Codex

`CodexDriver` reads `turn.startedAt` (Unix seconds) from `turn/start` responses
and `turn/started` notifications into the existing live reply start field.
The frontend only formats the elapsed time; it does not replace that timestamp.
The existing local start is a fallback for absent/null fields and older servers.

Verified with the installed CLI's generated JSON schema and an ephemeral real
read-only turn: the initial response had null timing, `turn/started` supplied a
timestamp, and `turn/completed` supplied the same timestamp and `durationMs`.
This is why listening only to the start response is insufficient.
Completion duration is not currently displayed, so no separate duration state
or new UI was added.

## Other providers

The Claude stream adapter, Pi RPC adapter and ACP adapters keep their existing
local live timer fallback. A message timestamp or a completed request's duration
is not a live start timestamp for the whole reply. Do not treat those values as
interchangeable. No equivalent whole-reply live start field has been established
for these integrations; no new provider-specific clock is implemented for them.

## References

- [Codex app-server](https://learn.chatgpt.com/docs/app-server)
- Installed CLI schema: `.cache/codex-protocol-timing/v2/TurnStartedNotification.json`
- [Claude Agent SDK](https://code.claude.com/docs/en/agent-sdk)
- [Pi JSON/RPC event definitions](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/json.md)

Automated regressions cover provider start notifications, absent/invalid values,
subagent isolation, consecutive replies and queue waits. These protocol/UI tests
use simulated agents; the ephemeral Codex check above used the real provider.
