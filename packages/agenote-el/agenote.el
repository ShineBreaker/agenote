;;; agenote.el --- agenote CLI adaptation layer for Emacs -*- lexical-binding: t; -*-

;; SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
;; SPDX-License-Identifier: MIT

;; Author: BrokenShine <xchai404@gmail.com>
;; Keywords: tools, knowledge
;; Package-Requires: ((emacs "29.1"))

;; This file is part of agenote-el, the Emacs integration for the
;; agenote knowledge-base CLI.  It provides the synchronous/async
;; process adapters and the path customization the rest of the package
;; builds on.

;;; Commentary:

;; The thin layer between Emacs and the `agenote' CLI.  All other
;; agenote-*.el files route CLI access through `agenote-call' /
;; `agenote-call-async'; they never invoke the executable directly.
;;
;; Design invariants:
;;   - `(executable-find "agenote")' is resolved on every call.  We do
;;     NOT cache an absolute /gnu/store path: a long-lived daemon must
;;     pick up a new agenote after a Guix profile switch.
;;   - Every call passes `--domain human|agenote' explicitly.  The
;;     default domain must never be relied on, otherwise a human-domain
;;     mutation could silently land in the agent sub-tree.
;;   - `agenote-call' returns a plist `(:status :stdout :stderr :command
;;     :domain)' so callers can branch on exit status uniformly.

;;; Code:

