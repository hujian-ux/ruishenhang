"""M27 · 非 LLM MLOps 闭环（围绕嵌入模型 bge-m3 的真实闭环）。

蓝图第 43 章把 MLOps 列为骨架（mlops.py）：register_model 仅内存登记、drift_check
是简易 PSI、pipeline_status 四项全 done=False，**明确不谎称已训练**。本模块把
「非 LLM」那条最实在的资产——**嵌入模型（bge-m3，本地、1024 维、RAG + 语义缓存
共用）**——接成可验证的闭环：

  1. **模型登记**：``register_embedding_model`` 对模型目录做指纹（文件清单 + 体积哈希），
     经 ``mlops.register_model`` 落库（真实元数据，不是「已训练」谎言）。
  2. **数据集版本化**：``snapshot_dataset`` 给知识库/语料做内容哈希版本，供回归比对
     （改了哪批文档、是否影响检索质量，可追溯）。
  3. **回归基准**：``benchmark`` 用 golden 相似对（anchor + 候选 + 正例下标）算
     cosine → recall@k + 正负分离度；**默认走确定性 fallback 嵌入（零依赖、可单测）**，
     真模型可用时（sentence-transformers + 模型路径）自动切真嵌入，不谎报。
  4. **回归判定**：``regression_check`` 拿当前与基线 recall 比对，掉超过阈值标 regression。
  5. **状态**：``pipeline_status_v2`` 在 ``mlops.pipeline_status`` 基础上补 embedding
     闭环状态（benchmark 跑过 → embedding_eval_done=True，否则 False）。

诚实边界：本地未装 sentence-transformers 或未配模型路径时，所有评估走 fallback，
脚本/测试会明确标 ``backend="fallback"``，不会把 fallback 结果伪装成真模型指标。
"""
from __future__ import annotations

import hashlib
import math
import os
from typing import Any, Callable, Dict, List, Optional

from app.services import mlops

DEFAULT_MODEL_PATH = "D:/models/bge-m3"
DEFAULT_DIM = 1024

_EMBED_BACKEND = "fallback"   # 运行时若加载到真模型会改成 bge-m3


# --------------------------------------------------------------------------- 嵌入（可插拔）
def _fallback_embed(text: str, dim: int = 64) -> List[float]:
    """确定性字符 3-gram 哈希嵌入（零依赖、可复现）。

    仅用于在无真模型时验证闭环逻辑；相似度趋势与真嵌入一致（共享字符越多越近），
    但绝对值不代表语义质量。
    """
    grams = set()
    t = (text or "").lower()
    for i in range(len(t) - 2):
        grams.add(t[i:i + 3])
    if not grams:
        grams = {"<empty>"}
    vec = [0.0] * dim
    for g in grams:
        h = int(hashlib.md5(g.encode("utf-8")).hexdigest(), 16)
        vec[h % dim] += 1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def _real_embed(text: str, model, dim: int) -> List[float]:
    v = model.encode([text])[0]
    arr = [float(x) for x in v]
    norm = math.sqrt(sum(x * x for x in arr)) or 1.0
    return [x / norm for x in arr]


def _embed(text: str, model=None, dim: int = DEFAULT_DIM) -> List[float]:
    if model is not None:
        return _real_embed(text, model, dim)
    return _fallback_embed(text, dim=64)  # fallback 固定 64 维，仅闭环验证用


