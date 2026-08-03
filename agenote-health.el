;;; agenote-health.el --- agenote knowledge-base health panel -*- lexical-binding: t; -*-

;; SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
;; SPDX-License-Identifier: MIT

;; This file is part of agenote-el.

;;; Commentary:

;; The `agenote health' output is already structured human-readable text
;; (section headings, metric lines with thresholds and markers), so it
;; is shown verbatim in a special-mode buffer.  `g' calls
;; `agenote health --quality' for the fuller report (content length /
;; quality-issue sections).
;;
;; Note: `agenote health' is human-domain only (`experiences/').  Agent
;; sub-tree health must be read from `index.json' via the browse view.

;;; Code:

(require 'agenote)

;;;###autoload
(define-derived-mode agenote-health-mode special-mode "agenote-health"
  "Show the `agenote health' knowledge-base health report (human domain).
\\{agenote-health-mode-map}")

(defvar agenote-health-mode-map
  (let ((m (make-sparse-keymap)))
    (define-key m "g" #'agenote-health)
    m)
  "Keymap for `agenote-health-mode'.")

(defun agenote--refresh-health-buffer ()
  "Re-call `agenote --domain human health --quality' and fill this health buffer."
  (let ((inhibit-read-only t))
    (erase-buffer)
    (insert (or (agenote-call-string 'human "health" "--quality")
                "（agenote 命令不可用）"))
    (goto-char (point-min))))

;;;###autoload
(defun agenote-health ()
  "Open the human knowledge-base health panel *agenote-health*.
Calls `agenote --domain human health --quality'.  `g' refreshes."
  (interactive)
  (if (agenote-resolve-executable)
      (let ((buf (get-buffer-create "*agenote-health*")))
        (with-current-buffer buf
          (unless (eq major-mode 'agenote-health-mode)
            (agenote-health-mode))
          (agenote--refresh-health-buffer))
        (switch-to-buffer buf))
    (message "未找到 agenote 命令")))

(provide 'agenote-health)
;;; agenote-health.el ends here
