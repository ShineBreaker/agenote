# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT

"""orgfmt 中文技术文档规范（_zh_style）自检。

R1-R8 自动修复与 L1-L4 只报不改规则，含幂等性与代码块/表格/drawer 保护。
用 _zh_style 单测规则本身（避免 _normalize_blank_lines 插空行干扰断言），
再用 format_org 整管线测保护与幂等。

运行：uv run pytest tests/test_orgfmt_zh_style.py -q
"""

from __future__ import annotations

from agenote.orgfmt import _zh_style, format_org


def _zh(text: str) -> str:
    """只跑中文规范阶段，返回新文本。"""
    return _zh_style(text)[0]


def _zh_changes(text: str) -> list[str]:
    return _zh_style(text)[1]


# ── R1 全角空格 ────────────────────────────────────────────────────────────


def test_r1_fullwidth_space():
    assert _zh("使用　全角空格") == "使用 全角空格"


def test_r1_idempotent():
    once = _zh("使用　全角空格")
    assert _zh(once) == once


# ── R2 全角标点旁空格 ──────────────────────────────────────────────────────


def test_r2_punct_spacing():
    assert _zh("如果 CPU 设有限额 （从 K8S 指定的上限） ，则需要调整。") == (
        "如果 CPU 设有限额（从 K8S 指定的上限），则需要调整。"
    )


# ── R3 中英文之间补空格 ────────────────────────────────────────────────────


def test_r3_zh_latin_spacing():
    assert _zh("这个数据库有不错的MySQL兼容性") == "这个数据库有不错的 MySQL 兼容性"
    assert _zh("性能提升了50%") == "性能提升了 50%"


def test_r3_no_space_before_punct():
    # 英文后紧跟中文标点：右侧空格本就省略，不应补
    assert _zh("使用MySQL。") == "使用 MySQL。"


def test_r3_spares_config_key_and_enum():
    # 与 tools/doc-punct.py 同口径：这些位置的空格由语法/约定决定
    assert _zh("在 NM [connection]段写默认") == "在 NM [connection]段写默认"
    assert _zh("2=disable省电 3=enable省电") == "2=disable省电 3=enable省电"


def test_r3_spares_leading_markup():
    # 行首标记/链接语法之后不补空格（半角转全角归 doc-punct，orgfmt 不做）
    assert _zh("- **桌面**:变体") == "- **桌面**:变体"


def test_r3_idempotent():
    once = _zh("这个数据库有不错的MySQL兼容性")
    assert _zh(once) == once


# ── R4 破折号 ──────────────────────────────────────────────────────────────


def test_r4_dash_spacing():
    assert _zh("这个符号表示禁止 —— stop") == "这个符号表示禁止——stop"


# ── R5 省略号 ──────────────────────────────────────────────────────────────


def test_r5_ellipsis():
    # ASCII 三点转换后，两侧空格由 R2/R3 收尾：中文标点旁空格本就该删，
    # Latin 侧也不补（R3 只认汉字边界，`……` 不是 Latin 字符）
    assert _zh("使用 so...that... 句型") == "使用 so……that……句型"


def test_r5_spares_version_numbers():
    # 版本号/范围：三点两侧是数字，不动
    assert _zh("版本 v1.2.3 与 v2...v3 范围") == "版本 v1.2.3 与 v2...v3 范围"


def test_r5_spares_table_truncation():
    # 表格截断标记不动
    assert _zh("| a | b... |") == "| a | b... |"


# ── R6 连续感叹号 ──────────────────────────────────────────────────────────


def test_r6_repeated_bang():
    assert _zh("真的很厉害！！") == "真的很厉害！"


# ── R7 序数词 ──────────────────────────────────────────────────────────────


def test_r7_ordinal_dun():
    assert _zh("3、 行政文明建设中的依法行政") == "3. 行政文明建设中的依法行政"


def test_r7_spares_parallel_numbers():
    # 「3、5、7」是并列数字，不是序数词，不能动
    assert _zh("支持 3、5、7 三种规格") == "支持 3、5、7 三种规格"


# ── R8 数值与单位 ──────────────────────────────────────────────────────────


def test_r8_unit_spacing():
    assert _zh("每 2 分钟导入一个256MB 的数据文件") == (
        "每 2 分钟导入一个 256 MB 的数据文件"
    )
    assert _zh("重量是7kg") == "重量是 7 kg"


def test_r8_exceptions_no_space():
    # 百分号不补空格
    assert _zh("占比为60%") == "占比为 60%"


def test_r8_idempotent():
    once = _zh("每 2 分钟导入一个256MB 的数据文件")
    assert _zh(once) == once


# ── 保护：代码块 / 表格 / drawer 内不动 ────────────────────────────────────


def test_org_code_block_untouched():
    src = (
        "#+begin_src bash\n"
        "echo 使用　全角空格和MySQL\n"
        "curl http://x.com?a=1 （中文）\n"
        "#+end_src\n"
    )
    assert _zh(src) == src


def test_markdown_fence_untouched():
    src = "```bash\necho 使用　全角空格和MySQL\n```\n"
    assert _zh(src) == src


def test_table_row_untouched():
    src = "| 项目 | 说明 |\n| 速度 | 256MB |\n"
    assert _zh(src) == src


def test_drawer_untouched():
    src = ":PROPERTIES:\n:ID: 20260925-000000\n:END:\n"
    assert _zh(src) == src