(require 'cl-lib)


;;;; Customization

(defcustom agenote-org-directory
  (expand-file-name "~/Documents/Org/")
  "Org root directory backing the knowledge base.
Host configurations should set this to match their own org directory
constant (e.g. via `use-package' :custom)."
  :group 'agenote
  :type 'directory)

(defcustom agenote-experiences-directory
  (expand-file-name "experiences" agenote-org-directory)
  "Human-domain experience cards directory.
Cards live one per file under `<category>/<timestamp>.org'.
Set this if `agenote-org-directory' is customized after load."
  :group 'agenote
  :type 'directory)

(defcustom agenote-inbox-file
  (expand-file-name "inbox.org" agenote-org-directory)
  "Org capture inbox file."
  :group 'agenote
  :type 'file)

(defcustom agenote-subdomain-directory
  (expand-file-name "agenote" agenote-org-directory)
  "agenote agent sub-domain directory.
Cards written by agents plus `index.json'."
  :group 'agenote
  :type 'directory)


;;;; Executable resolution and domain validation

(defconst agenote-valid-domains '(human agenote)
  "Domain values accepted by the `agenote' CLI.
Kept in sync with `agenote --help'.")

(defun agenote-resolve-executable ()
  "Resolve the `agenote' executable path on demand.
Return the executable path string, or nil if not found.  Never caches
an absolute store path: a daemon outliving a Guix profile update must
locate the agenote of the current generation."
  (executable-find "agenote"))

;; Back-compat alias for call sites written before the public rename.
(defalias 'agenote--resolve-executable #'agenote-resolve-executable)

(defun agenote--ensure-domain (domain)
  "Validate DOMAIN, returning the lower-case symbol.  Fail fast on nil."
  (let ((d (if (stringp domain) (intern (downcase domain)) domain)))
    (unless (memq d agenote-valid-domains)
      (error "agenote-call: domain must be %s, got %S"
             (mapconcat #'symbol-name agenote-valid-domains "|")
             domain))
    d))


;;;; Keymap for async result buffers

;; Defined before `agenote-call-async' so the async function can
;; reference it; also re-used by the knowledge/health sections.
(defvar agenote-async-mode-map
  (let ((m (make-sparse-keymap)))
    (set-keymap-parent m special-mode-map)
    m)
  "Keymap for `*agenote-<command>*' async result buffers.")


;;;; Synchronous call

;;;###autoload
(cl-defun agenote-call (domain command &rest args)
  "Call the agenote CLI synchronously.
DOMAIN is required (`human' or `agenote'), COMMAND is the subcommand
string, and ARGS are appended verbatim.

Return a plist `(:status :stdout :stderr :command :domain)':
  :status   process exit code (-1 if it could not be started)
  :stdout   stdout string (trimmed)
  :stderr   stderr string (trimmed)
  :command  full argv list (for diagnostics)
  :domain   the domain symbol actually used

Usage:
  (agenote-call 'human \"list\" \"--all\")
  (agenote-call 'agenote \"search\" \"emacs\" \"--limit\" \"10\")
  (plist-get (agenote-call 'human \"stats\") :stdout)"
  (let* ((dom (agenote--ensure-domain domain))
         (exe (agenote-resolve-executable))
         (argv (delq nil (append (list exe "--domain" (symbol-name dom) command) args))))
    (if (not exe)
        (list :status -1 :stdout "" :stderr "agenote executable not found"
              :command argv :domain dom)
      (let ((stdout-buffer (generate-new-buffer " *agenote-stdout*"))
            (stderr-file (make-temp-file "agenote-stderr"))
            status)
        (unwind-protect
            (progn
              ;; call-process signature: (PROGRAM INFILE BUFFER DISPLAY &rest ARGS)
              ;; BUFFER as a list (STDOUT-DEST STDERR-DEST): STDERR-DEST must be a
              ;; file name string, not a buffer object.  Capture stderr via a temp
              ;; file, read back into a string.
              (setq status
                    (apply #'call-process
                           (car argv) nil (list stdout-buffer stderr-file) nil
                           (cdr argv)))
              (list :status (or status -1)
                    :stdout (string-trim
                             (with-current-buffer stdout-buffer (buffer-string)))
                    :stderr (string-trim
                             (with-temp-buffer
                               (insert-file-contents stderr-file)
                               (buffer-string)))
                    :command argv
                    :domain dom))
          (when (buffer-live-p stdout-buffer) (kill-buffer stdout-buffer))
          (when (file-exists-p stderr-file) (delete-file stderr-file)))))))


;;;; Async call

;;;###autoload
(cl-defun agenote-call-async (domain command &rest args)
  "Call the agenote CLI asynchronously, with the same arguments as `agenote-call'.
Use this for long-running tasks (reindex / lint / deduplicate / commit
/ viz) so Emacs is not blocked.  The result is written to a
`*agenote-<command>*' buffer (special-mode; `g' re-runs, `q' quits).

Return the started process object; for synchronous results use
`agenote-call'."
  (let* ((dom (agenote--ensure-domain domain))
         (exe (agenote-resolve-executable))
         (argv (delq nil (append (list exe "--domain" (symbol-name dom) command) args)))
         (buf-name (format "*agenote-%s*" command))
         (buffer (get-buffer-create buf-name)))
    (if (not exe)
        (progn
          (with-current-buffer buffer
            (let ((inhibit-read-only t))
              (erase-buffer)
              (insert "agenote executable not found\n")))
          (display-buffer buffer)
          nil)
      (with-current-buffer buffer
        (let ((inhibit-read-only t))
          (erase-buffer)
          (insert (format "$ %s\n\n" (string-join argv " ")))
          (special-mode)
          ;; `g' re-runs the same command.
          (let ((mode-map agenote-async-mode-map))
            (use-local-map (or mode-map (make-sparse-keymap))))
          (define-key (current-local-map) "g"
                      (lambda () (interactive)
                        (apply #'agenote-call-async dom command args)))))
      (let ((proc (make-process
                   :name (format "agenote %s" command)
                   :buffer buffer
                   :command argv
                   :connection-type 'pipe
                   :sentinel (lambda (proc event)
                               (let ((buf (process-buffer proc)))
                                 (when (buffer-live-p buf)
                                   (with-current-buffer buf
                                     (let ((inhibit-read-only t))
                                       (goto-char (point-max))
                                       (insert
                                        (format "\n[process %s]\n"
                                                (string-trim event)))))))))))
        (display-buffer buffer)
        proc))))


;;;; String convenience wrapper

;;;###autoload
(defun agenote-call-string (domain command &rest args)
  "Call agenote synchronously and return stdout as a string.
DOMAIN/COMMAND/ARGS are as for `agenote-call'.  Return the empty string
on failure.  Provided for call sites that only consume text; new code
should use `agenote-call' directly."
  (plist-get (apply #'agenote-call domain command args) :stdout))

(provide 'agenote)
;;; agenote.el ends here
