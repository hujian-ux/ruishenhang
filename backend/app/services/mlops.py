"""M27 · MLOps 训练流水线（生产形状，诚实降级）。

相对旧骨架的落地项：
  - 特征库：本地特征库（纯 Python/csv，默认即生产可用）+ 可选 Feast（未装则降级，不报错）。
  - 训练：基线 sklearn 模型（分类/回归自动判别），产指标 + 产物（joblib）落到本地仓库。
  - 模型仓库：版本/产物/阶段（staging→production），晋升需 platform_admin 审批。
  - 漂移：PSI + 特征均值漂移 + 触发重训建议。
  - 重训：漂移触发「建议重训」钩子（函数级，非调度器——诚实标注未接调度）。

未落地（诚实，不谎称已接）：
  - 真实特征平台调度（Feast 在线服务/回填作业编排）。
  - 训练算力编排（分布式/GPU/实验跟踪系统对接）。
  - 自动重训 cron、灰度发布与回滚审批流。

存储：复用 ``_m27_store.Store``（内存态，重启丢——生产应换 PG/对象存储，已在 note 标注）；
产物落 ``MLOPS_ROOT``（默认仓库根 ``mlops_artifacts/``，已加 .gitignore）。
"""
from __future__ import annotations

import importlib.util as _ilu
import io
import json
import math
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

from app.services._m27_store import Store, new_id, now, iso, audit_event

_MODELS = Store("mlops_models")
_DATASETS = Store("mlops_datasets")
_RUNS = Store("mlops_runs")

_PSI_THRESHOLD = 0.20        # PSI 经验阈值：>=0.2 视为显著分布漂移
_PSI_BINS = 10

# 产物根目录（测试可 set_root 到 tmp）
_ROOT = Path(__file__).resolve().parent.parent.parent / "mlops_artifacts"
_FEATURE_LOCK = threading.Lock()


def set_root(p: Any) -> None:
    """覆盖产物根目录（测试隔离用）。"""
    global _ROOT
    _ROOT = Path(p)


def _root() -> Path:
    _ROOT.mkdir(parents=True, exist_ok=True)
    (_ROOT / "datasets").mkdir(exist_ok=True)
    (_ROOT / "models").mkdir(exist_ok=True)
    return _ROOT


def _feast_available() -> bool:
    return _ilu.find_spec("feast") is not None


# ───────────────────────────── 统计工具 ─────────────────────────────
def _nums(seq) -> List[float]:
    out: List[float] = []
    for x in seq or []:
        try:
            out.append(float(x))
        except (TypeError, ValueError):
            pass
    return out


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
    return float(sum((ai - ei) * math.log(ai / ei) for ei, ai in zip(e, a)))


def drift_check(baseline: list, observed: list) -> dict:
    """简易分布漂移：PSI + 均值漂移（保留旧签名，供 /mlops/drift 兼容）。"""
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
        "note": "单变量漂移；多维特征漂移请用 /mlops/drift/run",
    }


# ───────────────────────────── 特征库 ─────────────────────────────
class LocalFeatureStore:
    """本地特征库：把记录的若干特征列 + 标签落为 csv 数据集（确定性、可复现）。

    真实生产应替换为 Feast（在线服务 + 离线回填）。``materialize`` 返回的 dataset_id
    可喂给 ``train_model``。Feast 未装时本类即为默认实现，不报错。
    """

    def materialize(self, tenant_id: str, records: List[Dict[str, Any]],
                    feature_cols: List[str], label_col: str, name: str) -> Dict[str, Any]:
        if not records:
            raise ValueError("records 为空，无法生成训练数据集")
        for col in feature_cols + [label_col]:
            if col not in records[0]:
                raise ValueError(f"列缺失：{col}（记录中没有该字段）")
        cols = feature_cols + [label_col]
        dataset_id = new_id("ds")
        path = _root() / "datasets" / f"{dataset_id}.csv"
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = __import__("csv").writer(f)
            w.writerow(cols)
            for r in records:
                w.writerow([r.get(c, "") for c in cols])
        rec = {
            "id": dataset_id,
            "tenant_id": tenant_id,
            "name": name or f"dataset-{dataset_id}",
            "feature_cols": list(feature_cols),
            "label_col": label_col,
            "n_rows": len(records),
            "path": str(path),
            "store": "local",
            "created_at": now(),
            "created_iso": iso(),
            "note": "本地特征库落盘；生产建议替换为 Feast 在线/离线特征",
        }
        _DATASETS.put(rec)
        audit_event("mlops.dataset", tenant_id, target=dataset_id,
                    meta={"rows": len(records), "cols": cols})
        return dict(rec)

    def load(self, dataset_id: str) -> Optional[Dict[str, Any]]:
        rec = _DATASETS.get(dataset_id)
        if not rec:
            return None
        path = Path(rec["path"])
        if not path.exists():
            return rec
        import csv as _csv
        rows: List[Dict[str, Any]] = []
        with open(path, "r", encoding="utf-8") as f:
            for r in _csv.DictReader(f):
                rows.append(r)
        rec["_rows"] = rows
        return rec


