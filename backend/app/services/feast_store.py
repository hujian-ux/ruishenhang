"""Feast 在线特征服务适配器（最小本地落地，P2 可选升级收口）。

设计目标
--------
MLOps 原先用 ``LocalFeatureStore``（csv 落盘，离线/训练用）。本模块在不破坏既有
训练/漂移链路的前提下，把同一份本地数据集 **同步进 Feast 并 materialize 到 SQLite
在线存储**，从而提供低延迟的在线特征取数（``get_online_features``）。

关键事实
--------
- Feast 的 local provider + sqlite online_store **不需要 Redis**，纯本机即可跑通。
- 所有入口都受 ``_feast_available()`` 门控：feast 未安装或初始化失败，
  ``available`` 为 False，调用方自动回落到 ``LocalFeatureStore``，绝不抛错。
- 适配器按数据集动态生成 Entity + FileSource + FeatureView，复用已落盘的 csv，
  不引入额外数据源。
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

_ROOT = Path(__file__).resolve().parent.parent  # backend/
REPO_DIR = _ROOT / "feast_repo"


def _feast_available() -> bool:
    return importlib.util.find_spec("feast") is not None


class FeastFeatureStore:
    """Feast 在线特征服务适配器。

    典型用法::

        fs = get_feast_store()
        if fs.available:
            fs.sync_dataset(dataset_id)                 # csv -> Feast -> 在线存储
            feat = fs.get_online_features(dataset_id, ["row_0"], ["f1", "f2"])
    """

    def __init__(self, repo_dir: Path | str | None = None) -> None:
        self.repo_dir = Path(repo_dir or REPO_DIR)
        self._fs = None
        self._ready = False
        if _feast_available():
            try:
                from feast import FeatureStore  # 延迟导入，未装不触发
                self.repo_dir.mkdir(parents=True, exist_ok=True)
                # 确保 feature_store.yaml 存在（首装时由代码兜底写入）
                self._ensure_repo_config()
                self._fs = FeatureStore(repo_path=str(self.repo_dir))
                self._ready = True
            except Exception as _e:  # pragma: no cover - 环境异常时优雅降级
                self._fs = None
                self._ready = False
                self._init_error = str(_e)

    # ── 公共属性 ──────────────────────────────────────────────
    @property
    def available(self) -> bool:
        return self._ready

    @property
    def init_error(self) -> Optional[str]:
        return getattr(self, "_init_error", None)

    # ── 内部工具 ──────────────────────────────────────────────
    def _ensure_repo_config(self) -> None:
        cfg = self.repo_dir / "feature_store.yaml"
        if not cfg.exists():
            cfg.write_text(
                "project: enterprise_ai_mlops\n"
                "registry: registry.db\n"
                "provider: local\n"
                "online_store:\n"
                "  type: sqlite\n"
                "  path: online_store.db\n"
                "entity_key_serialization_version: 2\n",
                encoding="utf-8",
            )

    @staticmethod
    def _fv_name(dataset_id: str) -> str:
        return f"fv_{dataset_id}"

    @staticmethod
    def _entity_name() -> str:
        return "mlops_entity"

    def _infer_schema(self, rows: List[Dict[str, Any]], cols: List[str]):
        """推断 Feast Field schema：数值列 → FLOAT64，其余 → STRING。"""
        from feast import Field
        from feast.types import Float64, String

        # 抽样首行判断可转 float；时间戳由 FileSource 的 event_timestamp_column 承载，
        # 不应作为 FeatureView schema 的特征列。
        sample = rows[0] if rows else {}
        fields = []
        for c in cols:
            raw = sample.get(c, "")
            try:
                float(raw)
                fields.append(Field(name=c, dtype=Float64))
            except (TypeError, ValueError):
                fields.append(Field(name=c, dtype=String))
        return fields

    # ── 对外能力 ──────────────────────────────────────────────
    def sync_dataset(self, dataset_id: str) -> Dict[str, Any]:
        """把本地 csv 数据集同步进 Feast 并 materialize 到在线存储。

        返回摘要；不可用时抛 RuntimeError（调用方应先用 ``available`` 判断）。
        """
        if not self._ready or self._fs is None:
            raise RuntimeError("Feast 不可用，无法同步数据集（请确认 feast 已安装）")
        from datetime import datetime, timedelta, timezone
        from app.services import mlops as _mlops  # 惰性导入，避免与 mlops 的循环依赖

        local = _mlops.LocalFeatureStore().load(dataset_id)
        if not local:
            raise ValueError(f"本地数据集不存在：{dataset_id}")
        rows = local.get("_rows") or []
        if not rows:
            raise ValueError("数据集为空或已丢失")
        feat_cols = list(local.get("feature_cols") or [])

        # 1) 构造 DataFrame（含实体键 + 事件时间戳）；数值列按推断类型强制转 float
        try:
            import pandas as pd
        except Exception as _e:  # pragma: no cover
            raise RuntimeError(f"pandas 不可用：{_e}")
        sample = rows[0] if rows else {}
        numeric_cols = set()
        for c in feat_cols:
            try:
                float(sample.get(c, ""))
                numeric_cols.add(c)
            except (TypeError, ValueError):
                pass
        recs = []
        for i, r in enumerate(rows):
            rec = {"mlops_entity": str(i), "event_timestamp": datetime.now(timezone.utc)}
            for c in feat_cols:
                v = r.get(c, "")
                if c in numeric_cols:
                    try:
                        v = float(v)
                    except (TypeError, ValueError):
                        v = None
                rec[c] = v
            recs.append(rec)
        df = pd.DataFrame(recs)

        parquet_path = self.repo_dir / "data" / f"{dataset_id}.parquet"
        parquet_path.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(parquet_path, index=False)

        # 2) 动态构建 Entity + FileSource + FeatureView 并 apply
        from feast import Entity, FeatureView, FileSource, ValueType
        from datetime import timedelta as _td

        entity = Entity(name=self._entity_name(), value_type=ValueType.STRING)
        source = FileSource(
            path=str(parquet_path),
            event_timestamp_column="event_timestamp",
        )
        fv = FeatureView(
            name=self._fv_name(dataset_id),
            entities=[entity],
            schema=self._infer_schema(rows, feat_cols),
            source=source,
            ttl=_td(days=3650),
        )
        self._fs.apply([entity, fv])

        # 3) materialize 到在线存储（时间窗覆盖事件时间戳）
        now = datetime.now(timezone.utc)
        self._fs.materialize(start_date=now - _td(days=2), end_date=now + _td(days=2))

        return {
            "dataset_id": dataset_id,
            "feature_view": self._fv_name(dataset_id),
            "entity": self._entity_name(),
            "n_rows": len(rows),
            "feature_cols": feat_cols,
            "online_store": "sqlite",
            "materialized": True,
        }

    def get_online_features(self, dataset_id: str,
                            entity_ids: List[str],
                            features: Optional[List[str]] = None) -> Dict[str, Any]:
        """从 Feast 在线存储取特征。

        ``features`` 省略时取该数据集 FeatureView 的全部特征。
        返回 ``{"entities": [...], "features": {col: [v,...]}}``，不可用时抛 RuntimeError。
        """
        if not self._ready or self._fs is None:
            raise RuntimeError("Feast 不可用，无法在线取特征")
        try:
            fv = self._fv_name(dataset_id)
            if features:
                feat_refs = [f"{fv}:{c}" for c in features]
            else:
                # 未指定时取该 FeatureView 的全部特征（feast 0.68 要求显式 <fv>:<feature> 引用）
                fv_obj = self._fs.get_feature_view(fv)
                feat_refs = [f"{fv}:{f.name}" for f in fv_obj.features]
            entity_rows = [{self._entity_name(): str(e)} for e in entity_ids]
            resp = self._fs.get_online_features(
                features=feat_refs, entity_rows=entity_rows
            ).to_dict()
        except Exception as _e:
            # 特征视图未同步 / 实体不存在等 → 诚实降级为 RuntimeError（端点转 409）
            raise RuntimeError(f"Feast 在线取数失败（可能未同步 {dataset_id}）：{_e}")
        # 拆分实体键与特征
        entities = resp.pop(self._entity_name(), entity_ids)
        return {"entities": entities, "features": resp}

    def status(self) -> Dict[str, Any]:
        return {
            "available": self._ready,
            "repo_dir": str(self.repo_dir),
            "init_error": self.init_error,
            "feast_version": self._feast_version(),
        }

    @staticmethod
    def _feast_version() -> Optional[str]:
        try:
            import feast
            return getattr(feast, "__version__", "unknown")
        except Exception:
            return None


# ── 进程内单例 ────────────────────────────────────────────────
_STORE: Optional["FeastFeatureStore"] = None


def get_feast_store() -> FeastFeatureStore:
    global _STORE
    if _STORE is None:
        _STORE = FeastFeatureStore()
    return _STORE


def reset_store() -> None:
    """测试隔离：丢弃单例（不删除磁盘产物）。"""
    global _STORE
    _STORE = None
