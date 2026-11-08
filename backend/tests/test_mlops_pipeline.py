"""M27 MLOps 训练流水线 集成/单元测试（不依赖 Feast；sklearn 必装）。

覆盖：数据集导出、基线训练、维度漂移、晋升权限、租户隔离、校验失败、状态诚实标注。
"""
import pytest
from fastapi.testclient import TestClient

from app.core import security
from app.main import app
from app.services import mlops

_ADMIN = security.create_dev_token(user_id="u_admin", dept_id="d_it",
                                    role="platform_admin", tenant_id="t_demo")
_EMP = security.create_dev_token(user_id="u_emp", dept_id="d_it",
                                  role="employee", tenant_id="t_demo")
_EMP_OTHER = security.create_dev_token(user_id="u_emp2", dept_id="d_it",
                                        role="employee", tenant_id="t_other")
_H_ADMIN = {"Authorization": _ADMIN}
_H_EMP = {"Authorization": _EMP}
_H_EMP_OTHER = {"Authorization": _EMP_OTHER}

_CLIENT = TestClient(app)


@pytest.fixture(autouse=True)
def _isolate(tmp_path):
    mlops.set_root(tmp_path)
    mlops.reset_state()
    yield
    mlops.reset_state()


def _cls_records(n=12):
    return [{"x": float(i), "y": float(i * 2), "label": "good" if i % 2 == 0 else "bad"}
            for i in range(n)]


def _reg_records(n=12):
    return [{"x": float(i), "y": float(i * 2), "price": float(i * 10 + 3)}
            for i in range(n)]


def test_export_dataset_writes_csv():
    r = _CLIENT.post("/api/v1/mlops/datasets/export", headers=_H_ADMIN, json={
        "records": _cls_records(), "feature_cols": ["x", "y"],
        "label_col": "label", "name": "toy-cls"})
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["n_rows"] == 12 and d["label_col"] == "label"
    from pathlib import Path
    assert Path(d["path"]).exists()


def test_export_dataset_validation_missing_col():
    r = _CLIENT.post("/api/v1/mlops/datasets/export", headers=_H_ADMIN, json={
        "records": _cls_records(4), "feature_cols": ["x", "nope"],
        "label_col": "label"})
    assert r.status_code == 422


def test_train_baseline_registers_staging_model():
    ds = _CLIENT.post("/api/v1/mlops/datasets/export", headers=_H_ADMIN, json={
        "records": _cls_records(), "feature_cols": ["x", "y"],
        "label_col": "label"}).json()["data"]
    run = _CLIENT.post("/api/v1/mlops/trains", headers=_H_ADMIN,
                       json={"dataset_id": ds["id"], "model_type": "classification"})
    assert run.status_code == 200, run.text
    data = run.json()["data"]
    assert data["status"] == "completed" and data["metrics"]["task"] == "classification"
    assert "accuracy" in data["metrics"]

    models = _CLIENT.get("/api/v1/mlops/models", headers=_H_ADMIN).json()["data"]["items"]
    assert any(m["stage"] == "staging" and m["id"] == data["model_id"] for m in models)

    got = _CLIENT.get(f"/api/v1/mlops/trains/{data['id']}", headers=_H_ADMIN)
    assert got.status_code == 200 and got.json()["data"]["id"] == data["id"]


def test_train_regression_auto_detect():
    ds = _CLIENT.post("/api/v1/mlops/datasets/export", headers=_H_ADMIN, json={
        "records": _reg_records(), "feature_cols": ["x", "y"],
        "label_col": "price"}).json()["data"]
    run = _CLIENT.post("/api/v1/mlops/trains", headers=_H_ADMIN,
                       json={"dataset_id": ds["id"]}).json()["data"]
    assert run["metrics"]["task"] == "regression"
    assert "mse" in run["metrics"] and "r2" in run["metrics"]


def test_drift_run_detects_shift():
    base = _CLIENT.post("/api/v1/mlops/datasets/export", headers=_H_ADMIN, json={
        "records": _cls_records(), "feature_cols": ["x", "y"],
        "label_col": "label"}).json()["data"]
    # observed 把 x 整体平移 100，应触发漂移
    obs = [{"x": r["x"] + 100, "y": r["y"], "label": r["label"]} for r in _cls_records()]
    r = _CLIENT.post("/api/v1/mlops/drift/run", headers=_H_ADMIN, json={
        "baseline_dataset_id": base["id"], "observed_records": obs})
    assert r.status_code == 200, r.text
    rep = r.json()["data"]
    assert rep["overall_drift"] is True and rep["feature_count"] == 2


def test_promote_requires_platform_admin():
    ds = _CLIENT.post("/api/v1/mlops/datasets/export", headers=_H_ADMIN, json={
        "records": _cls_records(), "feature_cols": ["x", "y"],
        "label_col": "label"}).json()["data"]
    mid = _CLIENT.post("/api/v1/mlops/trains", headers=_H_ADMIN,
                       json={"dataset_id": ds["id"]}).json()["data"]["model_id"]

    deny = _CLIENT.post(f"/api/v1/mlops/models/{mid}/promote", headers=_H_EMP)
    assert deny.status_code == 403
    ok = _CLIENT.post(f"/api/v1/mlops/models/{mid}/promote", headers=_H_ADMIN)
    assert ok.status_code == 200 and ok.json()["data"]["stage"] == "production"


def test_tenant_isolation():
    ds = _CLIENT.post("/api/v1/mlops/datasets/export", headers=_H_ADMIN, json={
        "records": _cls_records(), "feature_cols": ["x", "y"],
        "label_col": "label"}).json()["data"]
    _CLIENT.post("/api/v1/mlops/trains", headers=_H_ADMIN,
                 json={"dataset_id": ds["id"]})
    # t_other 的员工看不到 t_demo 的模型
    other = _CLIENT.get("/api/v1/mlops/models", headers=_H_EMP_OTHER).json()["data"]
    assert other["count"] == 0


def test_pipeline_status_honest():
    s = _CLIENT.get("/api/v1/mlops/pipeline", headers=_H_ADMIN).json()["data"]
    assert s["feature_store"]["done"] is True
    assert s["training"]["done"] is True
    assert s["registry"]["done"] is True
    assert s["retrain"]["done"] is False      # 诚实标注：自动重训调度未接
    assert isinstance(s["feature_store"]["feast_available"], bool)
