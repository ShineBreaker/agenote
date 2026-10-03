# agenote-zcode

[中文文档](./README_CN.md)

[agenote](https://github.com/ShineBreaker/agenote) integration for ZCode, mirroring the pi-side `agenote-hooks` extension. Skills (`agenote-{base,curator,review}`) live in `~/.agents/skills/` and are shared across agents; this plugin only does **event triggering + command shortcuts** and never duplicates skill content.

## Components

| Component                               | Event / Invocation                                                       | What it does                                                                                                                                                                     |
| --------------------------------------- | ------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `hooks/session-start.mjs`               | `SessionStart`                                                           | Injects agenote usage rules (incl. the mandatory `AGENOTE_AGENT=zcode` attribution prefix) plus a KB health summary                                                              |
| `hooks/prompt-submit.mjs`               | `UserPromptSubmit`, regex pre-filter on completion words in `hooks.json` | Exact signal match + 5-min debounce (state under the plugin data dir), then injects an `agenote-review` evaluation prompt; self-injections carrying `<agenote-hook>` are skipped |
| `hooks/prompt-inject.mjs`               | `UserPromptSubmit`, no matcher (every turn)                              | Memory recall injection via `agenote context --mode recall`, with fingerprint / budget / cumulative caps suppressing redundant repeats                                           |
| `hooks/pre-tool-use.mjs`                | `PreToolUse` matcher `Bash`                                              | Reminds to prefix `AGENOTE_AGENT=zcode` when missing                                                                                                                             |
| `commands/{summarize,curate,health}.md` | `/agenote-zcode:summarize` etc.                                          | Slash-command shortcuts mirroring pi's `/agenote-*` commands                                                                                                                     |

Deliberately not ported from pi:

- **Idle fallback** — ZCode has no `agent_end`; the `Stop` hook's continuation request would interfere with normal session end.
- **MCP server** — agenote's primary path is the CLI; skills instruct bash invocation directly.
- **Subagent guard** — `UserPromptSubmit` only fires on user input in the main session.

Commit-trailer enforcement is a separate concern living in its own plugin (`assisted-by-zcode`, not shipped with this package).

## Install

Fetch the plugin from the monorepo first: unpack the `agenote-zcode-v*` tarball attached to its [GitHub Release](https://github.com/ShineBreaker/agenote/releases), or copy the [`packages/agenote-zcode`](https://github.com/ShineBreaker/agenote/tree/main/packages/agenote-zcode) directory from a checkout — e.g. to `~/.zcode/plugins/agenote-zcode`.

Then point `plugins.dirs` in `~/.zcode/cli/config.json` at this plugin root (each entry is one plugin directory containing `.zcode-plugin/plugin.json`; scanned entries are enabled by default):

```json
{
  "plugins": {
    "dirs": ["~/.zcode/plugins/agenote-zcode"]
  }
}
```

Then open a new session — plugin hooks are snapshotted at session start. Verify under **Settings → Plugin Management**.

## Maintenance notes

- The source of truth for completion signals is `spec/injection.toml` at the monorepo root. `COMPLETION_SIGNALS` and the budget constants in `hooks/prompt-submit.mjs` are **generated blocks** (emitted by `tools/codegen/generate.py`, committed so the plugin keeps working standalone); the `UserPromptSubmit` matcher regex in `hooks/hooks.json` and the prose list in `agenote-review/references/triggers.md` are verified against the spec by `tools/codegen/check.py`.
- The debounce timestamp is the only long-lived state: it goes to the plugin data directory ZCode injects, falling back to `~/.local/state/agenote-zcode/`.