def test_heading_lint_only():
    # 标题行只报告不改写：含繁体用语时报告，但不替换字符
    src = "* 使用　全角空格的檔案\n"
    out, changes = _zh_style(src)
    assert "　" in out  # 全角空格未被替换
    assert "檔" in out  # 繁体字未被替换
    assert any("繁体" in c for c in changes)


def test_heading_plain_untouched():
    # 无 lint 命中的标题行：原样返回，无变更
    src = "* 中文规范验证\n"
    out, changes = _zh_style(src)
    assert out == src
    assert changes == []


def test_org_metadata_lines_untouched():
    # org 元数据行不是正文：:EFFORT: 8h 不补单位空格，+1w 不当缩略语
    for src in (":EFFORT:   8h\n", "DEADLINE: <2024-01-15 周一 +1w>\n"):
        out, changes = _zh_style(src)
        assert out == src
        assert changes == []


def test_inline_math_untouched():
    # 行内公式不是正文：4ac 是代数式，不是缩略语
    src = "$$x = \\frac{-b \\pm \\sqrt{b^2 - 4ac}}{2a}$$\n"
    out, changes = _zh_style(src)
    assert out == src
    assert changes == []


# ── L1-L4 只报不改 ─────────────────────────────────────────────────────────


def test_lint_de_dasks_report_only():
    text, changes = _zh_style("他显式地使用事务，这个值不宜调得过大。")
    assert any("待人工" in c and "的地得" in c for c in changes)
    assert text == "他显式地使用事务，这个值不宜调得过大。"


def test_lint_traditional_chinese():
    text, changes = _zh_style("這個資料庫有不错的性能")
    assert any("繁体" in c for c in changes)
    assert "這" in text  # 不改写


def test_lint_bad_abbrev():
    _text, changes = _zh_style("机器配置为 16c32g")
    assert any("不规范缩略语" in c for c in changes)


# ── 幂等性（阶段级 + 整管线） ──────────────────────────────────────────────


def test_stage_idempotent():
    src = (
        "使用　全角空格和MySQL。\n"
        "如果 CPU 设有限额 （从 K8S 指定的上限） ，则需要调整。\n"
        "这个符号表示禁止 —— stop\n"
        "真的很厉害！！\n"
    )
    once = _zh(src)
    assert _zh(once) == once


def test_full_pipeline_idempotent():
    src = (
        "* DONE 中文规范验证\n"
        ":PROPERTIES:\n"
        ":ID: 20260925-120000\n"
        ":END:\n"
        "使用　全角空格和MySQL。\n"
        "\n"
        "```bash\n"
        "echo 使用　全角空格和MySQL\n"
        "```\n"
    )
    once = format_org(src)[0]
    assert format_org(once)[0] == once


def test_full_pipeline_drawer_no_blank_insertion():
    # 回归：drawer 位于文件首行时不得插入空行
    src = ":PROPERTIES:\n:ID: 20260925-000000\n:END:\n"
    assert format_org(src)[0] == src


def test_full_pipeline_fence_no_blank_insertion():
    # 回归：markdown 围栏内容不得被插入空行
    src = "```bash\necho hi\n```\n"
    assert format_org(src)[0] == src


# ── 回归：省略号幂等（2026-10-03 实测 lint --fix 每轮重改 348 个文件） ──────
#
# 症状：`agenote lint --fix` 连跑两轮都在改同一批文件、报「全角标点旁多余
# 空格」+「英文省略号 → 中文省略号」。根因：`_fix_ellipsis` 的正则 `…+` 把
# 上一轮产物「……」整体再匹配一次并重复 append changes，而 `_fix_punct_spacing`
# 每轮再删一处 `……` 左侧空格——文本每轮都不同，lint 永不收敛。


def test_r5_ellipsis_idempotent():
    """已是中文省略号 + 两侧空格正确时，不得再产生任何变更。"""
    # 规范上中文标点旁禁半角空格，故合法形态是「……」紧邻两侧内容
    src = "参考 ~path/to/file……~ 用法。"
    once = _zh(src)
    assert once == src
    assert _zh(once) == once


def test_r5_ellipsis_punct_space_collapses_once():
    """「…… 」带空格的形态第一轮删空格，第二轮零变更（振荡回归）。"""
    src = "参考 ~path/to/file ……~ 用法。"
    once = _zh(src)
    assert once == "参考 ~path/to/file……~ 用法。"
    assert _zh(once) == once


def test_r5_ellipsis_ascii_still_converts():
    """ASCII 三点仍要转，转换后空格一并收尾且幂等。"""
    src = "参考 path/to/file ... 用法。"
    once = _zh(src)
    assert once == "参考 path/to/file……用法。"
    assert _zh(once) == once


def test_r5_ellipsis_version_range_untouched():
    src = "版本范围 v1...v2 与 v1.2.3 不动。"
    assert _zh(src) == src


def test_full_pipeline_ellipsis_no_change_on_second_run():
    """整管线第二次跑必须零变更（含省略号 + 全角标点空格的组合行）。"""
    src = (
        "commit 被拦截时用 ~HTTPConnection(\"127.0.0.1\", port, ……)~ 硬编码主机，"
        "另见 ~mount(8)~ 与 ~(options ……)~。\n"
    )
    once, ch1 = format_org(src, strict=True)
    twice, ch2 = format_org(once, strict=True)
    assert twice == once, f"第二轮仍产生文本变更: {ch2[:3]}"
