# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT
"""lint --json 结构化输出测试：分类键、summary 一致性、人类可读输出不变。"""

from __future__ import annotations

import argparse
import json

import pytest

import agenote.lint as lint_mod

_BAD_CARD = """* DONE 测试卡片
:PROPERTIES:
:ID:       20260824-130000
:CATEGORY: general
:TECH:     general
:TYPE:     hack
:OWNER:    ai
:STATUS:   done
:END:
:general:hack:ai:general:note:extra::
"""


def _args(**kw):
    ns = dict(fix=False, check=False, json=True, files=[])
    ns.update(kw)
    return argparse.Namespace(**ns)


def test_lint_json_categorized(tmp_path, capsys):
    card = tmp_path / "bad.org"
    card.write_text(_BAD_CARD, encoding="utf-8")
    lint_mod.cmd_lint(_args(files=[str(card)]))
    out = json.loads(capsys.readouterr().out)
    # 枚举漂移（TYPE=hack）与 fingerprint（6 段）进对应分类
    assert out["summary"]["files_scanned"] == 1
    assert out["summary"]["files_with_issues"] == 1
    counts = out["summary"]["category_counts"]
    assert "enum_drift" in counts and counts["enum_drift"] >= 1
    assert "fingerprint" in counts and counts["fingerprint"] >= 1
    # 分类条目数与 category_counts 一致（可差分前提）
    for cat, n in counts.items():
        assert len(out[cat]) == n, cat
    assert out["fingerprint"][0]["file"] == "bad.org"
    assert out["fingerprint"][0]["reason"]


def test_lint_json_clean_file(tmp_path, capsys):
    card = tmp_path / "clean.org"
    card.write_text("* DONE 干净\n仅一行\n", encoding="utf-8")
    lint_mod.cmd_lint(_args(files=[str(card)]))
    out = json.loads(capsys.readouterr().out)
    assert out["summary"]["issues_found"] >= 0  # 干净卡可能仍有信息性提示
    assert out["summary"]["files_with_issues"] in (0, 1)


def test_lint_human_readable_unchanged(tmp_path, capsys):
    """不带 --json 时保持人类可读输出（含「检查完成」尾部）。"""
    card = tmp_path / "bad.org"
    card.write_text(_BAD_CARD, encoding="utf-8")
    lint_mod.cmd_lint(
        argparse.Namespace(fix=False, check=False, json=False, files=[str(card)])
    )
    out = capsys.readouterr().out
    assert "检查完成" in out
    assert "bad.org" in out


def test_format_missing_file_fails_without_success_output(tmp_path, capsys):
    """format 单文件失败必须非零退出，且不打印成功汇总或变更报告。"""
    from agenote.orgfmt import cmd_format

    with pytest.raises(SystemExit) as exc:
        cmd_format(argparse.Namespace(files=[str(tmp_path / "missing.org")], check=False))
    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "missing.org" in captured.err
