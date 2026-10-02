# agenote-skills: agent behavior specs

One behavior spec, shared by every agent.

## The problem

The same agent behaves differently depending on where it runs. Habits picked up in
pi have to be re-taught in crush, opencode, or hermes: look up past experience
before starting, reuse what is already known while working, write the lesson back
when the task ends. Each host has its own prompt, model, and toolchain, so a spec
written into one host's config only binds that host.

agenote-skills lifts that protocol out of host config into a standalone repo. Every
agent plugged in reads the same file and gets the same rules.

## The three skills

- `agenote-base` handles daily reads and writes. Before a task it runs
  `list`->`search`->`get` to check for known traps, reuses cards mid-task, then
  records with `add`->`touch` and `commit`s.
- `agenote-curator` maintains knowledge base health: diagnosis, dedup, archive,
  search weight recomputation, and memory reconcile across agents.
- `agenote-review` captures post-session experience. It spots signals worth
  recording, picks an ENTRY_TYPE, then decides whether to add a new card or touch
  an existing one.

Full responsibility table lives in the [usage guide](docs/usage.md).

## Core design

- Plain Markdown. Host frameworks scan the directory each session and load the
  spec, so editing the source takes effect immediately. Nothing to compile or
  deploy.
- Loaded on demand. Each `description` carries its trigger signals, so the body is
  pulled in only when the host matches one. Idle cost is three lines of
  description.
- One external dependency. The spec implements no storage of its own; every read
  and write goes through the `agenote` CLI on PATH.

## Who should not use this

- You run a single agent whose behavior is already stable. A prompt of your own
  does the job, and maintaining a shared spec is overhead.
- You do not plan to use agenote as the memory backend. Every operation in these
  skills is an `agenote` CLI call, so a different backend means rewriting them.

## Read more

- [Usage guide](docs/usage.md): skill spec, responsibility table, deployment, dependencies
- [中文 README](README.zh.md)
- [agenote main repo](https://github.com/ShineBreaker/agenote)

## License

MIT, see [LICENSE](LICENSE).
