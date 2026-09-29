# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT

"""行内标记 =...= / ~...~ / *...* 的内外侧空格修复与 zh 规范豁免。

覆盖：全角标点紧贴修复、内侧去空格、跨标记边界豁免（verbatim 内嵌
字面星号、双星 bold）、含空白内容不修（公式）、代码块保护、幂等。
"""

from __future__ import annotations

from agenote.orgfmt import _fix_inline_markers, _zh_style


def _fix(text: str) -> str:
    return _fix_inline_markers(text)[0]


# ── 外侧：全角标点紧贴 → 插空格 ────────────────────────────────────────────


def test_eq_pre_cjk_spacing():
    assert _fix("：=foo= 说明") == "： =foo= 说明"


def test_eq_post_cjk_spacing():
    assert _fix("=foo=）") == "=foo= ）"
    assert _fix("=foo=、=bar= 等") == "=foo= 、 =bar= 等"


def test_eq_both_sides_cjk():
    assert _fix("（=C-<tab>=）循环") == "（ =C-<tab>= ）循环"


# ── 内侧：去边界空格 ───────────────────────────────────────────────────────


def test_eq_inner_strip():
    assert _fix("= foo = 结束") == "=foo= 结束"


def test_eq_inner_with_embedded_star():
    assert _fix("顶部 = *Minibuf-0* = 承载") == "顶部 =*Minibuf-0*= 承载"


# ── 保护：跨标记边界豁免 ───────────────────────────────────────────────────


def test_verbatim_with_literal_star_kept():
    # verbatim 内嵌字面星号（buffer 名场景），不得被拆成「= *X* =」
    assert _fix("=* Warnings *=") == "=* Warnings *="


def test_double_star_bold_normalized():
    # Markdown 式双星不渲染，规范化为 org 单星 + 两侧空格
    assert _fix("**bold**") == " *bold* "
    assert _fix("留到**第一个 client frame* * 才弹") == "留到 *第一个 client frame* 才弹"
    assert _fix("后，**未本地修改* *的文件") == "后， *未本地修改* 的文件"


def test_double_star_in_verbatim_kept():
    # verbatim 内嵌的字面双星是内容，不得规范化
    assert _fix("=**argv= 指针") == "=**argv= 指针"
    assert _fix("~**x**~") == "~**x**~"


def test_double_star_heading_and_code_kept():
    # 行首标题与 C 指针不误伤
    assert _fix("** 三个必备工具") == "** 三个必备工具"
    assert _fix("char **argv 数组") == "char **argv 数组"


def test_tilde_in_verbatim_kept():
    assert _fix("=~foo~=") == "=~foo~="


# ── 不修：内容含空白（公式/句子，可能是字面符号） ─────────────────────────


def test_inner_formula_kept():
    s = "= 1 + Shift(1) ，方向码 = 上下"
    assert _fix(s) == s


# ── 保护：代码块与标题 ────────────────────────────────────────────────────


def test_src_block_untouched():
    s = "#+begin_src emacs-lisp\n(=foo=)\n#+end_src"
    assert _fix(s) == s


def test_heading_untouched():
    assert _fix("** 标题 = foo =") == "** 标题 = foo ="


# ── zh 规范：标记边界空格豁免 ─────────────────────────────────────────────


def test_zh_keeps_marker_boundary_space():
    out, _ = _zh_style("（ =foo= ）循环")
    assert out == "（ =foo= ）循环"


def test_zh_still_trims_plain_punct_space():
    out, _ = _zh_style("主板 （从） ，则。")
    assert out == "主板（从），则。"


# ── 保护：多标记行错配对（finditer 跨段吞并） ─────────────────────────────


def test_over_paired_segment_untouched():
    # 「甲对 + 中段文本 + 乙对」不得被错配成对后吞掉中段
    s = "把 =custom/open-project-folder= 的 =default-directory= 临时指向"
    assert _fix(s) == s


def test_table_nil_segment_untouched():
    # 表格行「（nil = 无 ts 版本）  | =major-mode-remap-alist=」原样
    s = "| =:ts-mode= | Tree-sitter 对应 mode（nil = 无 ts 版本） | =major-mode-remap-alist= |"
    assert _fix(s) == s


# ── 报告与幂等 ────────────────────────────────────────────────────────────


def test_change_report_mentions_eq():
    out, changes = _fix_inline_markers("：=foo= 说明")
    assert out == "： =foo= 说明"
    assert any("=code=" in c for c in changes)


def test_idempotent():
    for s in ("（=C-<tab>=）循环", "顶部 = *Minibuf-0* = 承载", "=* Warnings *="):
        once = _fix(s)
        assert _fix(once) == once
