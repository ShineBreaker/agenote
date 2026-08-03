;;; agenote-knowledge.el --- agenote knowledge-base UI for Emacs -*- lexical-binding: t; -*-

;; SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
;; SPDX-License-Identifier: MIT

;; This file is part of agenote-el.  It provides the interactive
;; knowledge-base commands and the card browse mode that sit on top of
;; the `agenote' CLI adapter in `agenote.el'.

;;; Commentary:

;; All file-system operations are delegated to the agenote CLI; Emacs
;; only handles interactive calling and buffer refresh.  This avoids
;; index/scan logic drift between Emacs and the CLI.
;;
;;   Index list              -> `agenote list --json'
;;   inbox archive/slug/...  -> `agenote inbox-archive' subcommand
;;   Interactive call+refresh -> Emacs (agenote-knowledge-*)

;;; Code:

(require 'cl-lib)
(require 'json)
(require 'org)
(require 'agenote)


;;;; Directory lifecycle

(defun agenote--ensure-knowledge-directories ()
  "Ensure knowledge-base directories and index files exist (idempotent).
Category sub-directories under `experiences/' are created on demand at
capture time, not here.  A minimal skeleton initializes `inbox.org'
when absent; an existing file is left untouched."
  (let ((dirs (list (expand-file-name "experiences" agenote-org-directory))))
    (dolist (dir dirs)
      (unless (file-exists-p dir)
        (make-directory dir t))))
  (dolist (file (list agenote-inbox-file))
    (unless (file-exists-p file)
      (let ((base (file-name-base file)))
        (with-temp-buffer
          (insert (format "#+title: %s\n#+date: [%s]\n\n* Inbox\n"
                          base (format-time-string "%Y-%m-%d %a")))
          (write-region (point-min) (point-max) file))))))

(with-eval-after-load 'org-capture
  (add-hook 'org-capture-before-finalize-hook
            #'agenote--ensure-knowledge-directories))
(with-eval-after-load 'org
  (add-hook 'org-mode-hook
            (lambda ()
              (when (and buffer-file-name
                         (string-prefix-p agenote-org-directory buffer-file-name))
                (agenote--ensure-knowledge-directories)))))


;;;; Capture

;;;###autoload
(defun agenote-knowledge-capture ()
  "Capture an experience card via org-capture templates.
`\\[org-capture] nil \"k\"' enters the k prefix sub-menu
(note/mistake/ascended, aligned with agenote-base entry-types), which
the host's `org-capture-templates' maps onto a structured PROPERTIES +
body, landing in `experiences/<category>/<timestamp>.org'.  The
templates themselves are owned by the host configuration.

Does not pre-check `org-capture-templates': `org-capture' is autoloaded
and installs the templates on first call; pre-checking would reject a
fresh daemon where org-capture is not yet loaded."
  (interactive)
  (org-capture nil "k"))


;;;; Search and inbox

;;;###autoload
(defun agenote-knowledge-search (query)
  "Full-text search experiences for QUERY.
Uses `consult-ripgrep' when available, falling back to `rgrep'."
  (interactive "s检索关键词: ")
  (let ((dir (expand-file-name "experiences" agenote-org-directory)))
    (cond
     ((fboundp 'consult-ripgrep)
      (consult-ripgrep (list dir) query))
     ((fboundp 'rgrep)
      (rgrep query "*.org" (list dir)))
     (t
      (message "未找到可用的搜索工具")))))

;;;###autoload
(defun agenote-knowledge-search-by-tag (tag)
  "Search experiences by TAG.
Prefer the `agenote tags' command; fall back to ripgrep on :TAG:."
  (interactive "s标签: ")
  (if (agenote-resolve-executable)
      (let ((buf (get-buffer-create "*agenote-tags*")))
        (with-current-buffer buf
          (erase-buffer)
          (insert (agenote-call-string 'human "tags" tag)))
        (display-buffer buf))
    (agenote-knowledge-search (format ":%s:" tag))))

;;;###autoload
(defun agenote-knowledge-archive-inbox-entry (category)
  "Archive the inbox entry at point into `experiences/<CATEGORY>/'.
Slug algorithm, PROPERTIES normalization and reindex are all handled
by the `inbox-archive' subcommand; Emacs only collects heading + body
and calls the CLI."
  (interactive
   (list (completing-read "归档分类: "
                          (or (condition-case nil
                                  (split-string
                                   (agenote-call-string 'human "fields" "--category")
                                   "\n" t "[ \t]+")
                                (error nil))
                              '("emacs" "guix" "general" "gamedev" "devops" "research" "tooling"))
                          nil t)))
  (let* ((heading (or (org-get-heading t t t t) ""))
         (full-text (progn (org-copy-subtree) (car kill-ring)))
         (body (if (and full-text (string-match "^\\*+ [^\n]+\n:PROPERTIES:\n\\(?:[^\n]*\n\\)*?:END:\n"
                                                full-text))
                   (substring full-text (match-end 0))
                 (or full-text "")))
         (stdin (json-encode `[(("heading" . ,heading) ("body" . ,body))]))
         (result (agenote-call 'human "inbox-archive"
                               "--category" category "--stdin" "--prune")))
    (if (eq (plist-get result :status) 0)
        (progn
          (setq kill-ring (cdr kill-ring))
          (when (buffer-live-p (get-buffer agenote-inbox-file))
            (with-current-buffer (get-buffer agenote-inbox-file)
              (revert-buffer t t t)))
          (message "已归档: %s" (string-trim (plist-get result :stdout))))
      (message "归档失败: %s" (plist-get result :stderr)))))


;;;; Card lifecycle CLI wrappers

(defun agenote-knowledge--current-id ()
  "Return the :ID: property of the current buffer, or nil.
A buffer that is not an org-mode experience card, or has no :ID:,
returns nil."
  (when (and (eq major-mode 'org-mode)
             (buffer-file-name))
    (org-with-wide-buffer
     (goto-char (point-min))
     (when (re-search-forward "^:ID:\\s-+\\([0-9]\\{8\\}-[0-9]\\{6\\}\\)" nil t)
       (match-string 1)))))

(defun agenote-knowledge--ensure-experience-buffer ()
  "Ensure the current buffer is a human-domain experience card; return its :ID:.
Otherwise message the reason and return nil.  Human-domain = buffer
file lives under `experiences/' (not the agenote sub-tree)."
  (let ((id (agenote-knowledge--current-id))
        (file (buffer-file-name))
        (exp-dir (expand-file-name agenote-experiences-directory))
        (agenote-dir (expand-file-name "agenote" agenote-org-directory)))
    (cond
     ((null file)
      (message "当前 buffer 无关联文件") nil)
     ((string-prefix-p agenote-dir (expand-file-name file))
      (message "当前卡片在 agenote 子域；人类域操作请打开 experiences/ 下的卡片") nil)
     ((not (string-prefix-p exp-dir (expand-file-name file)))
      (message "当前文件不在 experiences/ 下") nil)
     ((null id)
      (message "当前卡片无 :ID: 属性") nil)
     (t id))))

;;;###autoload
(defun agenote-knowledge-stats ()
  "Show human-domain knowledge-base stats (calls `agenote --domain human stats')."
  (interactive)
  (if (agenote-resolve-executable)
      (let ((buf (get-buffer-create "*agenote-stats*")))
        (with-current-buffer buf
          (erase-buffer)
          (insert (agenote-call-string 'human "stats"))
          (goto-char (point-min)))
        (display-buffer buf))
    (message "未找到 agenote 命令")))

;;;###autoload
(defun agenote-knowledge-open-file (file)
  "Open knowledge-base FILE."
  (interactive "f知识库文件: ")
  (find-file (expand-file-name file)))

;;;###autoload
(defun agenote-knowledge-open-inbox ()
  "Open the knowledge-base inbox."
  (interactive)
  (agenote-knowledge-open-file agenote-inbox-file))

;;;###autoload
(defun agenote-knowledge-touch (&optional used-only)
  "Update LAST_USED + LAST_VERIFIED on the current card (human domain).
With prefix arg USED-ONLY non-nil, update LAST_USED only."
  (interactive "P")
  (let ((id (agenote-knowledge--ensure-experience-buffer)))
    (when id
      (if (agenote-resolve-executable)
          (let ((result (apply #'agenote-call
                               'human "touch" id
                               (when used-only '("--used-only")))))
            (if (zerop (plist-get result :status))
                (message "已 touch: %s%s" id (if used-only " (仅 USED)" ""))
              (message "touch 失败: %s" (plist-get result :stderr))))
        (message "未找到 agenote 命令")))))

;;;###autoload
(defun agenote-knowledge-archive (reason)
  "Archive the current experience card (human domain).
The card is moved to `archived/'; restore it with
`agenote-knowledge-restore'."
  (interactive "s归档原因: ")
  (let ((id (agenote-knowledge--ensure-experience-buffer)))
    (when id
      (if (agenote-resolve-executable)
          (let* ((result (agenote-call 'human "archive" id "--reason" reason)))
            (if (zerop (plist-get result :status))
                (message "已归档: %s (%s)" id reason)
              (message "归档失败: %s" (plist-get result :stderr))))
        (message "未找到 agenote 命令")))))

;;;###autoload
(defun agenote-knowledge-restore (id)
  "Restore an archived experience card (human domain).
ID is read via completing-read from the archive list if available,
otherwise entered manually."
  (interactive
   (list (read-string "恢复的卡片 ID: "
                      (when-let* ((cur (agenote-knowledge--current-id)))
                        cur))))
  (if (agenote-resolve-executable)
      (let ((result (agenote-call 'human "restore" id)))
        (if (zerop (plist-get result :status))
            (message "已恢复: %s" id)
          (message "恢复失败: %s" (plist-get result :stderr))))
    (message "未找到 agenote 命令")))

;;;###autoload
(defun agenote-knowledge-review ()
  "Review the current experience card (human domain), output to a dedicated buffer."
  (interactive)
  (let ((id (agenote-knowledge--ensure-experience-buffer)))
    (when id
      (if (agenote-resolve-executable)
          (let ((buf (get-buffer-create "*agenote-review*"))
                (result (agenote-call 'human "review" id)))
            (with-current-buffer buf
              (erase-buffer)
              (insert (plist-get result :stdout))
              (goto-char (point-min)))
            (display-buffer buf))
        (message "未找到 agenote 命令")))))

;;;###autoload
(defun agenote-knowledge-memory (arg)
  "Human-domain memory system overview or operation.
No prefix: call `agenote --domain human memory' and show the overview.
Prefix ARG non-nil: read --flag arguments (free text, passed through)."
  (interactive "P")
  (if (agenote-resolve-executable)
      (let* ((extra-args (if arg
                             (let ((input (read-string
                                           "memory 参数 (如 --stale, --type feedback, 留空显示概览): ")))
                               (split-string-and-unquote input))
                           nil))
             (result (apply #'agenote-call 'human "memory" extra-args))
             (buf (get-buffer-create "*agenote-memory*")))
        (with-current-buffer buf
          (erase-buffer)
          (insert (plist-get result :stdout))
          (goto-char (point-min)))
        (display-buffer buf))
    (message "未找到 agenote 命令")))

;;;###autoload
(defun agenote-knowledge-connect (id-a id-b &optional desc)
  "Bi-directionally link two cards (human domain).
ID-A defaults to the current buffer's :ID:, ID-B and DESC are collected
in the minibuffer."
  (interactive
   (let* ((a (or (agenote-knowledge--current-id)
                 (read-string "主卡片 ID: ")))
          (b (read-string "链接到卡片 ID: "))
          (d (read-string "描述 (可选): ")))
     (list a b d)))
  (if (agenote-resolve-executable)
      (let* ((extra-args (delq nil (list id-a id-b
                                         (unless (string-empty-p desc) "--desc")
                                         (unless (string-empty-p desc) desc))))
             (result (apply #'agenote-call 'human "connect" extra-args)))
        (if (zerop (plist-get result :status))
            (message "已链接: %s ↔ %s" id-a id-b)
          (message "链接失败: %s" (plist-get result :stderr))))
    (message "未找到 agenote 命令")))

;;;###autoload
(defun agenote-knowledge-merge (primary-id secondary-ids reason)
  "Merge cards: fold SECONDARY-IDS into PRIMARY-ID (human domain).
SECONDARY-IDS is a space-separated list of IDs; REASON records why."
  (interactive
   (let* ((p (or (agenote-knowledge--current-id)
                 (read-string "主卡片 ID（保留）: ")))
          (s (read-string "次卡片 ID（合并后删除，多个用空格）: "))
          (r (read-string "合并原因: ")))
     (list p (split-string s) r)))
  (if (agenote-resolve-executable)
      (let* ((extra-args (append (list primary-id) secondary-ids
                                 (unless (string-empty-p reason)
                                   (list "--reason" reason))))
             (result (apply #'agenote-call 'human "merge" extra-args)))
        (if (zerop (plist-get result :status))
            (message "已合并 %d 张到 %s" (length secondary-ids) primary-id)
          (message "合并失败: %s" (plist-get result :stderr))))
    (message "未找到 agenote 命令")))

;;;###autoload
(defun agenote-knowledge-deduplicate ()
  "Detect duplicate cards (human domain, async)."
  (interactive)
  (if (agenote-resolve-executable)
      (agenote-call-async 'human "deduplicate")
    (message "未找到 agenote 命令")))

;;;###autoload
(defun agenote-knowledge-lint (arg)
  "Lint all experience cards (human domain, async).
No prefix: check only and show the report.
Prefix ARG non-nil: call `agenote lint --fix' to auto-fix."
  (interactive "P")
  (if (agenote-resolve-executable)
      (let ((args (if arg '("--fix") '("--check"))))
        (apply #'agenote-call-async 'human "lint" args)
        (when arg (message "正在自动修复格式问题...")))
    (message "未找到 agenote 命令")))

;;;###autoload
(defun agenote-knowledge-commit (summary)
  "Commit knowledge-base changes to git (human domain, async).
`index.json' is git-ignored; only cards and MEMORY changes are committed."
  (interactive "s提交总结: ")
  (if (agenote-resolve-executable)
      (progn
        (agenote-call-async 'human "commit" "-m" summary)
        (message "正在提交: %s" summary))
    (message "未找到 agenote 命令")))

(defun agenote-knowledge--setup-agenda ()
  "Add the experience directory to `org-agenda-custom-commands'."
  (add-to-list
   'org-agenda-custom-commands
   '("K" "知识库回顾"
     ((tags "difficulties|lessons"
            ((org-agenda-files (list (expand-file-name "experiences" agenote-org-directory)))
             (org-agenda-sorting-strategy '(timestamp-down))))))
   t))

(with-eval-after-load 'org-agenda
  (agenote-knowledge--setup-agenda))


;;;; Unified browse mode

(defconst agenote-knowledge-browse-buffer-name "*knowledge-browse*")

;;;###autoload
(defun agenote-knowledge-list-cards (domain &optional recent)
  "Return the card list for DOMAIN via the agenote CLI.
When RECENT is non-nil, request only the most recent RECENT entries;
ordering and filtering are entirely decided by the CLI."
  (let* ((args (append (when recent
                         (list "--recent" (number-to-string recent)))
                       (list "--json")))
         (result (apply #'agenote-call domain "list" args))
         (status (plist-get result :status))
         (output (plist-get result :stdout)))
    (unless (eq status 0)
      (error "agenote list 失败: %s" (plist-get result :stderr)))
    (let ((json-object-type 'plist)
          (json-array-type 'list)
          (json-key-type 'keyword))
      (if (string-empty-p output)
          nil
        (json-read-from-string output)))))

(defvar-local agenote--knowledge-card-domain nil)
(defvar-local agenote--knowledge-card-id nil)

(defun agenote-knowledge-card-refresh (&rest _)
  "Refresh the current card detail via the CLI."
  (let* ((result
          (agenote-call
           agenote--knowledge-card-domain "get" agenote--knowledge-card-id))
         (inhibit-read-only t))
    (erase-buffer)
    (insert (if (eq (plist-get result :status) 0)
                (plist-get result :stdout)
              (format "agenote get 失败:\n%s" (plist-get result :stderr))))
    (goto-char (point-min))))

;;;###autoload
(defun agenote-knowledge-show-card (domain id)
  "Show the card with ID in DOMAIN in a read-only Org buffer."
  (let ((buffer (get-buffer-create "*knowledge-card*")))
    (with-current-buffer buffer
      (org-mode)
      (setq-local agenote--knowledge-card-domain domain
                  agenote--knowledge-card-id id
                  revert-buffer-function #'agenote-knowledge-card-refresh)
      (agenote-knowledge-card-refresh)
      (view-mode 1)
      (setq header-line-format
            (format " [%s] %s · g 刷新 · q 关闭" domain id)))
    (pop-to-buffer buffer)))

(defun agenote-knowledge-browse--format-line (domain card)
  "Format CARD of DOMAIN as a clickable line."
  (let* ((id (or (plist-get card :id) ""))
         (title (or (plist-get card :title) "(无标题)"))
         (category (or (plist-get card :category) "unknown"))
         (created (or (plist-get card :created) ""))
         (line (format "%-12s  %-10s  %s" category created title))
         (map (make-sparse-keymap)))
    (define-key map (kbd "RET")
                (lambda ()
                  (interactive)
                  (agenote-knowledge-show-card domain id)))
    (define-key map [mouse-1]
                (lambda ()
                  (interactive)
                  (agenote-knowledge-show-card domain id)))
    (add-text-properties
     0 (length line)
     (list 'agenote-knowledge-id id
           'keymap map
           'mouse-face 'highlight
           'help-echo (format "查看 %s" id)
           'follow-link t)
     line)
    line))

(defvar-local agenote--knowledge-browse-domain 'human)
(defvar-local agenote--knowledge-browse-cards nil)

(defun agenote-knowledge-browse-refresh (&rest _)
  "Reload the current domain from the CLI and redraw the list."
  (interactive)
  (setq agenote--knowledge-browse-cards
        (agenote-knowledge-list-cards agenote--knowledge-browse-domain))
  (let ((inhibit-read-only t))
    (erase-buffer)
    (insert (propertize
             (format "%s · %d 张\n\n"
                     agenote--knowledge-browse-domain
                     (length agenote--knowledge-browse-cards))
             'face 'bold))
    (dolist (card agenote--knowledge-browse-cards)
      (insert (agenote-knowledge-browse--format-line
               agenote--knowledge-browse-domain card)
              "\n"))
    (goto-char (point-min))))

;;;###autoload
(defun agenote-knowledge-browse-open-browser ()
  "Open the web visualization of the current domain via `agenote viz'."
  (interactive)
  (agenote-call-async agenote--knowledge-browse-domain "viz" "--open"))

(defvar agenote-knowledge-browse-mode-map
  (let ((map (make-sparse-keymap)))
    (set-keymap-parent map special-mode-map)
    (define-key map "g" #'agenote-knowledge-browse-refresh)
    (define-key map "o" #'agenote-knowledge-browse-open-browser)
    (define-key map "q" #'quit-window)
    map))

;;;###autoload
(define-derived-mode agenote-knowledge-browse-mode special-mode "KB-Browse"
  "CLI-driven knowledge-base list view.
\\{agenote-knowledge-browse-mode-map}"
  (setq-local revert-buffer-function #'agenote-knowledge-browse-refresh))

(defun agenote-knowledge-browse--open (domain)
  "Open the unified knowledge-base list for DOMAIN."
  (let ((buffer (get-buffer-create agenote-knowledge-browse-buffer-name)))
    (with-current-buffer buffer
      (agenote-knowledge-browse-mode)
      (setq agenote--knowledge-browse-domain domain)
      (agenote-knowledge-browse-refresh))
    (pop-to-buffer buffer)))

;;;###autoload
(defun agenote-knowledge-browse-human ()
  "Open the human-domain knowledge-base list."
  (interactive)
  (agenote-knowledge-browse--open 'human))

;;;###autoload
(defun agenote-knowledge-browse-agenote ()
  "Open the agenote-domain knowledge-base list."
  (interactive)
  (agenote-knowledge-browse--open 'agenote))

;;;###autoload
(defun agenote-knowledge-viz-open-browser ()
  "Open the human-domain knowledge-base visualization in a browser."
  (interactive)
  (agenote-call-async 'human "viz" "--open"))

(provide 'agenote-knowledge)
;;; agenote-knowledge.el ends here
