"""P2 · MLOps 真实自动重训钩子验证：漂移→真实训练→可选晋升。

不依赖真实 IdP / 外部特征平台；用本地特征库造数据，验证：
- 有漂移时 scheduled_retrain 真正训练并落 staging（action=retrained）。
- 无漂移时跳过（action=skip）。
- /mlops/retrain 端点：非管理员 403、未开开关 409、管理员且开启后 200。
- 指标比较助手 _metrics_better 行为正确。
"""
import os

from app.core.config import settings
from app.main import app
from app.services import mlops
from fastapi.testclient import TestClient


def _mk_records(n: int, base: float):
    return [{"f1": base + i * 0.1, "f2": base * 2 + i * 0.05, "y": base * 3 + i}
            for i in range(n)]


def _token(client: TestClient, admin: bool):
    code = "dev_admin" if admin else "dev"
    r = client.post("/api/v1/auth/sso/callback",
                    json={"provider": "wecom", "code": code, "device_id": "x"})
    return r.json()["data"]["token"]


def _admin_headers(client: TestClient):
    return {"Authorization": _token(client, True)}


def _emp_headers(client: TestClient):
    return {"Authorization": _token(client, False)}


def test_scheduled_retrain_runs_on_drift(tmp_path):
    mlops.reset_state(); mlops.set_root(tmp_path)
    try:
        ds = mlops.export_training_dataset("t1", _mk_records(40, 0.0),
                                          ["f1", "f2"], "y", "baseline")
        run = mlops.train_model("t1", ds["id"], "regression")
        assert run["status"] == "completed"
        obs = _mk_records(20, 100.0)  # 显著漂移的分布
        out = mlops.scheduled_retrain(ds["id"], obs, tenant_id="t1", auto_promote=False)
        assert out["action"] == "retrained"
        assert out["drift"]["overall_drift"] is True
        assert "model_id" in out and out["model_id"]
    finally:
        mlops.reset_state()


def test_scheduled_retrain_skip_without_drift(tmp_path):
    mlops.reset_state(); mlops.set_root(tmp_path)
    try:
        ds = mlops.export_training_dataset("t1", _mk_records(40, 0.0),
                                          ["f1", "f2"], "y", "base")
        obs = _mk_records(40, 0.0)  # 与基线完全一致的分布 → 不应判漂移
        out = mlops.scheduled_retrain(ds["id"], obs, tenant_id="t1")
        assert out["action"] == "skip"
        assert out["drift"]["overall_drift"] is False
    finally:
        mlops.reset_state()


def test_metrics_better():
    assert mlops._metrics_better({"accuracy": 0.9}, {"accuracy": 0.8}) is True
    assert mlops._metrics_better({"accuracy": 0.8}, {"accuracy": 0.9}) is False
    assert mlops._metrics_better({"r2": 0.7}, {"r2": 0.5}) is True
    assert mlops._metrics_better({"mse": 0.1}, {"mse": 0.2}) is True
    assert mlops._metrics_better({"accuracy": 0.9}, {}) is True


def test_mlops_retrain_endpoint(tmp_path):
    mlops.reset_state(); mlops.set_root(tmp_path)
    orig = settings.MLOPS_AUTO_RETRAIN
    settings.MLOPS_AUTO_RETRAIN = True
    try:
        ds = mlops.export_training_dataset("t1", _mk_records(40, 0.0),
                                          ["f1", "f2"], "y", "b")
        c = TestClient(app)
        r = c.post("/api/v1/mlops/retrain",
                   json={"baseline_dataset_id": ds["id"],
                         "observed_records": _mk_records(10, 50.0)},
                   headers=_emp_headers(c))
        assert r.status_code == 403  # 非管理员禁入
        r = c.post("/api/v1/mlops/retrain",
                   json={"baseline_dataset_id": ds["id"],
                         "observed_records": _mk_records(10, 50.0)},
                   headers=_admin_headers(c))
        assert r.status_code == 200
        assert r.json()["data"]["action"] in ("retrained", "skip")
    finally:
        settings.MLOPS_AUTO_RETRAIN = orig
        mlops.reset_state()


def test_mlops_retrain_requires_flag(tmp_path):
    mlops.reset_state(); mlops.set_root(tmp_path)
    orig = settings.MLOPS_AUTO_RETRAIN
    settings.MLOPS_AUTO_RETRAIN = False
    try:
        ds = mlops.export_training_dataset("t1", _mk_records(20, 0.0),
                                          ["f1", "f2"], "y", "b")
        c = TestClient(app)
        r = c.post("/api/v1/mlops/retrain",
                   json={"baseline_dataset_id": ds["id"]}, headers=_admin_headers(c))
        assert r.status_code == 409  # 安全默认：未开开关先拦截
    finally:
        settings.MLOPS_AUTO_RETRAIN = orig
        mlops.reset_state()
