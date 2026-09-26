# agenote: cross-agent knowledge base

[![CI](https://github.com/ShineBreaker/agenote/actions/workflows/ci.yml/badge.svg)](https://github.com/ShineBreaker/agenote/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

> One memory, six agents sharing it.

Agents don't share memory. A pitfall you hit in Claude Code has to be hit again in
Codex, because every host stores its memory in a private format you can neither read nor
maintain. agenote collects those memories into one Org directory that both AI and humans
read and write as the same files.

## Why Org rather than Markdown

Most memory systems are built for the Markdown ecosystem: `.md` files, paragraphs for
body text, heading levels for structure, front matter for state. agenote takes the other
route, and every card is a standard org file.

This isn't a wrapper. Card structure uses org's native mechanisms directly:

- **Property drawers** hold structured metadata. `CATEGORY`, `TECH`, `STATUS`, `WEIGHT`,
  and `USAGE_COUNT` are drawer keys, not lines in the body.
- **TODO keywords** carry the state machine. The heading is always `* DONE`, with the
  real state in the `:STATUS:` property, and the CLI moves cards through four states
  (done, stable, stale, archived).
- **Sections and inline markers** keep org syntax. `** 场景` is an org subsection,
  `~orgfmt --check~` is an org code marker rather than a backtick.

You get three things a Markdown design can't offer:

**Humans can edit directly.** Open Emacs and `M-x org agenda` lists the cards by state;
`org-refile` moves them; `[[card][description]]` links are clickable. Maintaining the
knowledge base needs no agent and no separate interface.

**Zero learning cost for Emacs users.** If you already take notes in org mode, recording
an agent's lessons is just more entries in the same drawers.

**Readable Git diffs.** Which sentence changed in a card is visible at a glance in org.
Large Markdown files are mostly reflowing in a diff.

The cost is explicit: it assumes you have Emacs or at least an editor that understands
org syntax. Even without one, the files are plain text and nothing is lost, you just
lose agenda and refile.

## What it stores

Two kinds of content, with a clear boundary:

- **Experience cards** in `experiences/`. One reusable lesson each, with a title, category,
  tech stack, body, and status. This is the knowledge base's atom.
- **Memory entries** in `MEMORY.org`. Long-lived constraints that cut across cards, such as
  your preferences, a project's build commands, or gotchas specific to one machine. Lighter
  than cards, and injected into sessions first when you search.

Cards you write by hand and cards written by agents live in separate subdirectories and
don't contaminate each other. Search spans both domains by default, weighting what you
wrote higher.

## Core capabilities

**Write safety.** Mutating commands hold a flock process lock, writes go through tmp +
rename atomically, and same-second writes get an ID suffix appended. Several agents writing
to one knowledge base don't collide.

**Mixed Chinese-English search.** BM25 ranking with CJK 1/2/3-gram tokenization, so an
English command name embedded in a Chinese sentence still matches.

**Write gating.** Secret scanning is on by default and rejects key-shaped content. Sensitive
memory entries carry a marker and stay local, never injected into a session or projected to
a host.

**Trace back to the source.** Every experience links back to the original full conversation,
tool calls and reasoning included, rather than to a summary.

**A curation loop.** Health reports, dedup, status downgrade, archive, and weight recompute
are all atomic commands. The agent decides what to keep based on the spec; the CLI only
surfaces candidates and the evidence behind them.

**Visualization.** `agenote viz` renders the whole knowledge base into a single searchable
HTML file.

## Six hosts

zcode, Claude Code, Codex, oh-my-pi, opencode, and Hermes each have a plugin or injector
wired to `agenote context`. A brief is injected at session start, and task completion
triggers experience capture, with the exact behavior defined by shared skills. Each host's
built-in memory write side must be turned off by hand; `agenote doctor` checks them one by
one.

The knowledge base itself is a set of org files, and a Python CLI handles cards, search,
curation, and health. 37 subcommands, 460 tests, Python ≥ 3.10, no database.

## Versus built-in host memory

| Dimension | Built-in host memory | agenote |
| --- | --- | --- |
| Scope | Private to one host | Six hosts share one knowledge base |
| Storage | Per-host private formats (Markdown fragments, SQLite, JSONL) | Unified org files, human-readable |
| Write side | Each host writes independently | Host writes disabled; the CLI is the sole writer |
| Search | Local, per-host matching | Global BM25 with CJK n-gram |
| Curation | None | Health, dedup, downgrade, archive, weight recompute |
| Visualization | None | HTML visualization and an Emacs dashboard panel |

## Quick start

```bash
uv tool install git+https://github.com/ShineBreaker/agenote.git

agenote init                    # set up the knowledge base, default ~/Documents/Org

agenote add --title "concurrent write test" --category testing --tech Python
```

The full command set, configuration, and architecture are in the
[usage guide](docs/usage.md).

## Who shouldn't use this

- One person, one agent. Host built-in memory is enough.
- You already have a note system that works and don't intend to let AI read or write it.
- You need team sharing, multi-user permissions, or real-time collaboration. agenote is a
  single-machine file store versioned with Git.

## Ecosystem

- [agenote-skills](https://github.com/ShineBreaker/agenote-skills): three agent skills (base, curator, review)
- [pi-agenote](https://github.com/ShineBreaker/pi-agenote): the oh-my-pi integration extension
- [injectors/](injectors/README.md): memory injectors for the six hosts
- [agenote-el](https://github.com/ShineBreaker/agenote-el): the Emacs integration package

## Read more

- [Usage guide](docs/usage.md): installation, command reference, configuration, architecture, development
- [中文 README](README.zh.md)
- [Contributing](CONTRIBUTING.md) · [Changelog](CHANGELOG.md) · [Architecture decisions](docs/adr/)
- [Glossary](CONTEXT.md): what cards, memory entries, and reconcile actually mean

## License

MIT, see [LICENSE](LICENSE).
