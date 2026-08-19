# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT
"""HTML 模板 — 从 skeleton.html 加载 + string.Template 占位替换。

骨架源文件是 `skeleton.html`（与本文件同目录），包含 ${css} / ${js_core} 等
命名占位符，由调用方通过 Template.substitute 注入实际内容。

不持有任何 HTML 字符串 —— 所有骨架文本在 .html 文件中。
"""

from pathlib import Path
from string import Template

_SKELETON = (Path(__file__).parent / "skeleton.html").read_text(encoding="utf-8")

# 骨架声明的命名占位符白名单——substitute 仅接受这些键，防未定义注入点
_ALLOWED_PLACEHOLDERS = frozenset(
    {
        "cards_json",
        "css",
        "filter_json",
        "js_charts",
        "js_core",
        "js_force",
        "js_interact",
        "search_json",
        "stats_json",
        "theme",
        "theme_json",
        "top_techs_json",
        "total",
        "updated",
    }
)


class _SkeletonTemplate(Template):
    """占位符白名单版 string.Template——substitute 只接受骨架声明的键。"""

    def substitute(self, *args, **kwargs):  # type: ignore[override]
        mapping = dict(*args, **kwargs)
        unknown = set(mapping) - _ALLOWED_PLACEHOLDERS
        if unknown:
            raise KeyError(f"骨架未声明的占位符: {sorted(unknown)}")
        return super().substitute(mapping)


TEMPLATE = _SkeletonTemplate(_SKELETON)
