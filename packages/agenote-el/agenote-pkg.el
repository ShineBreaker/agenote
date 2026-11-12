;;; agenote-pkg.el --- package declaration for ELPA -*- lexical-binding: t; -*-
;;; no-byte-compile: t

;; SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
;; SPDX-License-Identifier: MIT

;; Version: 0.1.1
;; Package-Version: 0.1.1
;; Package-Requires: ((emacs "29.1"))
;; Keywords: tools, org, productivity

;;; Commentary:

;; Emacs 前端，纯 CLI 适配层：所有文件系统操作都委托给 `agenote` CLI
;; （process 适配层见 agenote.el，交互命令与 browse mode 见
;; agenote-knowledge.el）。本包不实现任何知识库逻辑。
;;
;; 快速上手：把 `agenote` CLI 装进 PATH 后 M-x agenote-list 即可。

;;; Code:

;; 打包入口。ELPA 按本文件声明的包信息装载；版本号是包的单一真相源——
;; CI 与 tools/release/build.py 都以它为准。
;;
;; 参数顺序是定义死的：(NAME VERSION &optional DOCSTRING REQUIREMENTS
;; &rest EXTRA-PROPERTIES)。此前把 docstring 写在 VERSION 位置、
;; author 与 (:require ...) 列表塞进 REQUIREMENTS 之后，导致
;; package-install-file 报 "Invalid version syntax"——本包此前实际
;; 装不上，只有 git 目录手工 load 的路子能跑。
(define-package "agenote"
  "0.1.1"
  "Emacs 前端：知识卡片浏览、搜索与健康度面板（纯 agenote CLI 适配层）"
  '((emacs "29.1"))
  :url "https://github.com/ShineBreaker/agenote"
  :bug-report-url "https://github.com/ShineBreaker/agenote/issues")

;;; agenote-pkg.el ends here
