# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT
"""completions 枚举派生测试：数据型枚举必须跟随真相源，不得手抄漂移。

覆盖：extract/memscan source 注册表、seed types / owner / entry / memory
types 领域枚举。语义枚举（DOMAIN/SHELLS/THEMES）是 CLI 接口契约本身，
不在此测。
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

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
    """extract registry 的 6 个 adapter + all；新增 adapter 自动出现。"""
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
    """zsh 生成器必须用 _arguments 的「:消息:(a b c)」值列表形式。

    `:{a,b,c}` 会被 zsh 当作 shell 代码执行，选项值补全静默失效（实测
    `command not found: aa,bb,cc`），所以这里同时锁住列表形式本身。
    """
    zsh = generate("zsh")
    assert f":来源:({_extract_sources()})" in zsh
    assert f":记忆类型:({_memory_types()})" in zsh
    assert ":{" not in zsh


def test_commands_match_cli_handlers():
    """补全顶层命令必须与 CLI dispatch 一致，禁止已删除命令残留。"""
    import ast
    from pathlib import Path

    from agenote.cli import main
    from agenote.completions import COMMANDS

    tree = ast.parse(Path(main.__code__.co_filename).read_text(encoding="utf-8"))
    dispatch = next(
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "commands" for target in node.targets)
    )
    assert isinstance(dispatch, ast.Dict)
    assert set(COMMANDS) == {
        key.value for key in dispatch.keys if isinstance(key, ast.Constant)
    }


def test_bash_completion_runtime_enum_values(tmp_path):
    """Bash 候选必须按空格拆分，scan-memories 与全局域也有真实补全。"""
    bash = shutil.which("bash")
    assert bash, "测试环境需要 bash"
    script = tmp_path / "agenote.bash"
    script.write_text(generate("bash"), encoding="utf-8")
    probe = r'''
source "$1"
_init_completion() {
  cur="${COMP_WORDS[COMP_CWORD]}"
  prev="${COMP_WORDS[COMP_CWORD-1]}"
  words=("${COMP_WORDS[@]}")
  cword=$COMP_CWORD
}
run() {
  COMP_WORDS=("$@")
  COMP_CWORD=$((${#COMP_WORDS[@]} - 1))
  _agenote_completions
  printf '%s\n' "${COMPREPLY[@]}"
}
run agenote memory --type ""
run agenote viz --theme ""
run agenote scan-memories --source ""
run agenote --domain ""
run agenote curate ""
'''
    result = subprocess.run(
        [bash, "-c", probe, "_", str(script)],
        text=True,
        capture_output=True,
        check=True,
    )
    lines = result.stdout.splitlines()
    assert lines[:3] == ["feedback", "project", "reference"]
    assert lines[3:6] == ["light", "dark", "auto"]
    assert "hermes" in lines[6:13] and "all" in lines[6:13]
    assert lines[13:15] == ["human", "agenote"]
    assert lines[15:] in ([], [""])


def test_zsh_completion_uses_separated_values():
    """_arguments 值列表是空格分隔的「:消息:(a b c)」，逗号形式是单字面量。"""
    zsh = generate("zsh")
    assert ":domain:(human agenote)" in zsh
    assert "human,agenote" not in zsh
    assert "(-h --help)-h[显示帮助]" in zsh
    assert "(-h --help)--help[显示帮助]" in zsh
    assert "scan-memories)" in zsh


def test_static_completion_scripts_match_generator():
    root = Path(__file__).resolve().parents[1] / "completions"
    for shell, filename in (
        ("bash", "agenote.bash"),
        ("fish", "agenote.fish"),
        ("zsh", "_agenote"),
    ):
        assert (root / filename).read_text(encoding="utf-8") == generate(shell)


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
