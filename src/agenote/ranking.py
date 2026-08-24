# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT
"""agenote.ranking — BM25 检索排序（Okapi）+ 中英混合分词。

移植自 claude-obsidian wiki-retrieve 的纯 stdlib BM25（裁剪版）：
- 分词：NFKC + casefold；CJK 连续段 1/2/3-gram（无分词器也能让单字/长词
  查询命中中文文档），拉丁段按词边界切分
- 打分：标准 Okapi BM25（k1/b 由调用方传入），IDF 加 1 平滑避免负值
- 不做持久倒排索引：agenote 百级卡片进程内即时计算毫秒级完成，
  claude-obsidian 的失效链/增量更新整个省掉（规模再上一个数量级才需要）

k1/b/title_boost 等参数在 config [search] 节配置，由 search.py 读入传入，
本模块不依赖 config（保持纯函数可测试）。
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter

# CJK 判定范围（Han + Hiragana + Katakana + Hangul，与 claude-obsidian 同款）
_CJK_RANGES = (
    (0x4E00, 0x9FFF),  # CJK Unified Ideographs
    (0x3400, 0x4DBF),  # Extension A
    (0xF900, 0xFAFF),  # Compatibility Ideographs
    (0x3040, 0x30FF),  # Hiragana + Katakana
    (0xAC00, 0xD7AF),  # Hangul Syllables
    (0x3130, 0x318F),  # Hangul Compatibility Jamo
)

_WORD_RE = re.compile(r"[a-z0-9']+")


def _is_cjk(ch: str) -> bool:
    cp = ord(ch)
    return any(lo <= cp <= hi for lo, hi in _CJK_RANGES)


def _cjk_ngrams(s: str) -> list[str]:
    """CJK 连续段的 1/2/3-gram（2/3-gram 是主力，1-gram 兜底单字查询）。"""
    n = len(s)
    out = list(s)
    if n >= 2:
        out.extend(s[i : i + 2] for i in range(n - 1))
    if n >= 3:
        out.extend(s[i : i + 3] for i in range(n - 2))
    return out


def _iter_segments(text: str) -> list[tuple[str, bool]]:
    """把文本切成 (连续片段, 是否CJK) 列表。"""
    segs: list[tuple[str, bool]] = []
    cur: list[str] = []
    cur_cjk: bool | None = None
    for ch in text:
        c = _is_cjk(ch)
        if cur_cjk is None or c == cur_cjk:
            cur.append(ch)
        else:
            segs.append(("".join(cur), cur_cjk))
            cur = [ch]
        cur_cjk = c
    if cur:
        segs.append(("".join(cur), bool(cur_cjk)))
    return segs


def tokenize(text: str) -> list[str]:
    """中英混合分词：CJK 段 1/2/3-gram，拉丁段按 [a-z0-9']+ 词切分。"""
    text = unicodedata.normalize("NFKC", text).casefold()
    tokens: list[str] = []
    for seg, is_cjk in _iter_segments(text):
        if is_cjk:
            tokens.extend(_cjk_ngrams(seg))
        else:
            tokens.extend(_WORD_RE.findall(seg))
    return tokens


class BM25:
    """Okapi BM25：fit 一次语料后对任意 key 反复打分。

    IDF = log((N - df + 0.5) / (df + 0.5) + 1)，加 1 平滑保证非负且
    常见词不被惩罚成负贡献。
    """

    def __init__(
        self,
        docs_tokens: dict[str, list[str]],
        k1: float = 1.5,
        b: float = 0.75,
    ) -> None:
        self.k1 = k1
        self.b = b
        self.doc_count = len(docs_tokens)
        self.doc_len = {k: len(t) for k, t in docs_tokens.items()}
        self.avgdl = (
            sum(self.doc_len.values()) / self.doc_count if self.doc_count else 0.0
        )
        self._tf: dict[str, Counter[str]] = {
            k: Counter(t) for k, t in docs_tokens.items()
        }
        df: dict[str, int] = {}
        for tokens in docs_tokens.values():
            for tok in set(tokens):
                df[tok] = df.get(tok, 0) + 1
        self.df = df

    def idf(self, term: str) -> float:
        df = self.df.get(term, 0)
        return math.log((self.doc_count - df + 0.5) / (df + 0.5) + 1)

    def score(self, key: str, query_tokens: list[str]) -> float:
        """单个文档对查询 token 序列的 BM25 得分（未知 key 返回 0）。"""
        tf_map = self._tf.get(key)
        if tf_map is None or not self.avgdl:
            return 0.0
        dl = self.doc_len[key]
        norm = 1 - self.b + self.b * dl / self.avgdl
        total = 0.0
        for term in query_tokens:
            tf = tf_map.get(term, 0)
            if not tf:
                continue
            total += self.idf(term) * tf * (self.k1 + 1) / (tf + self.k1 * norm)
        return total
