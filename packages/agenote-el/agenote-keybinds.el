;;; agenote-keybinds.el --- command keymap for agenote -*- lexical-binding: t; -*-

;; SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
;; SPDX-License-Identifier: MIT

;; This file is part of agenote-el.

;;; Commentary:

;; Defines `agenote-command-map', a bare keymap of all interactive
;; agenote commands.  This package does NOT bind any global prefix:
;; the host configuration is expected to attach `agenote-command-map'
;; to its preferred prefix (e.g. C-c o k) through its own key-binding
;; facility (which-key / help / dashboard integration).  Keeping the
;; global binding on the host side preserves the host's single source
;; of truth for key bindings.
;;
;; Hosts that prefer individual bindings can also ignore this map and
;; bind each command directly.

;;; Code:

(require 'agenote-knowledge)
(require 'agenote-health)

(defvar agenote-command-map
  (let ((map (make-sparse-keymap)))
    (define-key map "c" #'agenote-knowledge-capture)
    (define-key map "s" #'agenote-knowledge-search)
    (define-key map "t" #'agenote-knowledge-search-by-tag)
    (define-key map "I" #'agenote-knowledge-open-inbox)
    (define-key map "S" #'agenote-knowledge-stats)
    (define-key map "v" #'agenote-knowledge-browse-human)
    (define-key map "b" #'agenote-knowledge-browse-agenote)
    (define-key map "a" #'agenote-knowledge-archive-inbox-entry)
    (define-key map "d" #'agenote-knowledge-deduplicate)
    (define-key map "e" #'agenote-knowledge-merge)
    (define-key map "l" #'agenote-knowledge-lint)
    (define-key map "m" #'agenote-knowledge-memory)
    (define-key map "n" #'agenote-knowledge-connect)
    (define-key map "o" #'agenote-knowledge-commit)
    (define-key map "r" #'agenote-knowledge-review)
    (define-key map "u" #'agenote-knowledge-touch)
    (define-key map "V" #'agenote-knowledge-viz-open-browser)
    map)
  "Keymap for agenote commands.  Bind this to a prefix in the host config.")

(provide 'agenote-keybinds)
;;; agenote-keybinds.el ends here
