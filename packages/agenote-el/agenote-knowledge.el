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
(require 'tabulated-list)
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
(defun agenote-knowledge-list-cards (domain &optional recent all)
  "Return the card list for DOMAIN via the agenote CLI.
When RECENT is non-nil, request only the most recent RECENT entries.
When ALL is non-nil, request every card (the CLI caps a plain list at
its own default of 20 entries).  Ordering and filtering are entirely
decided by the CLI."
  (let* ((args (append (cond
                        (all (list "--all"))
                        (recent (list "--recent" (number-to-string recent))))
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

(defvar-local agenote--knowledge-browse-cards nil
  "Alist ((DOMAIN . CARDS) ...) cached from the CLI, newest first.
Refresh always fetches every card; per-group display limits trim later
so the substring filter stays accurate over the full domain.")

(defvar-local agenote--knowledge-browse-limits nil
  "Alist ((DOMAIN . LIMIT) ...) controlling group visibility.
LIMIT is a number (show the first N cards of the group), `all' (show
every card) or 0 (hide the group entirely).")
(defvar-local agenote--knowledge-browse-filter nil
  "Case-folded substring filter over title/category/status/author, or nil.")

(defconst agenote-knowledge-browse--default-limits
  '((human . 20) (agenote . 20))
  "Default view: head 20 cards of each domain, as separate groups.")

(defun agenote-knowledge-browse--root (domain)
  "Return the knowledge-base root directory for DOMAIN."
  (if (eq domain 'human)
      agenote-org-directory
    agenote-subdomain-directory))

(defun agenote-knowledge-browse--str (s fallback)
  "Return S unless it is nil or empty, else FALLBACK."
  (if (and (stringp s) (not (string-empty-p s))) s fallback))

(defun agenote-knowledge-browse--date (ts)
  "Extract the YYYY-MM-DD part of an Org timestamp TS (\"[2026-08-27 …\")."
  (if (and (stringp ts) (>= (length ts) 11))
      (substring ts 1 11)
    "-"))

(defun agenote-knowledge-browse--status-face (status)
  "Face for a card STATUS value."
  (pcase status
    ("stable" 'success)
    ("stale" 'warning)
    ("archived" 'shadow)
    (_ 'default)))

(defun agenote-knowledge-browse--match-p (card)
  "Return non-nil when CARD passes `agenote--knowledge-browse-filter'."
  (let ((needle agenote--knowledge-browse-filter))
    (or (null needle)
        (string-search
         needle
         (downcase
          (string-join
           (mapcar (lambda (k) (or (plist-get card k) ""))
                   '(:title :category :status :source_agent :owner))
           " "))))))

(defun agenote-knowledge-browse--entries ()
  "Build `tabulated-list-entries': one header row per visible group.
A card entry's id is its absolute file path (RET visits the file); a
group header's id is (group . DOMAIN) (RET toggles expansion).  The
header text lands in the flexible Title column so it never stretches
the fixed-width columns."
  (let (entries)
    (dolist (spec agenote--knowledge-browse-limits)
      (let* ((domain (car spec))
             (limit (cdr spec))
             (cards (cdr (assq domain agenote--knowledge-browse-cards)))
             (hits (seq-filter #'agenote-knowledge-browse--match-p cards))
             (shown (if (eq limit 'all) hits (seq-take hits limit))))
        (unless (eq limit 0)
          (let ((label
                 (propertize
                  (format "── %s · 显示 %d/%d 张%s"
                          domain (length shown) (length hits)
                          (if (eq limit 'all) "（RET 收起）" "（RET 展开全部）"))
                  'face 'bold)))
            (push (list (cons 'group domain)
                        (vector "" "" "" "" "" label))
                  entries))
          (dolist (card shown)
            (let ((status (agenote-knowledge-browse--str
                           (plist-get card :status) "done"))
                  (file (plist-get card :file)))
              ;; file 缺失 = CLI 版本过旧（list 无该字段）：跳过并提示，
              ;; 避免 expand-file-name 对 nil 报错毁掉整个界面
              (when file
                (push (list
                       (expand-file-name
                        file (agenote-knowledge-browse--root domain))
                       (vector
                        (propertize status
                                    'face (agenote-knowledge-browse--status-face status))
                        (agenote-knowledge-browse--str
                         (plist-get card :source_agent)
                         (agenote-knowledge-browse--str (plist-get card :owner) "-"))
                        (agenote-knowledge-browse--date (plist-get card :last_used))
                        (number-to-string (or (plist-get card :usage_count) 0))
                        (agenote-knowledge-browse--str (plist-get card :category) "?")
                        (agenote-knowledge-browse--str (plist-get card :title) "(无标题)")))
                      entries)))))))
    (nreverse entries)))

(defun agenote-knowledge-browse--modeline-name ()
  "Return the mode-name showing per-domain totals and the active filter.
The header-line is left to `tabulated-list-init-header' (column names)."
  (let ((counts
         (mapconcat
          (lambda (spec)
            (format "%s:%d"
                    (car spec)
                    (length (cdr (assq (car spec)
                                        agenote--knowledge-browse-cards)))))
          (seq-filter (lambda (s) (not (eq 0 (cdr s))))
                      agenote--knowledge-browse-limits)
          " ")))
    (if agenote--knowledge-browse-filter
        (format "KB[%s|%s]" counts agenote--knowledge-browse-filter)
      (format "KB[%s]" counts))))

(defun agenote-knowledge-browse-redraw ()
  "Rebuild entries from cached cards and redraw; no CLI access."
  (setq tabulated-list-entries (agenote-knowledge-browse--entries))
  (tabulated-list-print)
  (setq mode-name (agenote-knowledge-browse--modeline-name)))

(defun agenote-knowledge-browse-refresh (&rest _)
  "Reload every visible domain from the CLI and redraw.
A CLI failure degrades that domain to an empty group plus a message
instead of signalling, so the buffer stays usable."
  (interactive)
  (setq agenote--knowledge-browse-cards
        (mapcar
         (lambda (spec)
           (cons (car spec)
                 (condition-case err
                     (agenote-knowledge-list-cards (car spec) nil t)
                   (error
                    (message "%s" (error-message-string err))
                    nil))))
         agenote--knowledge-browse-limits))
  (when (seq-some (lambda (pair)
                    (seq-some (lambda (c) (not (plist-get c :file)))
                              (cdr pair)))
                  agenote--knowledge-browse-cards)
    (message "agenote CLI 的 list 输出缺 file 字段（版本过旧），受影响卡片暂不在总览显示；请升级 agenote"))
  (agenote-knowledge-browse-redraw))

(defun agenote-knowledge-browse--domain-at-point ()
  "Domain of the group header or card row at point (default `agenote')."
  (let ((id (tabulated-list-get-id)))
    (cond
     ((and (consp id) (eq (car id) 'group)) (cdr id))
     ((stringp id)
      (if (string-prefix-p
           (file-name-as-directory agenote-subdomain-directory) id)
          'agenote 'human))
     (t 'agenote))))

(defun agenote-knowledge-browse-ret ()
  "On a group header, toggle that group between head-20 and all.
On a card row, visit the card file."
  (interactive)
  (let ((id (tabulated-list-get-id)))
    (cond
     ((and (consp id) (eq (car id) 'group))
      (let ((spec (assq (cdr id) agenote--knowledge-browse-limits)))
        (when spec
          (setcdr spec (if (eq (cdr spec) 'all) 20 'all))
          (agenote-knowledge-browse-redraw))))
     ((stringp id) (find-file id))
     (t (message "此处没有卡片")))))

(defun agenote-knowledge-browse-filter (needle)
  "Narrow the browse list to cards matching NEEDLE.
NEEDLE matches (case-folded) against title, category, status or author,
so e.g. \"/stale\" surfaces exactly the stale cards.  Empty NEEDLE
clears the filter."
  (interactive
   (list (read-string (format "过滤 标题/类别/状态/作者 (空串清除%s): "
                              (if agenote--knowledge-browse-filter
                                  (format ", 当前: %s"
                                          agenote--knowledge-browse-filter)
                                "")))))
  (setq agenote--knowledge-browse-filter
        (and (not (string-empty-p needle)) (downcase needle)))
  (agenote-knowledge-browse-redraw))

(defun agenote-knowledge-browse-overview ()
  "Return to the two-group overview: head 20 cards of each domain.
Also clears the substring filter, so the overview always shows the
unfiltered state."
  (interactive)
  (setq agenote--knowledge-browse-limits
        (copy-alist agenote-knowledge-browse--default-limits)
        agenote--knowledge-browse-filter nil)
  (agenote-knowledge-browse-redraw))

;;;###autoload
(defun agenote-knowledge-browse-open-browser ()
  "Open the web visualization of the domain at point via `agenote viz'."
  (interactive)
  (agenote-call-async (agenote-knowledge-browse--domain-at-point)
                      "viz" "--open"))

(defvar agenote-knowledge-browse-mode-map
  (let ((map (make-sparse-keymap)))
    (set-keymap-parent map tabulated-list-mode-map)
    (define-key map (kbd "RET") #'agenote-knowledge-browse-ret)
    (define-key map "/" #'agenote-knowledge-browse-filter)
    (define-key map "B" #'agenote-knowledge-browse-overview)
    (define-key map "o" #'agenote-knowledge-browse-open-browser)
    map))

;;;###autoload
(define-derived-mode agenote-knowledge-browse-mode tabulated-list-mode "KB"
  "CLI-driven knowledge-base overview.
\\{agenote-knowledge-browse-mode-map}"
  (setq tabulated-list-format
        [("状态" 8) ("作者" 8) ("上次使用" 12) ("次数" 5) ("类别" 10) ("标题" 0)]
        ;; nil keeps group blocks contiguous; sorting would interleave them.
        tabulated-list-sort-key nil)
  (setq-local revert-buffer-function #'agenote-knowledge-browse-refresh)
  (tabulated-list-init-header))

(defun agenote-knowledge-browse--open (limits)
  "Open the knowledge-base overview with LIMITS ((DOMAIN . LIMIT) ...)."
  (let ((buffer (get-buffer-create agenote-knowledge-browse-buffer-name)))
    (with-current-buffer buffer
      (agenote-knowledge-browse-mode)
      (setq agenote--knowledge-browse-limits limits)
      (agenote-knowledge-browse-refresh))
    (pop-to-buffer buffer)))

;;;###autoload
(defun agenote-knowledge-browse ()
  "Open the knowledge-base overview: head 20 cards of each domain."
  (interactive)
  (agenote-knowledge-browse--open
   (copy-alist agenote-knowledge-browse--default-limits)))

;;;###autoload
(defun agenote-knowledge-browse-human ()
  "Open the human-domain knowledge-base overview (all cards)."
  (interactive)
  (agenote-knowledge-browse--open '((human . all) (agenote . 0))))

;;;###autoload
(defun agenote-knowledge-browse-agenote ()
  "Open the agenote-domain knowledge-base overview (all cards)."
  (interactive)
  (agenote-knowledge-browse--open '((human . 0) (agenote . all))))

;;;###autoload
(defun agenote-knowledge-viz-open-browser ()
  "Open the human-domain knowledge-base visualization in a browser."
  (interactive)
  (agenote-call-async 'human "viz" "--open"))

(provide 'agenote-knowledge)
;;; agenote-knowledge.el ends here
