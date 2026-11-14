"""P2 · Feast 在线特征服务适配器验证。

设计：降级路径（Feast 未安装）始终验证，确保不报错、诚实标注；
真实同步 + 在线取数仅在 feast 包可用时运行（skip，避免硬依赖）。

覆盖：
- ``_feast_available()`` 返回 bool；``pipeline_status().feature_store`` 含真实口径键。
- ``/mlops/feast/sync`` 非管理员 403；可用性与数据集缺失时返回 409/422（绝不 500）。
- ``/mlops/feast/online`` 同样不 500。
- 真实集成（feast 可用）：本地数据集 → sync_dataset(materialize) → get_online_features 回读一致。
"""
import importlib.util

import pytest
from app.main import app
from app.services import mlops
from fastapi.testclient import TestClient


def _token(client, admin: bool):
    code = "dev_admin" if admin else "dev"
    r = client.post("/api/v1/auth/sso/callback",
                    json={"provider": "wecom", "code": code, "device_id": "x"})
    return r.json()["data"]["token"]


def _feast_importable() -> bool:
    return importlib.util.find_spec("feast") is not None


def test_feast_available_is_bool():
    from app.services.feast_store import _feast_available
    assert isinstance(_feast_available(), bool)


def test_pipeline_status_feature_store_keys():
    st = mlops.pipeline_status()["feature_store"]
    assert "feast_available" in st
    assert isinstance(st["feast_available"], bool)
    assert "condition" in st and isinstance(st["condition"], str) and st["condition"]


def test_feast_sync_endpoint_requires_admin():
    c = TestClient(app)
    r = c.post("/api/v1/mlops/feast/sync", json={"dataset_id": "x"},
              headers={"Authorization": _token(c, False)})
    assert r.status_code == 403


def test_feast_sync_endpoint_no_500():
    c = TestClient(app)
    H = {"Authorization": _token(c, True)}
    r = c.post("/api/v1/mlops/feast/sync", json={"dataset_id": "nope"}, headers=H)
    # 不可用 → 409；可用但数据集不存在 → 422；绝不 500
    assert r.status_code in (409, 422)
    assert r.status_code != 500


def test_feast_online_endpoint_no_500():
    c = TestClient(app)
    H = {"Authorization": _token(c, True)}
    r = c.post("/api/v1/mlops/feast/online",
               json={"dataset_id": "nope", "entity_ids": ["0"]}, headers=H)
    assert r.status_code in (409, 422)
    assert r.status_code != 500


@pytest.mark.skipif(not _feast_importable(), reason="feast 未安装，跳过真实在线服务集成测试")
def test_feast_real_sync_and_online(tmp_path):
    from app.services.feast_store import FeastFeatureStore

    mlops.set_root(tmp_path)
    try:
        recs = [{"f1": 1.0, "f2": 2.0, "y": 3.0},
                {"f1": 1.5, "f2": 2.5, "y": 3.5}]
        ds = mlops.export_training_dataset("t1", recs, ["f1", "f2"], "y", "feast_test")

        store = FeastFeatureStore(repo_dir=tmp_path / "feast_repo")
        assert store.available, "feast 已安装但仓库初始化失败"

        sync = store.sync_dataset(ds["id"])
        assert sync["materialized"] is True
        assert sync["online_store"] == "sqlite"

        out = store.get_online_features(ds["id"], ["0", "1"])
        assert out["entities"] == ["0", "1"]
        feats = out["features"]
        assert float(feats["f1"][0]) == pytest.approx(1.0)
        assert float(feats["f1"][1]) == pytest.approx(1.5)
        assert float(feats["f2"][0]) == pytest.approx(2.0)
    finally:
        mlops.reset_state()
