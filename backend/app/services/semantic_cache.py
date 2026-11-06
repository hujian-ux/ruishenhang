"""M27 · 语义缓存骨架（蓝图第 38 章）。

诚实边界（避免把字面相似度冒充语义向量）：
  - **不接真 embedding**：本地没有可用的向量模型，也不想引入 HF 下载/推理依赖。
    这里用「归一化后字符 3-gram Jaccard 字面相似度」近似判断 query 是否近似重复。
  - ``stats()`` 里明确标注 ``similarity="char_3gram_jaccard"``，并附 note 说明不等价语义向量。
  - 因此它只适合拦「几乎同措辞」的重复提问；同义改写/跨语言不会命中，这是已知限制。

机制：TTL 过期作废、容量上限用 FIFO 淘汰、命中/未命中计数供命中率统计。
存储复用 ``_m27_store.Store``（进程内字典 + RLock，重启即丢，与项目「无 PG 全内存」口径一致）。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from app.core.config import settings
from app.services._m27_store import Store, new_id, now

_CACHE = Store("semantic_cache")
_HITS = 0
_MISSES = 0

_WS_RE = re.compile(r"\s+")


def normalize(text: str) -> str:
    """归一化：去首尾、折叠/移除所有空白、转小写。为 3-gram 比较消除排版噪声。"""
    return _WS_RE.sub("", str(text or "").strip().lower())


def _grams(norm: str) -> set:
    """字符 3-gram 集合；不足 3 字的串退化为整串单元素。"""
    if not norm:
        return set()
    if len(norm) < 3:
        return {norm}
    return {norm[i:i + 3] for i in range(len(norm) - 2)}


def _jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 1.0
    union = a | b
    return len(a & b) / len(union) if union else 0.0


def _evict() -> None:
    """容量上限：超过 SEMANTIC_CACHE_MAX_ENTRIES 时按插入顺序 FIFO 淘汰最旧条目。"""
    max_entries = max(1, int(settings.SEMANTIC_CACHE_MAX_ENTRIES))
    rows = _CACHE.list(newest_first=False, limit=10 ** 6)  # 插入顺序 = 最旧在前
    while len(rows) > max_entries:
        _CACHE.delete(rows[0]["id"])
        rows.pop(0)


def put(query: str, answer: str, agent_id: str = "", meta: dict | None = None) -> dict:
    """写入一条缓存。禁用时（SEMANTIC_CACHE_ENABLED=0）不落库，仅返回说明。"""
    if not settings.SEMANTIC_CACHE_ENABLED:
        return {"stored": False, "query": str(query or ""),
                "reason": "SEMANTIC_CACHE_ENABLED=0，缓存未启用"}
    norm = normalize(query)
    row: Dict[str, Any] = {
        "id": new_id("sc"),
        "query": str(query or ""),
        "norm": norm,
        "grams": sorted(_grams(norm)),       # 存成有序 list，读回时再转 set
        "answer": str(answer or ""),
        "agent_id": str(agent_id or ""),
        "meta": dict(meta or {}),
        "created_at": now(),                 # 用本模块可 patch 的 now()，便于 TTL 测试
    }
    stored = _CACHE.put(row)
    _evict()
    out = dict(stored)
    out["stored"] = True
    return out


def get(query: str, agent_id: str = "") -> dict | None:
    """按字符 3-gram Jaccard 相似度查缓存；>= 阈值且未过期且同 agent 命名空间才命中。

    返回命中条目（含 answer / meta / similarity），未命中返回 None。
    TTL 过期条目惰性删除并计入未命中。
    """
    global _HITS, _MISSES
    if not settings.SEMANTIC_CACHE_ENABLED:
        return None
    norm = normalize(query)
    qg = _grams(norm)
    aid = str(agent_id or "")
    best: Optional[Dict[str, Any]] = None
    best_sim = 0.0
    ttl = float(settings.SEMANTIC_CACHE_TTL_S)
    threshold = float(settings.SEMANTIC_CACHE_THRESHOLD)
    current = now()
    for row in _CACHE.list(newest_first=False, limit=10 ** 6):
        if str(row.get("agent_id", "")) != aid:
            continue
        if current - float(row.get("created_at", 0)) > ttl:
            _CACHE.delete(row["id"])          # 过期作废
            continue
        sim = _jaccard(qg, set(row.get("grams") or []))
        if sim >= threshold and sim > best_sim:
            best, best_sim = row, sim
    if best is not None:
        _HITS += 1
        out = dict(best)
        out["similarity"] = round(best_sim, 4)
        out["hit"] = True
        return out
    _MISSES += 1
    return None


def stats() -> dict:
    """缓存统计；明确 similarity 实现与「未接 embedding」的边界。"""
    total = _HITS + _MISSES
    return {
        "entries": _CACHE.count(),
        "hits": _HITS,
        "misses": _MISSES,
        "hit_rate": round(_HITS / total, 4) if total else 0.0,
        "threshold": float(settings.SEMANTIC_CACHE_THRESHOLD),
        "enabled": bool(settings.SEMANTIC_CACHE_ENABLED),
        "similarity": "char_3gram_jaccard",
        "ttl_s": int(settings.SEMANTIC_CACHE_TTL_S),
        "max_entries": int(settings.SEMANTIC_CACHE_MAX_ENTRIES),
        "note": "字符 3-gram Jaccard 字面相似度，未接 embedding 模型，不等价语义向量；"
                "仅能拦近似同措辞的重复提问。",
    }


def clear() -> None:
    """清空缓存并重置命中/未命中计数。"""
    global _HITS, _MISSES
    _CACHE.clear()
    _HITS = 0
    _MISSES = 0


def reset_state() -> None:
    """测试隔离用：等价于 clear()（语义缓存无其它状态）。"""
    clear()
