# agenote-cindy: agenote adaptation for Cindy

Brings the agenote cross-agent knowledge base into Cindy. Each user message carries a
memory briefing plus an on-demand recall block; when a task wraps up, the plugin nudges
the agent to file what it learned. Two read-only tools let the agent query the KB directly.

## Why not a Pi extension

Cindy launches its embedded Pi with `--no-extensions`, so the omp extension
(`agenote-pi/index.ts`) is never loaded. This is a ghost plugin (`.cindy`), built on
Cindy's own plugin protocol instead of Pi's extension points.

## How it works

| Hook / event | Behaviour |
|---|---|
| `will-user-message` (3 s verdict window) | Prepends a `<!-- agenote-inject:… -->` block by rewriting the incoming message |
| `did-turn-end` | Completion signal → posts an agenote-review prompt; long turns arm an idle fallback timer |
| `did-turn-start` | Cancels the idle timer |
| Tools `agenote_search` / `agenote_health` | Read-only CLI queries through the bundled Node worker |

`main.js` runs in Cindy's browser sandbox (no Node, no filesystem, no direct network), so
every CLI call happens in `node/worker.cjs` and always carries `AGENOTE_AGENT=cindy`.

The injection state machine (fingerprint cache, query dedupe, per-session cumulative
budget) lives only in the CLI — the plugin calls `agenote inject` and keeps no second copy.

## Trade-offs

1. **Injection is append-style and appears in your message bubble.** Cindy's entry hook can
   only rewrite the current message, not history, so old blocks cannot be stripped. Dedupe
   and the cumulative budget come from the CLI. If you prefer a clean bubble, switch the
   manifest hook to `will-compose-context` — at the cost of selecting the plugin per task.
2. **The idle fallback approximates "did real work" by turn duration** (45 s), because
   Cindy's subscription surface exposes no tool-call events.
3. **Slash commands are not bundled.** `/agenote-review`, `/agenote-curator`, etc. come from
   `agenote-skills` installed in the agent skill directory. Shipping a copy would create a
   second source of truth for the writing workflow.

## Install

Pack with `ghost_forge_pack`, then install with `ghost_forge_install` (both are Cindy host
tools). Requirements:

- `agenote` on `PATH` (or `AGENOTE_BIN`). Versions with `inject` (≥ 0.2.4) get per-turn
  recall; older CLIs degrade to briefing-only and keep the tools working.
- Cindy ≥ 0.1.139 (see `ghost.json.minCindyVersion`).

## Switches

| To disable | How |
|---|---|
| All injection | `agenote config set injection.enabled false` |
| This host only | `agenote config set injection.hosts.cindy_enabled false` |
| The whole plugin | Disable it on the Cindy plugin page |

## Permissions

`will-user-message` is the heaviest permission (read/intercept/rewrite everything you
send). The plugin only decides whether to prepend a block; your original text follows it
unchanged. `agent.hostTools: control` is used solely for `send_to_session` nudges. The
bundled Node worker runs with your local account's permissions.

## Verified (2026-11, Cindy 0.1.139)

- `agenote context` cold start ≈ 0.22 s; the hook window is 3 s, per-call timeout is 1.2 s.
- `--host cindy` requires CLI ≥ 0.2.4; older CLIs reject it with `invalid choice` and the
  worker falls back to `generic`, remembering the choice.
- Host APIs checked against tag `v0.1.139` (commit `9ffef649`).