def export_training_dataset(tenant_id: str, records: List[Dict[str, Any]],
                           feature_cols: List[str], label_col: str,
                           name: str = "") -> Dict[str, Any]:
    """导出训练数据集（本地特征库 materialize）。返回数据集记录。"""
    return LocalFeatureStore().materialize(tenant_id, records, feature_cols, label_col, name)


def list_datasets(tenant_id: str = "", caller_tenant: str = "", role: str = "") -> List[Dict[str, Any]]:
    items = _DATASETS.list(limit=10 ** 6)
    if role == "platform_admin":
        return items
    scope = caller_tenant or tenant_id
    if scope:
        items = [r for r in items if r.get("tenant_id") == scope]
    return items


# ───────────────────────────── 训练 ─────────────────────────────
def _is_regression(labels: List[Any]) -> bool:
    nums = _nums(labels)
    if len(nums) < len(labels):          # 含非数值 → 分类
        return False
    uniq = set(labels)
    return len(uniq) > 10                  # 数值且取值多 → 回归


def train_model(tenant_id: str, dataset_id: str, model_type: str = "auto",
                params: Optional[dict] = None, caller: Any = None) -> Dict[str, Any]:
    """用基线 sklearn 训练模型（分类/回归自动判别），产物落本地仓库并登记。

    返回 run 记录（含 model_id、指标、产物路径）。``model_type="auto"`` 按标签类型选。
    真实训练可在此处替换为更大的流水线（特征工程/调参/分布式），保持相同返回契约。
    """
    ds = LocalFeatureStore().load(dataset_id)
    if not ds:
        raise ValueError(f"数据集不存在：{dataset_id}")
    rows = ds.get("_rows") or []
    if not rows:
        raise ValueError("数据集为空或已丢失")

    try:
        from sklearn.model_selection import train_test_split
        from sklearn.linear_model import LogisticRegression, LinearRegression
        from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
        from sklearn.metrics import (accuracy_score, f1_score,
                                     mean_squared_error, r2_score)
        import joblib
    except Exception as e:  # pragma: no cover - sklearn 环境必然存在
        raise RuntimeError(f"sklearn/joblib 不可用，无法训练：{e}")

    feat = ds["feature_cols"]
    label = ds["label_col"]
    X, y = [], []
    for r in rows:
        try:
            X.append([float(r.get(c, 0) or 0) for c in feat])
        except (TypeError, ValueError):
            continue
        y.append(r.get(label))
    if len(X) < 4:
        raise ValueError("可用样本不足（需 >=4 条数值特征样本）")

    regression = (model_type == "regression") or (
        model_type == "auto" and _is_regression(y))
    Xa = np.asarray(X, dtype=float)
    ya = np.asarray([float(v) if regression else v for v in y])

    strat = None
    if not regression and len(set(ya.tolist())) > 1:
        strat = ya
    X_tr, X_te, y_tr, y_te = train_test_split(
        Xa, ya, test_size=0.2, random_state=42, stratify=strat)

    p = dict(params or {})
    if regression:
        est = (RandomForestRegressor(random_state=42, **p)
               if p.get("ensemble") else LinearRegression(**p))
    else:
        est = (RandomForestClassifier(random_state=42, **p)
               if p.get("ensemble") else LogisticRegression(max_iter=1000, **p))
    est.fit(X_tr, y_tr)
    yp = est.predict(X_te)

    if regression:
        metrics = {
            "mse": round(float(mean_squared_error(y_te, yp)), 6),
            "r2": round(float(r2_score(y_te, yp)), 6),
            "task": "regression",
        }
    else:
        metrics = {
            "accuracy": round(float(accuracy_score(y_te, yp)), 6),
            "f1": round(float(f1_score(y_te, yp, average="macro",
                                       zero_division=0)), 6),
            "task": "classification",
        }

    model_id = new_id("mdl")
    artifact = _root() / "models" / f"{model_id}.joblib"
    joblib.dump({"estimator": est, "feature_cols": feat, "regression": regression}, artifact)

    model = register_model(
        name=f"auto-{('rf' if p.get('ensemble') else ('lr' if not regression else 'lin'))}",
        version=iso(),
        metrics=metrics,
        fingerprint=f"sha1:{len(rows)}:{ds['n_rows']}",
        tenant_id=tenant_id,
        artifact=str(artifact),
        stage="staging",
        dataset_id=dataset_id,
        label_col=label, feature_cols=feat,
    )

    run = {
        "id": new_id("run"),
        "tenant_id": tenant_id,
        "model_id": model["id"],
        "dataset_id": dataset_id,
        "model_type": "regression" if regression else "classification",
        "params": p,
        "metrics": metrics,
        "n_train": len(X_tr),
        "n_test": len(X_te),
        "status": "completed",
        "created_at": now(),
        "created_iso": iso(),
    }
    _RUNS.put(run)
    audit_event("mlops.train", tenant_id, target=model["id"],
                meta={"task": run["model_type"], "metrics": metrics})
    return dict(run)


