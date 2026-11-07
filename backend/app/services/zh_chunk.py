"""中文特化 · 长度感知语义切分（蓝图第 43 章）。

为什么不用「一句话一块」：一句话往往太碎（三四字），检索召回的块语义不完整；
而一份公告整段没标点时又会变成超长块，embedding 末尾信息被稀释。这里做长度感知的
递归切分：先按句界断，再把连续短句合并到预算内、把超长单元按逗号/长度劈开。

不引入新依赖，纯规则 + 中文标点/数字/专名友好的长度估算（按字符数，中文 1 字≈1 token）。
"""
from __future__ import annotations

import re
from typing import List

# 句界：中英文句号/感叹/疑问/分号 + 换行；保留标点本身在块尾，便于上下文连贯。
_SENT_RE = re.compile(r"(?<=[。！？!?；;\n])")
# 次一级切分（超长句内部再切）：逗号/顿号/空格。
_SUB_RE = re.compile(r"(?<=[，,、\s])")


def _rough_len(text: str) -> int:
    """中文友好的长度估计：去空白后按字符计（中文 1 字≈1 token，比 byte 更准）。"""
    return len(re.sub(r"\s+", "", text))


def split_sentences(text: str) -> List[str]:
    """按句界断句（保留尾标点）。空段丢弃。"""
    parts = [p.strip() for p in _SENT_RE.split(text)]
    return [p for p in parts if p]


def chunk_text(text: str, max_len: int = 120, min_len: int = 12) -> List[str]:
    """长度感知中文切块。

    算法：
      1. 句界断句；
      2. 顺序合并：把连续短句拼到不超过 max_len；上一个块若 < min_len 则尽量并入下一块；
      3. 单句超 max_len：先按逗号/顿号劈，仍超则按 max_len 硬切。
    返回有序块列表（非空、去首尾空白）。
    """
    text = (text or "").strip()
    if not text:
        return []
    sents = split_sentences(text)
    if not sents:
        sents = [text]

    chunks: List[str] = []
    buf = ""
    for sent in sents:
        # 超长单句：先内部按逗号/顿号再切
        if _rough_len(sent) > max_len:
            for sub in _split_sub(sent, max_len):
                chunks.extend(_flush_or_merge(chunks, sub, max_len, min_len))
            continue
        pieces = _flush_or_merge(chunks, sent, max_len, min_len)
        chunks = pieces
    # 收尾
    if buf:
        chunks.append(buf)
    # 末块若过短，并入上一块（仅在合并后仍不超预算时，否则保留独立短块）
    if len(chunks) >= 2 and _rough_len(chunks[-1]) < min_len:
        merged = (chunks[-2] + chunks[-1]).strip()
        if _rough_len(merged) <= max_len:
            chunks[-2] = merged
            chunks.pop()
    return [c for c in chunks if c.strip()]


def _flush_or_merge(chunks: List[str], sent: str, max_len: int, min_len: int) -> List[str]:
    """把一句送进 chunks：能并入末块（不超预算）就并入，否则新起一块。"""
    if chunks and _rough_len(chunks[-1]) < min_len:
        merged = (chunks[-1] + sent).strip()
        if _rough_len(merged) <= max_len:
            chunks[-1] = merged
            return chunks
    last = chunks[-1] if chunks else ""
    if last and _rough_len(last) + _rough_len(sent) <= max_len:
        chunks[-1] = (last + sent).strip()
    else:
        chunks.append(sent.strip())
    return chunks


def _split_sub(sent: str, max_len: int) -> List[str]:
    """超长句内部切分：先按逗号/顿号，仍超则按 max_len 硬切。"""
    if _rough_len(sent) <= max_len:
        return [sent]
    subs = [s.strip() for s in _SUB_RE.split(sent) if s.strip()]
    if len(subs) <= 1:
        # 没有次级标点，按 max_len 硬切
        return [sent[i:i + max_len] for i in range(0, len(sent), max_len)]
    out: List[str] = []
    buf = ""
    for s in subs:
        if buf and _rough_len(buf) + _rough_len(s) > max_len:
            out.append(buf)
            buf = s
        else:
            buf = (buf + s).strip()
    if buf:
        out.append(buf)
    return out


def chunk_parent_content(content: str, max_len: int = 120, min_len: int = 12) -> List[str]:
    """给入库流程用的便捷封装：整篇 parent 内容切块。"""
    return chunk_text(content, max_len=max_len, min_len=min_len)
