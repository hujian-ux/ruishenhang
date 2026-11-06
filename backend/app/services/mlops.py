"""M27 · MLOps 骨架（蓝图第 43 章）。

诚实边界（**骨架，不谎称已训练**）：
  - ``register_model`` 只是进程内登记（名字/版本/指标/指纹），不是 MLflow/模型仓库，
    不产生产物、不做校验、不部署。
  - ``drift_check`` 是「简易 PSI + 均值漂移」的统计提示，不是生产漂移监控；
    ``pipeline_status`` 明确标注 feature_store / training / registry / retrain 全部
    ``done=False`` 并给出落地条件。
  - 全部状态在内存，重启即丢；未接训练流水线、特征库、调度、审批与回滚。

存储复用 ``_m27_store.Store``。
"""
from __future__ import annotations

import math
from typing import Any, Dict, List

from app.services._m27_store import Store, new_id, now, iso, audit_event

_MODELS = Store("mlops_models")
_PSI_THRESHOLD = 0.20        # PSI 常用经验阈值：>=0.2 视为显著分布漂移
_PSI_BINS = 10


def _nums(seq) -> List[float]:
    out: List[float] = []
    for x in seq or []:
        try:
            out.append(float(x))
        except (TypeError, ValueError):
            pass
    return out


def register_model(name: str, version: str, metrics: dict,
                   fingerprint: str = "") -> dict:
    """登记模型元数据（骨架）。按 ``name:version`` 去重更新，不入库真实产物。"""
    key = f"{str(name or '')}:{str(version or '')}"
    payload: Dict[str, Any] = {
        "name": str(name or ""),
        "version": str(version or ""),
        "metrics": dict(metrics or {}),
        "fingerprint": str(fingerprint or ""),
        "stage": "registered",
        "updated_at": now(),
        "updated_iso": iso(),
        "note": "仅内存登记，未接模型仓库/训练流水线/产物存储",
    }
    for row in _MODELS.list(limit=10 ** 6):
        if row.get("key") == key:
            out = _MODELS.update(row["id"], payload)
            audit_event("mlops.register", None, target=row["id"],
                        meta={"key": key, "updated": True})
            return dict(out)
    payload["key"] = key
    payload["id"] = new_id("mdl")
    out = _MODELS.put(payload)
    audit_event("mlops.register", None, target=out["id"], meta={"key": key})
    return dict(out)


def list_models() -> list:
    """列出已登记模型（内存态）。"""
    return _MODELS.list(limit=10 ** 6)


def _psi(baseline: List[float], observed: List[float], bins: int = _PSI_BINS) -> float:
    """简易 PSI：按 baseline 范围分箱，比较两分布占比（加平滑避免 log(0)）。"""
    if not baseline or not observed:
        return 0.0
    lo, hi = min(baseline), max(baseline)
    if hi <= lo:
        hi = lo + 1.0
    width = (hi - lo) / bins
    eps = 0.5

    def proportions(vals: List[float]) -> List[float]:
        counts = [0] * bins
        for v in vals:
            idx = int((v - lo) / width)
            idx = min(max(idx, 0), bins - 1)
            counts[idx] += 1
        n = len(vals)
        return [(c + eps) / (n + eps * bins) for c in counts]

    e = proportions(baseline)
    a = proportions(observed)
    return sum((ai - ei) * math.log(ai / ei) for ei, ai in zip(e, a))


def drift_check(baseline: list, observed: list) -> dict:
    """简易分布漂移：PSI + 均值漂移。

    返回 ``{"psi","mean_shift","drift","threshold","note"}``；
    ``drift = psi>=0.2 或 |均值漂移| 超过基线均值的 50%（最小 2.0）``。
    """
    b = _nums(baseline)
    o = _nums(observed)
    psi = round(_psi(b, o), 4)
    mean_b = sum(b) / len(b) if b else 0.0
    mean_o = sum(o) / len(o) if o else 0.0
    mean_shift = round(mean_o - mean_b, 4)
    mean_tol = max(2.0, abs(mean_b) * 0.5)
    drift = bool(psi >= _PSI_THRESHOLD or abs(mean_shift) >= mean_tol)
    return {
        "psi": psi,
        "mean_shift": mean_shift,
        "drift": drift,
        "threshold": _PSI_THRESHOLD,
        "mean_baseline": round(mean_b, 4),
        "mean_observed": round(mean_o, 4),
        "bins": _PSI_BINS,
        "size_baseline": len(b),
        "size_observed": len(o),
        "note": "骨架实现，未接训练流水线",
    }


def pipeline_status() -> dict:
    """MLOps 流水线状态：四项全部 done=False + 落地条件，明确未训练。"""
    return {
        "feature_store": {
            "done": False,
            "condition": "需接入离线/在线特征库（如 Feast）并定义特征视图与一致性校验；当前仅内存态",
        },
        "training": {
            "done": False,
            "condition": "需接入训练流水线（数据集、算力、任务调度、实验跟踪）；当前未接真模型训练",
        },
        "registry": {
            "done": False,
            "condition": "需接入模型仓库（版本/产物/签名/阶段流转）；当前 register_model 仅内存登记",
        },
        "retrain": {
            "done": False,
            "condition": "需漂移监控触发 + 审批 + 灰度 + 回滚；当前无自动重训",
        },
        "done_all": False,
        "trained": False,
        "models_registered": len(_MODELS.list(limit=10 ** 6)),
        "note": "MLOps 骨架：feature_store/training/registry/retrain 均未落地，未接训练流水线，"
                "不谎称已训练；register_model 仅作内存元数据登记。",
    }


def reset_state() -> None:
    """清空登记，供测试隔离。"""
    _MODELS.clear()
