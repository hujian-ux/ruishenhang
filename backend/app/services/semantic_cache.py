"""M27 · 语义缓存（蓝图第 38 章）—— 生产级补全。

两层相似度：
  1. 字符 3-gram Jaccard（廉价预筛）：拦「几乎同措辞」的重复提问，**不触发 embedding 计算**。
  2. bge-m3 余弦相似度（真实语义）：当字面预筛未命中、且本机加载了 bge-m3
     （``USE_LOCAL_EMBEDDING=1``），对 query 与候选算 1024 维余弦，命中**同义改写 / 跨语言**
     的近似提问。bge-m3 不可用时（默认单体骨架），如实回退到仅字面层并 `note` 标注，
     **不冒充语义向量**。

冷热分层：每条缓存记录命中次数，命中 >= ``SEMANTIC_CACHE_HOT_HITS`` 次升为 hot 层，
FIFO 淘汰时**优先丢 cold、保 hot**（生产里热路径的向量检索本就该常驻），体现「向量库冷热分离」
的缓存侧实现；生产向量库（Qdrant）的 collection 级冷热分离属部署项，不在此模块。

存储复用 ``_m27_store.Store``（进程内字典 + RLock，重启即丢，与项目「无 PG 全内存」口径一致）。
"""
from __future__ import annotations

import math
import re
from typing import Any, Dict, List, Optional

from app.core.config import settings
from app.services._m27_store import Store, new_id, now

_CACHE = Store("semantic_cache")
_HITS = 0
_MISSES = 0

_WS_RE = re.compile(r"\s+")
# hot 阈值：命中次数达到即升热层（避免频繁重复提问每次都过模型）。
# 运行时读取 settings，保证 SEMANTIC_CACHE_HOT_HITS 改环境变量即时生效。
def _hot_hits() -> int:
    return max(1, int(getattr(settings, "SEMANTIC_CACHE_HOT_HITS", "3")))


# ── 字面预筛（廉价，零 embedding 开销） ───────────────────────────────────────
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


# ── 真实语义向量（bge-m3，惰性、可降级） ─────────────────────────────────────
def _real_embedding_available() -> bool:
    """本机是否真的加载了 bge-m3（USE_LOCAL_EMBEDDING=1 且模型可用）。"""
    return bool(settings.USE_LOCAL_EMBEDDING)


def _embed(text: str) -> Optional[List[float]]:
    """查/算 query 向量；bge-m3 不可用返回 None（调用方回退字面层）。"""
    if not _real_embedding_available():
        return None
    try:
        from app.services import knowledge
        return knowledge.embed(text)
    except Exception:
        return None


