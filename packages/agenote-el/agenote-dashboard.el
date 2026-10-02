;;; agenote-dashboard.el --- pure data helpers for dashboard integration -*- lexical-binding: t; -*-

;; SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
;; SPDX-License-Identifier: MIT

;; Version: 0.1.0
;; Package-Requires: ((emacs "29.1"))

;; This file is part of agenote-el.

;;; Commentary:

;; Pure, side-effect-free data helpers that a host dashboard can call
;; to render recent knowledge entries.  This file deliberately holds
;; NO cache, NO process state and NO UI registration: those concerns
;; belong to the host's dashboard framework (they depend on the host's
;; cache machinery, frame lifecycle and widget registry).  Splitting at
;; the pure-data boundary keeps this package reusable.
;;
;; A host dashboard typically:
;;   1. caches `(agenote-recent-knowledge-entries N)' on its own terms,
;;   2. refreshes the cache asynchronously with its own process pool,
;;   3. only calls these functions to turn JSON into display rows.

;;; Code:

(require 'agenote-knowledge)

;;;###autoload
(defun agenote-knowledge-entries-from-cards (cards)
  "Convert CARDS (a list of plists from `agenote list --json') into rows.
Each row is a (ID CATEGORY TITLE) triple."
  (cl-loop
   for card in cards
   for id = (plist-get card :id)
   for title = (or (plist-get card :title) "(无标题)")
   for category = (or (plist-get card :category) "unknown")
   when id
   collect (list id category title)))

;;;###autoload
(defun agenote-recent-knowledge-entries (max-items &optional domain)
  "Return the most recent knowledge entries as (ID CATEGORY TITLE) triples.
DOMAIN defaults to `agenote' (where agent-written cards live); the human
domain only holds hand-written cards and is often empty.  Consumes the
stable `agenote list --json' index with no caching.  Ordering and
filtering are decided by the CLI; Emacs performs no recursive file
scan.  Let callers layer their own TTL cache."  (agenote-knowledge-entries-from-cards
   (agenote-knowledge-list-cards (or domain 'agenote) max-items)))

(provide 'agenote-dashboard)
;;; agenote-dashboard.el ends here