def list_runs(tenant_id: str = "", caller_tenant: str = "", role: str = "") -> List[Dict[str, Any]]:
    items = _RUNS.list(limit=10 ** 6)
    if role == "platform_admin":
        return items
    scope = caller_tenant or tenant_id
    if scope:
        items = [r for r in items if r.get("tenant_id") == scope]
    return items


def get_run(run_id: str) -> Optional[Dict[str, Any]]:
    return _RUNS.get(run_id)


# ───────────────────────────── 模型仓库 ─────────────────────────────
def register_model(name: str, version: str, metrics: dict,
                   fingerprint: str = "", tenant_id: str = "",
                   artifact: str = "", stage: str = "registered",
                   dataset_id: str = "", label_col: str = "",
                   feature_cols: Optional[List[str]] = None) -> dict:
    """登记模型元数据 + 产物路径 + 阶段。按 ``name:version`` 去重更新。"""
    key = f"{str(name or '')}:{str(version or '')}"
    payload: Dict[str, Any] = {
        "name": str(name or ""),
        "version": str(version or ""),
        "metrics": dict(metrics or {}),
        "fingerprint": str(fingerprint or ""),
        "tenant_id": tenant_id,
        "artifact": artifact,
        "stage": stage,
        "dataset_id": dataset_id,
        "label_col": label_col,
        "feature_cols": list(feature_cols or []),
        "updated_at": now(),
        "updated_iso": iso(),
    }
    for row in _MODELS.list(limit=10 ** 6):
        if row.get("key") == key:
            out = _MODELS.update(row["id"], payload)
            audit_event("mlops.register", tenant_id, target=row["id"],
                        meta={"key": key, "updated": True, "stage": stage})
            return dict(out)
    payload["key"] = key
    payload["id"] = new_id("mdl")
    out = _MODELS.put(payload)
    audit_event("mlops.register", tenant_id, target=out["id"], meta={"key": key, "stage": stage})
    return dict(out)


def list_models(tenant_id: str = "", caller_tenant: str = "", role: str = "") -> list:
    items = _MODELS.list(limit=10 ** 6)
    if role == "platform_admin":
        return items
    scope = caller_tenant or tenant_id
    if scope:
        items = [r for r in items if r.get("tenant_id") == scope]
    return items


def get_model(model_id: str) -> Optional[dict]:
    return _MODELS.get(model_id)


def promote_model(model_id: str, caller: Any) -> dict:
    """晋升 staging→production（需 platform_admin）。诚实：单步审批，非灰度回滚流。"""
    if getattr(caller, "role", "") != "platform_admin":
        raise PermissionError("仅 platform_admin 可晋升模型")
    row = _MODELS.get(model_id)
    if not row:
        raise ValueError(f"模型不存在：{model_id}")
    out = _MODELS.update(model_id, {"stage": "production", "updated_at": now(),
                                    "updated_iso": iso()})
    audit_event("mlops.promote", getattr(caller, "tenantId", ""), target=model_id,
                meta={"from": row.get("stage"), "to": "production"})
    return dict(out)


