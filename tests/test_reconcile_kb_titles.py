# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT
"""reconcile._kb_titles 的 BOM 容错与 KB 优先跳过路径。

首行 UTF-8 BOM 的卡片曾让 ``^\\* DONE`` 失配（976f794 只修了 orgserde 的
heading 探测），导致 KB 优先去重对 BOM 卡片失明。本文件锚定：BOM 卡片标题
必须进 _kb_titles，且 reconcile 的同标题事实会被跳过（report.skipped 路径）。
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import agenote.reconcile as reconcile
from agenote.extract.models import ReconciledFact


def _write_card(path: Path, title: str, *, bom: bool = False) -> None:
    content = (
        f"* DONE {title}\n"
        ":PROPERTIES:\n"
        ":ID:       20260925-000000\n"
        ":CATEGORY: general\n"
        ":STATUS:   done\n"
        ":END:\n"
        "正文内容。\n"
    )
    path.write_text(("\ufeff" if bom else "") + content, encoding="utf-8")


def test_kb_titles_parses_bom_card(tmp_path, monkeypatch):
    monkeypatch.setattr(reconcile, "AGENOTE_ROOT", tmp_path)
    exp = tmp_path / "experiences"
    exp.mkdir()
    _write_card(exp / "bom.org", "Guix 通道配置", bom=True)
    _write_card(exp / "plain.org", "普通卡片")

    titles = reconcile._kb_titles()
    assert "guix 通道配置" in titles  # BOM 剥离后可解析（casefold 比对）
    assert "普通卡片" in titles


def test_kb_priority_skips_fact_matching_bom_card_title(tmp_path, monkeypatch):
    """KB 里的 BOM 卡片标题与 reconcile 事实同题 → 事实被跳过不索引。"""
    monkeypatch.setattr(reconcile, "AGENOTE_ROOT", tmp_path)
    exp = tmp_path / "experiences"
    exp.mkdir()
    _write_card(exp / "bom.org", "Guix 通道配置", bom=True)

    fresh = ReconciledFact(
        id="fake:s1:m1",
        source="fake",
        native_id="m1",
        title="Guix 通道配置",
        category="general",
        content="用户询问 Guix 通道配置并得到具体步骤。",
        trust_score=0.7,
        weight=0.7,
    )
    with patch.object(reconcile, "_known_extractors", lambda: {"fake": lambda: ([fresh], [])}):
        report = reconcile.reconcile_source("fake", dry_run=True)

    assert report.skipped == 1
    assert report.indexed == 0
    assert report.errors == 0


def test_kb_priority_skip_persists_after_publish(tmp_path, monkeypatch):
    """非 dry-run 下被 KB 跳过的事实确实不进索引落盘。"""
    monkeypatch.setattr(reconcile, "AGENOTE_ROOT", tmp_path)
    exp = tmp_path / "experiences"
    exp.mkdir()
    _write_card(exp / "bom.org", "Guix 通道配置", bom=True)

    reconcile_dir = tmp_path / ".reconcile"
    reconcile_dir.mkdir()
    reconcile_index = reconcile_dir / "index.json"

    fresh = ReconciledFact(
        id="fake:s1:m1",
        source="fake",
        native_id="m1",
        title="Guix 通道配置",
        category="general",
        content="用户询问 Guix 通道配置并得到具体步骤。",
        trust_score=0.7,
        weight=0.7,
    )
    with patch.object(reconcile, "_known_extractors", lambda: {"fake": lambda: ([fresh], [])}), \
         patch.object(reconcile, "RECONCILE_DIR", reconcile_dir), \
         patch.object(reconcile, "RECONCILE_INDEX", reconcile_index), \
         patch("agenote.core.KB_ROOT", tmp_path):
        report = reconcile.reconcile_source("fake")

    assert report.skipped == 1 and report.errors == 0
    saved = json.loads(reconcile_index.read_text(encoding="utf-8"))
    assert saved["facts"] == []
