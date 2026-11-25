# agenote-cindy: agenote adaptation for Cindy

Brings the agenote cross-agent knowledge base into Cindy. Each user message carries a memory briefing plus an on-demand recall block; when a task wraps up, the plugin nudges the agent to file what it learned. Two read-only query tools and one optional test-delivery diagnostic tool cover KB access and integration checks.

## Why not a Pi extension

Cindy launches its embedded Pi with `--no-extensions`, so the omp extension (`agenote-pi/index.ts`) is never loaded. This is a ghost plugin (`.cindy`), built on Cindy's own plugin protocol instead of Pi's extension points.

## How it works

| Hook / event | Behaviour |
|---|---|
| `will-compose-context` (2 s verdict window) | Attaches the briefing as host-side reference material on the wire message; the stored and displayed original is untouched |
| `did-message-created` | Listens for user messages to remember the last one for completion detection |
| `did-tool-execution` | Observes tool calls/results as evidence that a short turn did real work (cannot block or modify tools) |
| `did-turn-end` | Completion signal → posts an agenote-review prompt; long turns arm an idle fallback timer |
| `did-turn-start` | Cancels the idle timer |
| Tools `agenote_search` / `agenote_health` | Read-only CLI queries through the bundled Node worker |
| Tool `agenote_diag` | Reports events, delivery receipts, and CLI health; `send_test` delivers a test message only to an explicitly supplied session |

`main.js` runs in Cindy's browser sandbox (no Node, no filesystem, no direct network), so every CLI call happens in `node/worker.cjs` and always carries `AGENOTE_AGENT=cindy`.

The injection state machine (fingerprint cache, query dedupe, per-session cumulative budget) lives only in the CLI — the plugin calls `agenote inject` and keeps no second copy.

## Trade-offs

1. **The reference channel must be enabled per task, but it never touches your words.** `will-compose-context` is the only injection surface that is neither displayed nor persisted: it prepends a note to the wire message (`appendRuntimePluginContext`), and the stored original stays byte-identical. The cost is that the host only asks plugins selected in the task's "task plugins → context provider" menu. The entry hook `will-user-message` is not an option: Cindy renders user bubbles as plain `whitespace-pre-wrap` text (`UserMessage.tsx`), not Markdown, so any rewrite — including an HTML comment — shows up verbatim in the bubble. Without the opt-in the plugin still works: completion signals and the idle fallback are driven by the `message` / `turn` / `tool-execution` topics and do not depend on that selection; only the briefing and recall are skipped.
2. **The idle fallback prefers tool events and otherwise approximates "did real work" by turn duration** (45 s). Short turns with `tool_use` / `tool_result` events on the `tool-execution` topic arm it; without those events, duration is only a fallback approximation on older hosts.
3. **Slash commands are not bundled.** `/agenote-review`, `/agenote-curator`, etc. come from `agenote-skills` installed in the agent skill directory. Shipping a copy would create a second source of truth for the writing workflow.

## Install

Pack with `ghost_forge_pack`, then install with `ghost_forge_install` (both are Cindy host tools). Requirements:

- `agenote` on `PATH` (or `AGENOTE_BIN`). Versions with `inject` (≥ 0.2.4) get per-turn recall; older CLIs degrade to briefing-only and keep the tools working.
- Cindy ≥ 0.1.143 (see `ghost.json.minCindyVersion`; this is the first verified host version with `tool-execution`).

## Switches

| To disable | How |
|---|---|
| All injection | `agenote config set injection.enabled false` |
| This host only | `agenote config set injection.hosts.cindy_enabled false` |
| The whole plugin | Disable it on the Cindy plugin page |

## Permissions

`will-compose-context` only lets the plugin attach reference material; it does not read or rewrite your messages. `subscribe.topics: message` lets it observe stored message bodies for completion detection, and `tool-execution` observes bounded tool call/result summaries for short-turn work detection — observation only, but it does see those payloads. `agent.hostTools: handoff` is used for `send_to_session` nudges and the explicit `agenote_diag.send_test` delivery — it belongs to the **`handoff`** group, not `control`; a wrong group only yields a bare `PERMISSION_DENIED` (which is why every nudge before 0.3.2 died silently). `send_test` requires an explicit `target_session_id` to avoid sending a diagnostic message to another recent session. The bundled Node worker runs with your local account's permissions.

## Verified (2026-11, Cindy 0.1.143)

- `agenote context` cold start ≈ 0.22 s; the context point window is 2 s, per-call timeout is 1.2 s.
- `--host cindy` requires CLI ≥ 0.2.4; older CLIs reject it with `invalid choice` and the worker falls back to `generic`, remembering the choice.
- The manifest and `tool-execution` topic were checked against tag `v0.1.143` at commit `55d3b6ac45ea68c489122ed891148699fb55e477`.
- Host APIs checked against the same tag, including `appendRuntimePluginContext`, which only edits the wire message.
