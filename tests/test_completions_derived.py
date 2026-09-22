# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT
"""completions 枚举派生测试：数据型枚举必须跟随真相源，不得手抄漂移。

覆盖：extract/memscan source 注册表、seed types / owner / entry / memory
types 领域枚举。语义枚举（DOMAIN/SHELLS/THEMES）是 CLI 接口契约本身，
不在此测。
"""

from __future__ import annotations

from agenote.completions import (
    generate,
    _entry_values,
    _extract_sources,
    _memscan_sources,
    _memory_types,
    _owner_values,
    _type_values,
)
from agenote.core import (
    MEMORY_TYPES,
    SEED_AGENTS,
    SEED_TYPES,
    VALID_ENTRY_TYPES,
    VALID_OWNERS,
)


def test_type_values_follow_seed_types():
    assert set(_type_values().split()) == SEED_TYPES


def test_owner_values_follow_valid_owners():
    assert set(_owner_values().split()) == VALID_OWNERS


def test_entry_values_follow_valid_entry_types():
    assert set(_entry_values().split()) == VALID_ENTRY_TYPES


def test_memory_types_follow_core():
    assert _memory_types().split() == MEMORY_TYPES


def test_extract_sources_follow_registry():
    """extract registry 的 7 个 adapter + all；新增 adapter 自动出现。"""
    from agenote.extract.base import SOURCES, _resolve_extractors

    _resolve_extractors()  # 触发注册
    srcs = _extract_sources().split()
    assert set(SOURCES) | {"all"} == set(srcs)
    assert srcs[-1] == "all"


def test_memscan_sources_follow_registry():
    from agenote.memscan import SOURCES as MEMSCAN_REGISTRY

    srcs = _memscan_sources().split()
    assert set(MEMSCAN_REGISTRY) | {"all"} == set(srcs)
    assert srcs[-1] == "all"


def test_fish_script_carries_derived_values():
    """生成的 fish 脚本携带派生枚举（不是旧手抄串）。"""
    fish = generate("fish")
    assert _extract_sources() in fish
    assert _memscan_sources() in fish
    assert _type_values() in fish


def test_bash_script_carries_derived_values():
    bash = generate("bash")
    assert _extract_sources() in bash
    for v in MEMORY_TYPES:
        assert v in bash


def test_zsh_script_carries_derived_values():
    zsh = generate("zsh")
    assert ",".join(_extract_sources().split()) in zsh
    assert ",".join(MEMORY_TYPES) in zsh


def test_seed_agents_untouched():
    """KNOWN_AGENTS → SEED_AGENTS 重命名后种子成员不变（回归锚点）。"""
    assert {
        "omp",
        "hermes",
        "crush",
        "opencode",
        "mimocode",
        "claude-code",
        "zcode",
        "pi-dream",
        "pi-distill",
    } == SEED_AGENTS