def _cosine(a: List[float], b: List[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


# --------------------------------------------------------------------------- 模型登记
def _model_fingerprint(path: str) -> str:
    """对模型目录做轻量指纹：文件清单 + 体积哈希（不读权重，快且稳定）。"""
    if not path or not os.path.isdir(path):
        return "unavailable"
    parts = []
    for root, _, files in os.walk(path):
        for f in sorted(files):
            fp = os.path.join(root, f)
            try:
                parts.append(f"{f}:{os.path.getsize(fp)}")
            except OSError:
                pass
    if not parts:
        return "empty"
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]


def register_embedding_model(path: str = DEFAULT_MODEL_PATH, dim: int = DEFAULT_DIM,
                             provider: str = "bge-m3") -> Dict[str, Any]:
    """登记嵌入模型真实元数据（指纹 + 维度 + 后端），复用 mlops 登记链。"""
    fp = _model_fingerprint(path)
    metrics = {
        "dim": dim, "provider": provider, "model_path": path,
        "fingerprint": fp, "backend": _EMBED_BACKEND,
        "note": "嵌入模型闭环：登记+数据集版本+回归基准；未谎称已训练",
    }
    return mlops.register_model(f"embedding:{provider}", f"fp-{fp[:8]}", metrics,
                                fingerprint=fp)


# --------------------------------------------------------------------------- 数据集版本化
def snapshot_dataset(name: str, items: List[Any]) -> Dict[str, Any]:
    """给知识库/语料做内容哈希版本（可追溯「改了哪批文档、是否影响检索质量」）。"""
    payload = "|".join(str(x) for x in items)
    h = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    version = f"{name}@{h}"
    return {"name": name, "version": version, "hash": h, "count": len(items)}


# --------------------------------------------------------------------------- 回归基准
def benchmark(items: List[Dict[str, Any]], *, model=None,
              dim: int = DEFAULT_DIM, k: int = 1) -> Dict[str, Any]:
    """用 golden 相似对评估嵌入检索质量。

    ``items`` 每项：``{"anchor": str, "candidates": [str...], "positive_idx": int}``。
    返回 recall@1、recall@k、正/负例平均相似度、分离度、后端标注。
    """
    global _BENCH_RAN
    _BENCH_RAN = model is not None
    n = len(items)
    hit1 = hitk = 0
    pos_sims: List[float] = []
    neg_sims: List[float] = []
    for it in items:
        anchor = _embed(it["anchor"], model, dim)
        cands = it["candidates"]
        sims = [_cosine(anchor, _embed(c, model, dim)) for c in cands]
        order = sorted(range(len(cands)), key=lambda i: sims[i], reverse=True)
        topk = set(order[:k])
        if it["positive_idx"] in topk:
            hitk += 1
        if order[0] == it["positive_idx"]:
            hit1 += 1
        pidx = it["positive_idx"]
        pos_sims.append(sims[pidx])
        for i, s in enumerate(sims):
            if i != pidx:
                neg_sims.append(s)
    recall1 = round(hit1 / n, 4) if n else 0.0
    recallk = round(hitk / n, 4) if n else 0.0
    mean_pos = round(sum(pos_sims) / len(pos_sims), 4) if pos_sims else 0.0
    mean_neg = round(sum(neg_sims) / len(neg_sims), 4) if neg_sims else 0.0
    return {
        "n": n, "recall_at_1": recall1, "recall_at_k": recallk, "k": k,
        "mean_positive_sim": mean_pos, "mean_negative_sim": mean_neg,
        "separation": round(mean_pos - mean_neg, 4),
        "backend": _EMBED_BACKEND,
        "note": "fallback 为字符 n-gram 嵌入，仅验证闭环；真模型需 sentence-transformers+模型路径",
    }


def _load_real_model(path: str = DEFAULT_MODEL_PATH, dim: int = DEFAULT_DIM):
    """尝试加载真 bge-m3；失败（未装/无路径）返回 None 并保留 fallback。"""
    global _EMBED_BACKEND
    try:
        from sentence_transformers import SentenceTransformer  # 延迟导入
        if os.path.isdir(path):
            model = SentenceTransformer(path)
            _EMBED_BACKEND = "bge-m3"
            return model
    except Exception:
        pass
    _EMBED_BACKEND = "fallback"
    return None


# --------------------------------------------------------------------------- 回归判定与状态
def regression_check(current: Dict[str, Any], baseline: Optional[Dict[str, Any]],
                     drop_threshold: float = 0.05) -> Dict[str, Any]:
    """当前基准相对基线：recall_at_1 掉超过阈值 → regression=True。"""
    if not baseline:
        return {"regression": False, "baseline": None,
                "drop": 0.0, "threshold": drop_threshold, "note": "无基线可比对"}
    drop = round((baseline.get("recall_at_1", 0.0) - current.get("recall_at_1", 0.0)), 4)
    return {
        "regression": bool(drop > drop_threshold),
        "baseline_recall_at_1": baseline.get("recall_at_1"),
        "current_recall_at_1": current.get("recall_at_1"),
        "drop": drop, "threshold": drop_threshold,
    }


def pipeline_status_v2() -> Dict[str, Any]:
    """在 mlops.pipeline_status 基础上补嵌入闭环状态（不谎称已训练）。"""
    base = mlops.pipeline_status()
    base["embedding_loop"] = {
        "registered": len(mlops.list_models()) > 0,
        "backend": _EMBED_BACKEND,
        "embedding_eval_done": _BENCH_RAN,
        "note": "嵌入模型闭环：登记+数据集版本+回归基准；真评估需 sentence-transformers+模型路径",
    }
    return base


# 是否跑过一次真模型基准（供状态展示）；fallback 下为 False。
_BENCH_RAN = False


def reset_state() -> None:
    """清空登记与基准状态，供测试隔离。"""
    mlops.reset_state()
