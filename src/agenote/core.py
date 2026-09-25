# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT

"""kb_core — 知识库核心模块：常量、工具函数、索引管理、Org 解析、搜索辅助"""

import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import NoReturn

from agenote import config
from agenote.safeio import atomic_write, restore_text_files, KBLockTimeoutError

# ═══════════════════════════════════════════════════════════════════════════════
# 配置常量 — 默认值与覆盖入口见 agenote/config.py SCHEMA 与 ~/.config/agenote/config.toml
# 优先级：环境变量 > config.toml > SCHEMA 默认
# ═══════════════════════════════════════════════════════════════════════════════

# ── 路径 ──────────────────────────────────────────────────────────────────────
KB_ROOT = config.get_path("paths", "kb_root")  # 知识库根目录
AGENOTE_DIR_NAME = str(config.get("paths", "agenote_dir"))  # agent 域子目录名
KB_EXPERIENCES = KB_ROOT / "experiences"  # 经验卡片存储目录
KB_MEMORY = KB_ROOT / "MEMORY.org"  # 记忆文件（feedback/project/reference）
KB_MEMORIES = KB_ROOT / "memories"  # 记忆子目录
KB_PROJECTS = KB_MEMORIES / "projects"  # 项目记忆文件目录
KB_INDEX = KB_ROOT / "index.json"  # JSON 查询索引
KB_INBOX = KB_ROOT / "inbox.org"  # 快速捕获收件箱
KB_MEMORY_ARCHIVE = KB_ROOT / "MEMORY-ARCHIVE.org"  # feedback 归档文件
# 运行时产物目录（空配置 = 默认挂载位置；均不进 experiences/）
_conv_root = str(config.get("paths", "conversations_root"))
CONVERSATIONS_ROOT = (  # extract 对话输出（extract/base.py 用）
    config.get_path("paths", "conversations_root") if _conv_root else KB_ROOT / "conversations"
)
AGENOTE_ROOT = KB_ROOT / AGENOTE_DIR_NAME  # agent 域根（reconcile/viz 共用）

# type 正式性是动态的：全空 KB 以 SEED_TYPES 为初始正式集；某 type 的非归档
# 卡片数达 TYPE_PROMOTE_MIN 后即晋升正式（index.formal_types 实时计算，无持久
# 状态——聚拢归零的 type 自动失去正式性，无需降级维护）
SEED_TYPES = {"debug", "refactor", "research", "workflow", "feature", "config"}
VALID_OWNERS = {"human", "ai", "collab"}
VALID_ENTRY_TYPES = {"mistake", "note", "ascended"}
TYPE_PROMOTE_MIN = int(config.get("curation", "type_promote_min"))

# ── source_agent 体系（跨 agent 经验溯源）─────────────────────────────────────
# 记录每张卡片由哪个 agent 写入，供跨 agent 检验/健康度统计/reconcile 使用。
# 写入时从 os.environ["AGENOTE_AGENT"] 取值；人类手写的卡片留空（source_agent=""）。
# 与 type 门禁同构（SEED_TYPES/formal_types）：已知 agent = 种子集 ∪ index 中
# 出现过的 source_agent，实时计算无持久状态——新增 agent 写第一张卡即自动收录，
# 无需同步改代码（首张卡的警告是提示性的，不阻塞写入）。
SEED_AGENTS = {
    "omp",  # omp (oh-my-pi, pi 下游)
    "hermes",  # hermes-agent
    "crush",  # crush agent
    "opencode",  # opencode fork
    "mimocode",  # MiMoCode（opencode fork）
    "claude-code",  # claude-code
    "zcode",  # zcode agent (GLM-based)
    "pi-dream",  # dream 工作流产生的卡片（系统生成）
    "pi-distill",  # distill 工作流产生的卡片（系统生成）
}

# AGENOTE_AGENT 环境变量名（各 agent 的 MCP 启动入口需设置）
AGENT_ENV_VAR = "AGENOTE_AGENT"
# 兜底默认值：未设置环境变量时（如人类直接 kb add）记为 omp，保持向后兼容。
# strip + or 兜底：配置/env 给了纯空白时回落 "omp"，避免 SOURCE_AGENT 写成空白串。
DEFAULT_AGENT = str(config.get("agent", "default_name")).strip() or "omp"


