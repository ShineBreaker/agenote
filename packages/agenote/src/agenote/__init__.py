# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT
#
# KB CLI 共享库:cards / memory / lint / core / viz。
#
# 所有模块通过 `from agenote.X import ...` 由 bin/kb 加载。

from importlib.metadata import PackageNotFoundError, version as _pkg_version

try:
    __version__ = _pkg_version("agenote")
except PackageNotFoundError:  # 从源码树直接运行（未安装），metadata 不存在
    __version__ = "unknown"