# ───────────────────────────── 漂移 + 重训钩子 ─────────────────────────────
def run_drift(tenant_id: str, baseline_dataset_id: str,
              observed_records: Optional[List[Dict[str, Any]]] = None,
              observed_dataset_id: Optional[str] = None) -> dict:
    """多维特征漂移：每个特征列算 PSI + 均值漂移，给出整体是否漂移。

    baseline 取自已登记数据集；observed 取记录列表或另一个数据集。
    """
    base = LocalFeatureStore().load(baseline_dataset_id)
    if not base:
        raise ValueError(f"基线数据集不存在：{baseline_dataset_id}")
    if observed_records is None:
        obs = LocalFeatureStore().load(observed_dataset_id) if observed_dataset_id else None
        observed_records = (obs or {}).get("_rows") or []
    if not observed_records:
        raise ValueError("observed 数据为空")

    feat = base["feature_cols"]
    features: List[Dict[str, Any]] = []
    any_drift = False
    for col in feat:
        b = _nums([r.get(col) for r in (base.get("_rows") or [])])
        o = _nums([r.get(col) for r in observed_records])
        psi = round(_psi(b, o), 4)
        mb = sum(b) / len(b) if b else 0.0
        mo = sum(o) / len(o) if o else 0.0
        ms = round(mo - mb, 4)
        tol = max(2.0, abs(mb) * 0.5)
        d = bool(psi >= _PSI_THRESHOLD or abs(ms) >= tol)
        any_drift = any_drift or d
        features.append({"col": col, "psi": psi, "mean_shift": ms,
                         "drift": d, "size_baseline": len(b), "size_observed": len(o)})
    return {
        "baseline_dataset_id": baseline_dataset_id,
        "overall_drift": bool(any_drift),
        "threshold": _PSI_THRESHOLD,
        "feature_count": len(features),
        "features": features,
        "note": "多维特征漂移；性能漂移须比对线上指标（本实现预留占位）",
    }


def maybe_retrain(drift_report: dict) -> dict:
    """漂移触发「建议重训」钩子（函数级，不自动调度——诚实标注）。"""
    should = bool(drift_report.get("overall_drift"))
    return {
        "should_retrain": should,
        "reason": ("检测到特征分布漂移，建议重训并回归验收"
                   if should else "未检测到显著漂移"),
        "drifted_features": [f["col"] for f in drift_report.get("features", [])
                             if f.get("drift")],
        "scheduler": "未接（生产应接 cron/Argo/K8s CronJob 周期执行本钩子）",
    }


def pipeline_status() -> dict:
    """MLOps 流水线状态：四项已部分落地 + 诚实标注未接项。"""
    return {
        "feature_store": {
            "done": True,
            "condition": "本地特征库已落盘（csv，确定性）；Feast 在线/离线服务未接（可选升级）",
            "feast_available": _feast_available(),
        },
        "training": {
            "done": True,
            "condition": "基线 sklearn 训练（分类/回归自动判别）+ 指标 + 产物(joblib) 已落地；"
                         "真实特征工程/调参/分布式训练未接",
        },
        "registry": {
            "done": True,
            "condition": "模型仓库（版本/产物/阶段 staging→production 晋升，需 platform_admin）已落地；"
                         "灰度发布与回滚审批流未接",
        },
        "retrain": {
            "done": False,
            "condition": "漂移检测 + 重训建议钩子已落地，但自动重训调度(cron/编排)未接——诚实标注",
        },
        "done_all": False,
        "trained": _RUNS.list(limit=10 ** 6) and True or False,
        "models_registered": len(_MODELS.list(limit=10 ** 6)),
        "datasets": len(_DATASETS.list(limit=10 ** 6)),
        "runs": len(_RUNS.list(limit=10 ** 6)),
        "note": "MLOps 已落地：本地特征库 + 基线训练 + 模型仓库 + 漂移检测；"
                "未接：Feast 在线服务、训练编排、自动重训调度、灰度回滚。不谎称已端到端训练。",
    }


def reset_state() -> None:
    """清空登记与产物，供测试隔离。"""
    _MODELS.clear()
    _DATASETS.clear()
    _RUNS.clear()
