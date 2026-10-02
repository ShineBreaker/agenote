# agenote-el: the Emacs front end for agenote

agenote-el wires the knowledge base operations of the
[agenote](https://github.com/ShineBreaker/agenote) CLI into Emacs, so you can
capture, search, review and curate experience cards from a buffer. You and the
AI read the same org files.

## What problem it solves

agenote turns experience into a knowledge base: one org file per card, written
by hand or through org-capture, and AI agents can write into the same tree. The
friction is that these operations otherwise only happen in a terminal, so
Emacs users switch back and forth for every card.

agenote-el turns the terminal commands into interactive commands. A card is an
ordinary org file, capture goes through org-capture, search goes through
consult-ripgrep or rgrep, and opening a card is `find-file`. There is no second
data format to keep in sync.

## What it does

- **Knowledge base overview.** `M-x agenote-knowledge-browse` lists cards grouped
  by the human and agenote domains, with status, author, last use, count,
  category and title on each row. Press `/` to filter on title, category, status
  or author, and `RET` on a group header to expand it from 20 cards to all of
  them.
- **Health panel.** `M-x agenote-health` renders the CLI health report as a
  panel; `g` refetches it.
- **Card lifecycle.** Capture, archive, commit, merge, deduplicate, connect two
  cards, and update timestamps each have a command. The slow ones run as async
  processes and write to their own buffer, so editing is never blocked.
- **Web visualization.** Hand a domain's card graph to the browser.

One rule holds the implementation together: every CLI call goes through
`agenote-call` or `agenote-call-async`. The Emacs side duplicates no index or
scan logic and leaves ordering and filtering to the CLI. Each call re-resolves
`executable-find "agenote"` instead of caching an absolute path, so a long-lived
daemon picks up the new CLI after a Guix profile switch.

`agenote-dashboard.el` adds a few pure functions that a host dashboard calls to
get recent entries, keeping its own cache and refresh.

## Who it is not for

- People who do not use Emacs. The whole value is inside Emacs; terminal users
  should call the CLI directly.
- Knowledge bases not managed by the agenote CLI. The elisp side never scans the
  org directory on its own.
- Anyone after a knowledge base tool with its own database and full-text index.
  What agenote stores is org files plus one CLI-maintained index.

## Quick start

```elisp
(use-package agenote
  :load-path "/path/to/agenote-el"
  :custom
  (agenote-org-directory "~/Documents/Org"))
```

Needs Emacs 29.1 or later, with no third-party elisp dependencies. For
installation steps, the keybinding table, the full browse mode reference and
every configuration option, see the [usage guide](docs/usage.md).

## Read more

- [Usage guide](docs/usage.md)
- [中文 README](README.zh.md)
- [agenote main repo](https://github.com/ShineBreaker/agenote)

## License

MIT, see [LICENSE](LICENSE).
