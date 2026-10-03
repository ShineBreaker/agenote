# pi-agenote: agenote integration for oh-my-pi (omp)

pi-agenote wires the agenote knowledge base into oh-my-pi (omp). It is a
single-file TypeScript extension that omp loads straight from source, so
edits take effect on the next session start with no build step.

## The problem it solves

Every omp session starts blank. The agent does not know which dependency
tripped you up last week, or which technical decisions you already made, so
it walks into the same trap twice. agenote stores that experience.
pi-agenote delivers it into each turn.

The second problem is the tail end. When a change is finished, the agent
does not write down how it got there on its own, and the trap is still
sitting there for whoever picks the work up next. pi-agenote prompts at the
moment a task looks finished, and the current agent does the capture.

Nobody using this has to remember a command or carry context between
sessions by hand.

## How it works

The memory brief is attached before every LLM call:

- The extension runs `agenote-cli context --mode session --host pi --budget
  8000` (8000 is the default budget, in characters), wraps the output in
  `<!-- agenote-inject:start -->` and `<!-- agenote-inject:end -->` tags, and
  writes it to the top of the first user message of the turn.
- Every write first strips all previously tagged blocks. If the host persists
  injected messages into session history, the blocks still do not pile up
  turn after turn.
- Briefs are cached against an mtime plus size fingerprint of `MEMORY.org`
  and the `memories/` tree on the KB side. An unchanged fingerprint replays
  the cache without spawning the CLI; only a KB change reruns it.
- An empty CLI result (no memory yet, or agenote's semantic switch is off)
  strips without writing, so the previous brief disappears on its own.
- A missing CLI, a failed call, or a run past 5 seconds appends one line to
  `.load-errors.log` and skips the round. The host session keeps going.

Experience capture fires on two triggers:

- Completion signal. On `agent_end` the extension reads the user's most
  recent message. A hit against the completion word list (搞定, 做完了, 测试通过,
  done., ship it, and similar), more than 5 minutes after the last trigger,
  injects a review prompt into the next turn. Self-injected prompts are
  excluded so the trigger cannot feed itself.
- Idle fallback. A session idle for 5 consecutive minutes that has never
  triggered injects the same prompt. It covers unattended overnight runs where
  nobody says "done". A new turn cancels the timer.

Judging and writing are left to the agent. The completion word list, the write
flow, and card formats are defined in agenote-skills; this extension only
triggers and registers commands, so the two never drift apart.
`/agenote-curate` works the same way. It injects a curation task prompt, and
the agent runs the curation through the agenote-curator skill. The extension
does not execute the curation itself.

Subagent processes take no part in any of this. A process carrying
`PI_BLOCKED_AGENT`, `--no-session`, or both `--mode json` and `-p` is treated
as a subagent, and every hook returns immediately.

## Who it is not for

- Anyone not using oh-my-pi. This is an omp extension and runs nowhere else.
- Anyone running a single agent with no subagents and no need for memory across
  sessions. Each turn costs one CLI query, and that is a local directory stat
  when the fingerprint is unchanged. It buys nothing back.
- Anyone already running a different memory-injection extension. Two
  extensions both writing to the top of the first user message stack their
  content.

## Deploy

agenote-pi lives at `packages/agenote-pi` in the
[agenote monorepo](https://github.com/ShineBreaker/agenote). The standalone
repo and the Guix-configs submodule flow are gone (see docs/adr/0005 in the
monorepo).

Deployment is managed by the Guix-configs dotfiles: `agenote.lock` pins a
release tag (`agenote-pi-v<version>`), and `sync-agenote.sh` fetches the
matching GitHub Release tarball, verifies its sha256, and symlinks the
extension into the omp extension directory. See the monorepo root AGENTS.md
§7 for the mechanism.

Without the dotfiles, download `agenote-pi-<version>.tar.gz` from the
matching GitHub Release and put `index.ts` under
`~/.config/omp/extensions/agenote-hooks/`; omp picks it up on the next
session start.

For development, clone the monorepo — omp loads the TypeScript source at
`packages/agenote-pi/index.ts` directly.

## Read more

- [Usage guide](docs/usage.md): hooks and commands, injection mechanism,
  switches, deployment, dependencies
- [中文 README](README.zh.md)
- [agenote main repo](https://github.com/ShineBreaker/agenote)
- [agenote-skills](https://github.com/ShineBreaker/agenote/tree/main/packages/agenote-skills):
  source of truth for the signal list and the write flow

## License

MIT, see [LICENSE](LICENSE).