def default_agent() -> str:
    """读取当前调用者所属 agent 名。

    优先取 AGENOTE_AGENT 环境变量；缺失时回退配置 [agent].default_name（默认 "omp"）。
    空/只空白视为未设置（回退默认值），避免 SOURCE_AGENT 写成空串。
    """
    val = str(config.get("agent", "default_name")).strip()
    return val or DEFAULT_AGENT


# ── reconcile 噪声过滤（系统消息/工具提示，非用户知识）──────────────────────
# reconcile 写入层用；dream 复用避免重复统计。
# 覆盖 TodoWrite、background-task、system-reminder、checkpoint、command-* 等
# "元消息"——它们源自 harness 注入而非用户的真实经验。
# 另覆盖 agent prompt 模板的结构化标记（oh-my-pi 的 [CONTEXT]/[GOAL]/[DOWNSTREAM]/
# [REQUEST] 框架、harness 的 [SYSTEM NOTIFICATION]）——它们是 agent 生成的元结构，
# 不是人类经验；常以 `USER: [CONTEXT]: ...` 形态出现在 content 开头。
NOISE_MARKERS = re.compile(
    r"<system-reminder>|<command-instruction>|<command-name>|"
    r"<skill-instruction>|<auto-slash-command>|"
    r"<task-notification>|<subagent-message>|"
    r"\[search-mode\]|\[analyze-mode\]|\[SYSTEM DIRECTIVE"
    r"|\[CONTEXT\]|\[GOAL\]|\[DOWNSTREAM\]|\[REQUEST\]"
    r"|\[SYSTEM NOTIFICATION\]|\[OUT-OF-BAND USER MESSAGE"
    r"|Continuing toward your standing goal"
    r"|TodoWrite|BACKGROUND TASK|OMO_INTERNAL_INITIATOR|"
    r"delegate_task|subagent_type|run_in_background|"
    r"load_skills|checkpoint|MANDATORY",
    re.IGNORECASE,
)
NOISE_MIN_LEN = int(config.get("reconcile", "min_fact_len"))  # <此长度的事实视为无信息量
NOISE_SCAN_CHARS = int(config.get("reconcile", "noise_scan_chars"))  # 噪声标记扫描窗口（USER 提问区）


def is_noise_fact(fact: dict) -> bool:
    """判别 reconcile 事实是否为元消息/工具提示噪声。

    元消息（TodoWrite 提示、[search-mode]、system-reminder、checkpoint 等）源自
    harness 注入，不是用户的真实经验。它们常以 `USER: <元消息>\\n\\nASSISTANT: <真实回复>`
    的形态出现——整条 content 可能很长（ASSISTANT 段有内容），但**用户提问的开头**
    是元消息。因此按全文长度做密度阈值（旧 /100、/300）会漏检。

    判定规则（任一即噪声）：
    1. content+title 总长 < NOISE_MIN_LEN（信息量不足）
    2. title 本身命中 NOISE_MARKERS（纯元消息标题，如 `[search-mode]`）
    3. content 的**开头 NOISE_SCAN_CHARS 字符**（USER 提问区）命中 ≥1 个 NOISE_MARKERS
       ——真实对话的 marker 常出现在 assistant 回复中段（被保留），只有用户提问
       本身是元消息时才判噪。
    """
    content = fact.get("content", "") or ""
    title = fact.get("title", "") or ""
    if len(content) + len(title) < NOISE_MIN_LEN:
        return True
    if NOISE_MARKERS.search(title):
        return True
    return bool(NOISE_MARKERS.search(content[:NOISE_SCAN_CHARS]))