def _cosine(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _evict() -> None:
    """容量上限：超过 SEMANTIC_CACHE_MAX_ENTRIES 时淘汰。

    冷热分层：优先丢最旧的 cold 条目，hot 层尽量常驻；只有当 cold 全清空仍超容，
    才动 hot（按插入顺序 FIFO 丢最旧 hot）。
    """
    max_entries = max(1, int(settings.SEMANTIC_CACHE_MAX_ENTRIES))
    rows = _CACHE.list(newest_first=False, limit=10 ** 6)
    cold = [r for r in rows if r.get("tier") != "hot"]
    hot = [r for r in rows if r.get("tier") == "hot"]
    # 先丢 cold（最旧优先）
    while len(cold) + len(hot) > max_entries and cold:
        _CACHE.delete(cold.pop(0)["id"])
    # cold 清空后仍超容，才丢 hot
    while len(hot) > 0 and len(cold) + len(hot) > max_entries:
        _CACHE.delete(hot.pop(0)["id"])


def put(query: str, answer: str, agent_id: str = "", meta: dict | None = None) -> dict:
    """写入一条缓存。禁用时（SEMANTIC_CACHE_ENABLED=0）不落库，仅返回说明。

    同时预存 query 的 bge-m3 向量（若可用），供后续 cosine 命中；不可用则 vec=None。
    """
    if not settings.SEMANTIC_CACHE_ENABLED:
        return {"stored": False, "query": str(query or ""),
                "reason": "SEMANTIC_CACHE_ENABLED=0，缓存未启用"}
    norm = normalize(query)
    vec = _embed(query)  # 真 embedding；不可用时为 None（回退字面层）
    row: Dict[str, Any] = {
        "id": new_id("sc"),
        "query": str(query or ""),
        "norm": norm,
        "grams": sorted(_grams(norm)),       # 存成有序 list，读回时再转 set
        "vec": vec,                          # 1024 维（None=未启用 embedding）
        "answer": str(answer or ""),
        "agent_id": str(agent_id or ""),
        "meta": dict(meta or {}),
        "hits": 0,
        "tier": "cold",
        "created_at": now(),                 # 用本模块可 patch 的 now()，便于 TTL 测试
    }
    stored = _CACHE.put(row)
    _evict()
    out = dict(stored)
    out["stored"] = True
    return out


def get(query: str, agent_id: str = "") -> dict | None:
    """两层相似度查缓存：字面预筛优先，未命中再走 bge-m3 余弦。

    返回命中条目（含 answer / meta / similarity / sim_mode / tier），未命中返回 None。
    TTL 过期条目惰性删除并计入未命中。
    """
    global _HITS, _MISSES
    if not settings.SEMANTIC_CACHE_ENABLED:
        return None
    norm = normalize(query)
    qg = _grams(norm)
    aid = str(agent_id or "")
    ttl = float(settings.SEMANTIC_CACHE_TTL_S)
    lit_thr = float(settings.SEMANTIC_CACHE_THRESHOLD)
    emb_thr = float(settings.SEMANTIC_CACHE_EMBED_THRESHOLD)
    current = now()

    best: Optional[Dict[str, Any]] = None
    best_sim = 0.0
    best_mode = ""
    # 字面层候选（先扫一遍，避免无谓 embedding 计算）
    for row in _CACHE.list(newest_first=False, limit=10 ** 6):
        if str(row.get("agent_id", "")) != aid:
            continue
        if current - float(row.get("created_at", 0)) > ttl:
            _CACHE.delete(row["id"])          # 过期作废
            continue
        sim = _jaccard(qg, set(row.get("grams") or []))
        if sim >= lit_thr and sim > best_sim:
            best, best_sim, best_mode = row, sim, "char_3gram"

    # 字面层已命中（>= 阈值）直接返回，省去 embedding
    if best is not None:
        return _mark_hit(best, best_sim, best_mode)

    # 字面未命中 → 若真 embedding 可用，走 bge-m3 余弦（同义改写/跨语言）
    qvec = _embed(query)
    if qvec is not None:
        for row in _CACHE.list(newest_first=False, limit=10 ** 6):
            if str(row.get("agent_id", "")) != aid:
                continue
            if current - float(row.get("created_at", 0)) > ttl:
                continue
            rvec = row.get("vec")
            if not rvec:
                continue
            sim = _cosine(qvec, rvec)
            if sim >= emb_thr and sim > best_sim:
                best, best_sim, best_mode = row, sim, "bge-m3_cosine"

    if best is not None:
        return _mark_hit(best, best_sim, best_mode)
    _MISSES += 1
    return None


def _mark_hit(best: Dict[str, Any], sim: float, mode: str) -> dict:
    global _HITS
    _HITS += 1
    # 升热层（写回 store 持久化 tier）
    best["hits"] = int(best.get("hits", 0)) + 1
    if best["hits"] >= _hot_hits():
        best["tier"] = "hot"
    _CACHE.put(best)
    out = dict(best)
    out["similarity"] = round(sim, 4)
    out["sim_mode"] = mode
    out["hit"] = True
    return out


def stats() -> dict:
    """缓存统计；按当前是否启用真 embedding 动态标注 similarity 实现。"""
    total = _HITS + _MISSES
    rows = _CACHE.list(newest_first=False, limit=10 ** 6)
    hot = sum(1 for r in rows if r.get("tier") == "hot")
    real = _real_embedding_available()
    return {
        "entries": _CACHE.count(),
        "hits": _HITS,
        "misses": _MISSES,
        "hit_rate": round(_HITS / total, 4) if total else 0.0,
        "threshold": float(settings.SEMANTIC_CACHE_THRESHOLD),
        "embed_threshold": float(settings.SEMANTIC_CACHE_EMBED_THRESHOLD),
        "enabled": bool(settings.SEMANTIC_CACHE_ENABLED),
        "similarity": "bge-m3_cosine + char_3gram" if real else "char_3gram_jaccard",
        "semantic_layer": "bge-m3" if real else "disabled",
        "hot_entries": hot,
        "cold_entries": _CACHE.count() - hot,
        "ttl_s": int(settings.SEMANTIC_CACHE_TTL_S),
        "max_entries": int(settings.SEMANTIC_CACHE_MAX_ENTRIES),
        "hot_hits_threshold": _hot_hits(),
        "note": ("bge-m3 余弦命中同义改写/跨语言；字面 3-gram 预筛拦近似同措辞。"
                 if real else "未启用真 embedding，仅字面 3-gram 近似；同义改写不会命中（已知限制）。"),
    }


def clear() -> None:
    """清空缓存并重置命中/未命中计数。"""
    global _HITS, _MISSES
    _CACHE.clear()
    _HITS = 0
    _MISSES = 0


def reset_state() -> None:
    """测试隔离用：等价于 clear()。"""
    clear()
