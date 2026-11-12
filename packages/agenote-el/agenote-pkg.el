;;; agenote-pkg.el --- package declaration for ELPA -*- lexical-binding: t; -*-

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

(eval-when-compile (require 'package))

;; 打包入口。ELPA 按本文件声明的 feature 列表决定装载哪些 .el；
;; 版本号是包的单一真相源——CI 与 tools/release/build.py 都以它为准。
;; define-package 自 Emacs 29.1 起标记废弃（新包应只写头字段，ELPA 自动
;; 生成 -pkg.el）。本文件是手写描述符，打包链路与老版本 Emacs 都依赖它，
;; 故保留调用、只压掉这条编译警告。
(with-suppressed-warnings ((obsolete define-package))
  (define-package "agenote"
  "Emacs 前端：知识卡片浏览、搜索与健康度面板（纯 agenote CLI 适配层）"
  "0.1.1"
  "BrokenShine"
  '((emacs "29.1"))
  '((:require "agenote")
    (:require "agenote-knowledge")
    (:require "agenote-health")
    (:require "agenote-keybinds")
    (:require "agenote-dashboard"))
  :url "https://github.com/ShineBreaker/agenote"
  :bug-report-url "https://github.com/ShineBreaker/agenote/issues")

  )  ; with-suppressed-warnings

;;; agenote-pkg.el ends here