# ── secret 门禁（memory import/export 共用单一清单）───────────────────────────
# 高置信密钥前缀子集（v1 不承诺完备，宁可误拦）。命中只记类别名，不记值。
# 同一类别多条正则取历史双份清单的并集（较宽者）；改清单只改这一处。
SECRET_PATTERNS: list[tuple[str, "re.Pattern[str]"]] = [
    ("anthropic_key", re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}")),
    ("openai_key", re.compile(r"sk-(?:proj|live|test)-[A-Za-z0-9_\-]{8,}")),
    ("openai_key", re.compile(r"sk-[A-Za-z0-9]{16,}")),
    ("github_token", re.compile(r"gh[pousr]_[A-Za-z0-9_]{8,}")),
    ("github_token", re.compile(r"github_pat_[A-Za-z0-9_]{8,}")),
    ("gitlab_token", re.compile(r"glpat-[A-Za-z0-9_\-]{8,}")),
    ("slack_token", re.compile(r"xox[abprs]\-[A-Za-z0-9\-]{8,}")),
    ("aws_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("google_key", re.compile(r"AIza[0-9A-Za-z_\-]{10,}")),
    ("hf_token", re.compile(r"hf_[A-Za-z0-9]{8,}")),
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
]


def scan_secret_categories(text: str) -> list[str]:
    """扫文本命中的密钥类别（排序去重；只回类别名，永不回值）。"""
    return sorted({name for name, rx in SECRET_PATTERNS if rx.search(text)})


def _secret_scan_enabled() -> bool:
    """[memories].secret_scan_enabled 统一取值（env 覆盖以字符串抵达）。"""
    raw = config.get("memories", "secret_scan_enabled")
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def gate_secret_write(text: str, what: str, allow: bool = False) -> None:
    """写入侧 secret 门禁：命中高置信密钥前缀即拒写；--allow-secret 显式豁免。

    与 import/export 共用 SECRET_PATTERNS 与 [memories].secret_scan_enabled——
    SSOT 内容会经 context 注入每轮外发、经 export 投影进宿主，门禁只在外围
    入口（import/export）挡等于留缺口：秘密直接写入 MEMORY.org/卡片后即全量
    放行。报错只含类别名，不回显值。
    """
    if allow or not _secret_scan_enabled():
        return
    cats = scan_secret_categories(text)
    if cats:
        die(f"{what}被 secret 门禁拦截（类别: {', '.join(cats)}，不回显值）；"
            f"确认非密钥请加 --allow-secret")


def warn_secret_write(text: str, what: str) -> None:
    """捕获通道的温和门禁：inbox 是暂存草稿层，命中只警告不拦截。

    inbox.org 不进注入/投影通道，归档为卡片时 inbox-archive 有硬门禁兜底——
    快速捕获路径若误拦会打断记录动作，故只提示。
    """
    if not _secret_scan_enabled():
        return
    cats = scan_secret_categories(text)
    if cats:
        print(
            f"警告: {what}命中 secret 类别 {', '.join(cats)}（不回显值）；"
            f"inbox-archive 归档时会被硬门禁拦截",
            file=sys.stderr,
        )


# ── 阈值 ──────────────────────────────────────────────────────────────────────
DEFAULT_LIST_COUNT = 20  # kb list 默认显示条数（CLI --limit 覆盖，不进配置文件）

# ── agenote 权重系统 ──────────────────────────────────────────────────────────
HUMAN_DEFAULT_WEIGHT = float(config.get("weights", "human_default"))  # 人类卡片默认检索权重
AGENT_DEFAULT_WEIGHT = float(config.get("weights", "agent_default"))  # agent 卡片默认检索权重
WEIGHT_USAGE_BONUS = float(config.get("weights", "usage_bonus"))  # 每次 touch 的权重提升系数
WEIGHT_USAGE_CAP = int(config.get("weights", "usage_cap"))  # 使用次数提升上限（×bonus）
WEIGHT_STALE_PENALTY = float(config.get("weights", "stale_penalty"))  # 超 STALE_DAYS 未用的惩罚系数

# 记忆一级节：deprecated 是生命周期终态（语义特例，排末尾），其余五节为 N1 五类型
# 记忆类型（memory --type 补全与校验从 MEMORY_TYPES 派生，不在此重复罗列）。
MEMORY_TYPES = ["user", "feedback", "project", "environment", "reference"]
MEMORY_SECTIONS = [*MEMORY_TYPES, "deprecated"]

# 每个模板是一个行列表，用于 cmd_add 生成新卡片
CARD_TEMPLATES = {
    "mistake": [
        "** 执行过程",
        None,  # 占位符：运行时替换为 body 或默认内容
        "",
        "** 关键发现",
        "*** 下次开始前自检",
        "",
        "** 难点与坑点 :difficulties:",
        "",
        "** 经验教训 :lessons:",
        "",
        "** 相关链接",
        "",
        "** AI 建议 :ai_notes:",
    ],
    "note": [
        "** 执行过程",
        None,
        "",
        "** 关键发现",
        "",
        "** 难点与坑点 :difficulties:",
        "",
        "** 经验教训 :lessons:",
        "",
        "** 相关链接",
        "",
        "** AI 建议 :ai_notes:",
    ],
    "ascended": [
        "** 执行过程",
        None,
        "",
        "** 关键发现",
        "*** 需要新增或修补的规则",
        "",
        "** 难点与坑点 :difficulties:",
        "",
        "** 经验教训 :lessons:",
        "",
        "** 相关链接",
        "",
        "** AI 建议 :ai_notes:",
    ],
    "default": [
        "** 执行过程",
        None,  # 占位符：运行时替换为 body 或 "1. "
        "",
        "** 难点与坑点 :difficulties:",
        "",
        "** 经验教训 :lessons:",
        "",
        "** 相关链接",
        "",
        "** AI 建议 :ai_notes:",
    ],
}

# 各 entry_type 的默认 body 占位内容
ENTRY_BODY_DEFAULTS = {
    "mistake": "*** 原始问题\n\n*** 用户纠错反馈\n\n*** 这次到底错在哪里\n\n*** 最终正确处理\n",
    "note": "*** 事项内容\n\n*** 为什么值得长期保留\n\n*** 适用场景与例外\n\n*** 后续行动\n",
    "ascended": "*** 前几轮失败的根因\n\n*** 检索过的知识源\n\n*** 核对过的真实文件或输出\n\n*** 最终采用的最强方案\n",
    "default": "1. ",
}


# ═══════════════════════════════════════════════════════════════════════════════
# KBContext — 路径上下文封装（人类 / agenote 共享实现的基础）
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class KBContext:
    """封装一个知识库域的全部路径。

    人类域用 default_context()，agent 域用 agenote_context()。
    所有 cmd_* 与辅助函数接受可选 ctx 参数，None 时回退到 default_context()。
    """

    name: str  # 域名（"human" / "agenote"）
    root: Path  # 该域根目录
    experiences: Path  # experiences/ 目录
    memories: Path  # memories/ 目录
    projects: Path  # memories/projects/ 目录
    memory_org: Path  # MEMORY.org 路径
    memory_archive: Path  # MEMORY-ARCHIVE.org 路径
    index: Path  # index.json 路径
    inbox: Path  # inbox.org 路径
    is_human: bool = True  # 是否人类域（影响 MEMORY 模板文案、curate 基础权重选择）
    default_weight: float = 1.5  # 该域卡片默认检索权重
    agent_name: str = ""  # 写入卡片时打的 source_agent 标签（default_context 留空）


def default_context() -> KBContext:
    """人类知识库上下文（KB_ROOT 根，权重 1.5）。

    人类域的 agent_name 留空（""），区分"人手写"与"agent 写"。
    """
    return KBContext(
        name="human",
        root=KB_ROOT,
        experiences=KB_EXPERIENCES,
        memories=KB_MEMORIES,
        projects=KB_PROJECTS,
        memory_org=KB_MEMORY,
        memory_archive=KB_MEMORY_ARCHIVE,
        index=KB_INDEX,
        inbox=KB_INBOX,
        is_human=True,
        default_weight=HUMAN_DEFAULT_WEIGHT,
        agent_name="",
    )


def agenote_context(agent_name: str | None = None) -> KBContext:
    """agenote 上下文（KB_ROOT/agenote 子目录，权重 1.0）。

    首次调用不创建目录——由 cmd_agenote_init / ensure_dirs(ctx) 负责。

    agent_name 决定该上下文写入卡片时的 SOURCE_AGENT 标签：
    - 显式传参时用传入值（便于 dream/distill 等系统工作流标记自身）
    - 否则读 AGENOTE_AGENT 环境变量（MCP 启动入口设置）
    - 都缺失时回退 DEFAULT_AGENT（"omp"），保持向后兼容
    """
    root = AGENOTE_ROOT
    return KBContext(
        name="agenote",
        root=root,
        experiences=root / "experiences",
        memories=root / "memories",
        projects=root / "memories" / "projects",
        memory_org=root / "MEMORY.org",
        memory_archive=root / "MEMORY-ARCHIVE.org",
        index=root / "index.json",
        inbox=root / "inbox.org",
        is_human=False,
        default_weight=AGENT_DEFAULT_WEIGHT,
        agent_name=agent_name if agent_name is not None else default_agent(),
    )


def _build_template(entry_type: str, body: str) -> list[str]:
    """根据 entry_type 生成对应的模板章节。

    模板定义在 CARD_TEMPLATES 常量中，此处查找对应模板并填充 body。
    """
    # 选择模板：优先精确匹配 entry_type，否则用 default
    template = CARD_TEMPLATES.get(entry_type, CARD_TEMPLATES["default"])
    # 选择默认 body 内容
    default_body = ENTRY_BODY_DEFAULTS.get(entry_type, ENTRY_BODY_DEFAULTS["default"])

    result = []
    for line in template:
        if line is None:
            # None 是 body 占位符：运行时替换为实际内容
            result.append(body or default_body)
        else:
            result.append(line)
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════════════════════════


class PublicError(ValueError):
    """可安全向 CLI 用户展示的错误。"""


class ReconcileIndexError(PublicError):
    """reconcile 索引损坏或结构非法。"""


def safe_error_message(exc: BaseException) -> str:
    """只向公共边界暴露受控错误或异常类型，不转发内部异常正文。"""
    if isinstance(exc, PublicError):
        return str(exc)
    if isinstance(exc, KBLockTimeoutError):
        # 锁超时消息由代码自生成（等待时长 + 持锁提示），是可操作的用户提示
        # 而非内部信息，完整透出；其余未知异常维持脱敏。
        return str(exc)
    return f"操作失败（{type(exc).__name__}）"


def die(msg: str) -> NoReturn:
    """打印错误信息并退出。"""
    print(f"错误: {msg}", file=sys.stderr)
    sys.exit(1)


def validate_category(category: str) -> None:
    """拒绝会逃逸 experiences/<category>/ 或注入换行的类别名。"""
    if "/" in category or "\\" in category or ".." in category:
        die(f"类别名不能包含路径分隔符或 '..': {category}")
    # category 会进入重命名后的文件名（update --type），换行会产出非法文件名。
    if "\n" in category or "\r" in category:
        die(f"类别名不能包含换行符: {category}")


def now() -> str:
    """返回当前时间字符串，格式：2026-05-05 一 15:30"""
    return datetime.now().strftime("%Y-%m-%d %a %H:%M")


def today() -> str:
    """返回当前日期字符串，格式：2026-05-05"""
    return datetime.now().strftime("%Y-%m-%d")


def timestamp_id() -> str:
    """生成时间戳 ID，格式：20260505-153000"""
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _init_memory_template_for_ctx(ctx: "KBContext") -> None:
    """为指定 ctx 生成 MEMORY.org 模板（参数化版 _init_memory_template）。

    人类域与 agent 域的 MEMORY 文案略有差异（feedback 节语义不同）。
    """
    date_str = datetime.now().strftime("%Y-%m-%d %a")
    sections = [f"#+title: MEMORY-{ctx.name}", f"#+date: [{date_str}]"]
    sections.append("")
    sections.append("#+BEGIN_COMMENT")
    sections.append(f"MEMORY.org — {ctx.name} 记忆索引")
    sections.append("")
    sections.append("设计原则：")
    sections.append("1. 记忆只存储无法从当前代码/项目状态推导的信息")
    if ctx.is_human:
        sections.append("2. feedback 记录用户的行为偏好和工作癖好")
    else:
        sections.append("2. feedback 记录用户对 agent 工作方式的偏好")
    sections.append("3. project 节索引行登记项目（PATH/FILE），条目以 :PROJECT: 分区键按工作区隔离")
    sections.append("4. 记忆是时间点观察，不是实时状态——引用前先验证")
    sections.append("#+END_COMMENT")
    for sec in MEMORY_SECTIONS:
        sections.append("")
        sections.append(f"* {sec}")
    atomic_write(ctx.memory_org, "\n".join(sections) + "\n")


def ensure_dirs(ctx: "KBContext | None" = None) -> None:
    """确保知识库目录和基础文件存在。

    自愈机制：所有目录和模板文件在缺失时自动重建。
    删除任意文件或整个 KB_ROOT 后重新运行 kb 命令即可恢复骨架。
    注意：仅重建结构，不恢复卡片内容。
    """
    ctx = ctx or default_context()
    # ── 目录 ────────────────────────────────────────────────────────────────
    ctx.experiences.mkdir(parents=True, exist_ok=True)
    ctx.memories.mkdir(parents=True, exist_ok=True)
    ctx.projects.mkdir(parents=True, exist_ok=True)

    # ── inbox.org ──────────────────────────────────────────────────────────
    if not ctx.inbox.exists():
        atomic_write(ctx.inbox, f"#+title: inbox\n#+date: [{now()}]\n\n")

    # ── MEMORY.org（含所有标准节）──────────────────────────────────────────
    if not ctx.memory_org.exists():
        _init_memory_template_for_ctx(ctx)

    # ── index.json（空索引）────────────────────────────────────────────────
    if not ctx.index.exists():
        from agenote.index import _save_index  # lazy：core 顶层不依赖 index（避免循环）

        _save_index({"version": 1, "updated": "", "total": 0, "cards": []}, ctx)


# ═══════════════════════════════════════════════════════════════════════════════
# 子命令: add — 添加经验卡片
# ═══════════════════════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════════════════════
# 状态机阈值
# ═══════════════════════════════════════════════════════════════════════════════

STALE_DAYS = int(config.get("curation", "stale_days"))  # memory/卡片「陈旧」统一阈值（天）
ARCHIVE_THRESHOLD_DAYS = int(config.get("curation", "archive_days"))  # stale → archived 阈值（天）
DEDUP_THRESHOLD = float(config.get("curation", "dedup_threshold"))  # 去重相似度阈值（4 处硬编码的单一来源）
DEDUP_CATEGORY_BONUS = float(config.get("weights", "dedup_category_bonus"))  # 去重：category 相同加成
DEDUP_TECH_BONUS = float(config.get("weights", "dedup_tech_bonus"))  # 去重：tech 相同加成
# 新卡片默认字段值（cmd_add 与 index._card_dict 共用的单一来源）
DEFAULT_CATEGORY = str(config.get("add", "default_category"))
DEFAULT_TYPE = str(config.get("add", "default_type"))
DEFAULT_OWNER = str(config.get("add", "default_owner"))


def build_fingerprint_line(
    category: str, type_: str, owner: str, tech: str = "", entry_type: str = ""
) -> str:
    """构建 :END: 后的 fingerprint 标签行（单一真相源：add 组装与 lint 校验共用）。

    格式 ``:category:type:owner:tech:entry_type::``；tech 与 category 相同则省略，
    entry_type 为空则省略——lint 必须按同一口径判定，否则会把合规卡片报成
    「字段数不为 5」。
    """
    parts = [category, type_, owner]
    if tech and tech != category:
        parts.append(tech)
    if entry_type:
        parts.append(entry_type)
    return ":" + ":".join(parts) + "::"
PROJECT_CURATE_DAYS = int(config.get("curation", "project_curate_days"))  # memory 项目建议策展阈值（天）
VALID_STATUSES = {"done", "stable", "stale", "archived"}


# ═══════════════════════════════════════════════════════════════════════════════
# 卡片级操作
# ═══════════════════════════════════════════════════════════════════════════════


def touch_card(
    filepath: Path, field: str = "LAST_USED", ctx: "KBContext | None" = None,
    session: str | None = None, count: bool = True,
) -> None:
    """更新卡片 PROPERTIES 中的指定时间戳字段，同步更新 index.json。

    Args:
        filepath: 卡片文件路径
        field: 要更新的字段名（LAST_USED 或 LAST_VERIFIED）
        ctx: 知识库上下文（None 时用 default_context）
        session: 会话幂等键（S3）。同卡同 session 首次 USAGE_COUNT+1，
            重复调用只刷时间戳；None 时保持旧语义（每次调用 +1）。
        count: 是否递增 USAGE_COUNT。cmd_touch 默认路径连续调两次
            （LAST_USED + LAST_VERIFIED），第二次必须 count=False，
            否则单次 touch 无 session 时 +2。
    """
    ctx = ctx or default_context()
    if not filepath.exists():
        return
    content = filepath.read_text(encoding="utf-8")
    from agenote.index import _load_index
    from agenote.orgserde import parse_org_prop, set_org_prop

    # 先验证将要读改写的持久状态，避免卡片已写而索引失败。
    index = _load_index(ctx)
    # S3 会话幂等：只有「确证本 session 已计过」才跳过递增；记录缺失或
    # 损坏一律视为未计（宁可多计一次，不少计）。
    counted = _touch_session_counted(ctx, filepath.name, session) if session else False
    ts = f"[{now()}]"
    content = set_org_prop(content, field, ts)
    if count and not counted:
        # 递增 USAGE_COUNT（留痕核心：每次 touch 表示该卡片被实际使用）
        try:
            new_count = int(parse_org_prop(content, "USAGE_COUNT") or 0) + 1
        except ValueError:
            new_count = 1
        content = set_org_prop(content, "USAGE_COUNT", str(new_count))
    original = filepath.read_bytes()
    original_index = ctx.index.read_bytes() if ctx.index.exists() else None
    try:
        from agenote.index import _save_index, _upsert_card

        atomic_write(filepath, content)
        _upsert_card(index, filepath, ctx)
        _save_index(index, ctx)
    except BaseException as exc:
        failures = restore_text_files(
            {filepath: original, ctx.index: original_index}
        )
        if failures:
            raise RuntimeError(
                f"touch 回滚失败（{', '.join(failures)}）"
            ) from exc
        raise
    if session and count and not counted:
        _touch_session_record(ctx, filepath.name, session)


def _touch_session_path(ctx: "KBContext") -> Path:
    """会话计数记录文件（agent 域小文件，与卡片同域）。"""
    return ctx.root / ".touch-sessions.json"


def _touch_session_counted(ctx: "KBContext", card_name: str, session: str) -> bool:
    """本 session 是否已为该卡计过数；任何读取失败都返回 False（不少计）。"""
    try:
        rec = json.loads(_touch_session_path(ctx).read_text(encoding="utf-8"))
        seen = rec.get(card_name) or []
        return session in seen if isinstance(seen, list) else False
    except (OSError, ValueError):
        return False


def _touch_session_record(ctx: "KBContext", card_name: str, session: str) -> None:
    """记录本 session 已计数；失败静默（下次调用重计，至多多计一次）。"""
    # ponytail: 每卡只留最近 32 个 session，防唯一 session 高频写入撑大文件
    try:
        path = _touch_session_path(ctx)
        try:
            rec = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(rec, dict):
                rec = {}
        except (OSError, ValueError):
            rec = {}
        seen = [s for s in (rec.get(card_name) or []) if s != session]
        rec[card_name] = [session, *seen][:32]
        atomic_write(path, json.dumps(rec, ensure_ascii=False, indent=1))
    except OSError:
        pass


def _resolve_card(id_or_path: str, ctx: "KBContext | None" = None) -> Path | None:
    """通过精确 ID、路径或文件名片段解析卡片路径。"""
    from agenote.orgserde import parse_org_prop

    if not isinstance(id_or_path, str) or id_or_path.strip() in {"", ".", ".."}:
        return None

    ctx = ctx or default_context()

    def in_experiences(path: Path) -> bool:
        try:
            experiences = ctx.experiences.resolve(strict=True)
            # experiences 根本身不能是符号链接；否则 resolve() 会把跨域目标
            # 重新定义成“合法根”，绕过 containment。
            if ctx.experiences.is_symlink():
                return False
            return path.resolve().is_relative_to(experiences)
        except (OSError, RuntimeError):
            return False

    p = Path(id_or_path)
    if p.is_symlink():
        return None
    try:
        is_file = p.is_file()
    except OSError:
        is_file = False
    if is_file:
        if p.suffix != ".org" or not in_experiences(p):
            return None
        try:
            p.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return None
        return p

    # 卡片可能已被重命名，不能只靠文件名 glob；遍历本域全部 Org 卡片，
    # 先读取 PROPERTIES 抽屉里的精确 ID，再保留唯一文件名片段回退。
    candidates = [
        c
        for c in ctx.experiences.rglob("*.org")
        if not c.is_symlink() and c.is_file() and in_experiences(c)
    ]
    exact: list[Path] = []
    readable: list[Path] = []
    for candidate in candidates:
        try:
            content = candidate.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        readable.append(candidate)
        if parse_org_prop(content, "ID") == id_or_path:
            exact.append(candidate)
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        return None

    fallback = [c for c in readable if id_or_path in c.name]
    if len(fallback) == 1:
        return fallback[0]
    if len(fallback) > 1:
        return None
    return None
