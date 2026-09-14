# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT
"""memscan（跨 agent 记忆库只读扫描）测试：布局解析、§ 分隔、env 优先级、缺失容错。"""

from __future__ import annotations

import json

import agenote.memscan as memscan
from agenote.memscan import _parse_frontmatter, scan_memories


ZCODE_FM = """---
name: hard-gates-for-agents
description: 约束 agent 必须硬拒绝+--force 逃生
metadata: 
  node_type: memory
  type: feedback
---

用户设计原则：硬拒绝才能约束 agent。
"""


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_parse_frontmatter_scalars_and_metadata_type():
    meta, body = _parse_frontmatter(ZCODE_FM)
    assert meta["name"] == "hard-gates-for-agents"
    assert meta["description"].startswith("约束 agent")
    assert meta["type"] == "feedback"
    assert body.strip().startswith("用户设计原则")


def test_parse_frontmatter_absent():
    meta, body = _parse_frontmatter("没有 frontmatter 的正文。")
    assert meta == {}
    assert "没有 frontmatter" in body


def test_scan_dir_source_parses_and_skips_index(tmp_path, monkeypatch):
    root = tmp_path / "memories"
    _write(root / "projects/proj-a/memory/hard-gates.md", ZCODE_FM)
    _write(root / "projects/proj-a/memory/plain.md", "# 无 frontmatter\n\n正文。")
    _write(root / "projects/proj-a/memory/MEMORY.md", "# Memory Index\n")
    monkeypatch.setenv("ZCODE_MEMORIES_DIR", str(root))

    report = scan_memories("zcode")
    assert report["notes"] == []
    names = [e["name"] for e in report["entries"]]
    # MEMORY.md 索引被跳过；无 frontmatter 时用文件名兜底
    assert names == ["hard-gates-for-agents", "plain"]
    first = report["entries"][0]
    assert first["source"] == "zcode"
    assert first["project"] == "proj-a"
    assert first["type"] == "feedback"
    assert "硬拒绝" in first["body"]
    assert report["by_source"]["zcode"] == 2


def test_scan_missing_dir_returns_note(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "nope"))
    report = scan_memories("codex")
    assert report["total"] == 0
    assert len(report["notes"]) == 1 and "不存在" in report["notes"][0]


def test_scan_hermes_sections(tmp_path, monkeypatch):
    root = tmp_path / "hermes"
    _write(
        root / "memories/MEMORY.md",
        "[SOUL: 目标导向] 工作围绕目标组织。\n§\n调试纪律：只读真实状态。\n§\n",
    )
    _write(root / "memories/USER.md", "偏好简短回复。\n§\n")
    monkeypatch.setenv("HERMES_HOME", str(root))

    report = scan_memories("hermes")
    assert report["total"] == 3
    titles = [e["name"] for e in report["entries"]]
    assert titles[0] == "SOUL: 目标导向"  # [标签] 前缀作标题
    assert titles[1].startswith("调试纪律")  # 首句截断
    assert report["entries"][0]["path"].endswith("MEMORY.md")


def test_xdg_placeholder_fallback(tmp_path, monkeypatch):
    # 未设专属 env 时，$XDG_DATA_HOME 占位符展开到规范目录
    monkeypatch.delenv("REASONIX_HOME", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    root = tmp_path / "data/reasonix"
    _write(root / "projects/proj-b/memory/emacs31.md", ZCODE_FM)

    report = scan_memories("reasonix")
    assert report["total"] == 1
    assert report["entries"][0]["project"] == "proj-b"


def test_scan_all_aggregates_sources(tmp_path, monkeypatch):
    # 隔离全部 6 源，避免扫到本机真实记忆库
    for env in ("ZCODE_MEMORIES_DIR", "CLAUDE_CONFIG_DIR", "CODEX_HOME",
                "PI_CODING_AGENT_DIR", "REASONIX_HOME", "HERMES_HOME"):
        monkeypatch.setenv(env, str(tmp_path / "isolated"))
    monkeypatch.setenv("ZCODE_MEMORIES_DIR", str(tmp_path / "z"))
    _write(tmp_path / "z/projects/p/memory/a.md", ZCODE_FM)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "c"))
    _write(tmp_path / "c/projects/p/memory/b.md", ZCODE_FM)
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(tmp_path / "no-pi"))

    report = scan_memories("all")
    assert report["total"] == 2
    assert report["by_source"]["zcode"] == 1
    assert report["by_source"]["claude"] == 1
    assert any("no-pi" in n for n in report["notes"])


def test_cmd_json_output(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ZCODE_MEMORIES_DIR", str(tmp_path / "z"))
    _write(tmp_path / "z/projects/p/memory/a.md", ZCODE_FM)

    import argparse

    memscan.cmd_scan_memories(argparse.Namespace(source="zcode", json=True))
    out = json.loads(capsys.readouterr().out)
    assert out["total"] == 1
    assert out["entries"][0]["name"] == "hard-gates-for-agents"


def test_source_envs_have_matching_schema_keys():
    """每个记忆源的 env 必须在 SCHEMA[memories.sources] 中有对应键（键名 = env 小写）。

    resolve_xdg_path 在没有 env 时按 env.lower() 查配置，键名漂移会让该源
    在 env 未设时抛 KeyError——见 test_pi_source_without_env_uses_xdg_default。
    """
    from agenote import config

    section = config.SCHEMA["memories.sources"]
    missing = sorted(s.env for s in memscan.SOURCES.values() if s.env.lower() not in section)
    assert missing == []


def test_pi_source_without_env_uses_xdg_default(tmp_path, monkeypatch):
    """回归：PI_CODING_AGENT_DIR 未设时走配置/XDG 分支，不得 KeyError（曾键名漂移）。"""
    monkeypatch.delenv("PI_CODING_AGENT_DIR", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    _write(tmp_path / "cfg/omp/agent/memory/note.md", ZCODE_FM)

    report = scan_memories("pi")
    assert report["total"] == 1
    assert report["by_source"]["pi"] == 1
