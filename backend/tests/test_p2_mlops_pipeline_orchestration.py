"""P2 · MLOps 训练编排（run_training_pipeline）验证：多阶段 prepare→drift→train→evaluate→promote。

不依赖真实 IdP / 外部特征平台；用本地特征库造数据，验证：
- 漂移时编排真正训练并落 staging，stages 各阶段状态正确（action=retrained）。
- 无漂移时 train 阶段 skip（action=skip）。
- skip_drift_check=True 时可跳过漂移直接训练（observed 为空也不崩）。
- auto_promote=True 且更优（无现存生产模型）→ promote 阶段 done。
- auto_promote=True 但指标未严格更优（现存相等生产模型）→ promote 阶段 skipped + reason_no_promote。
- 数据集不存在 → prepare 阶段 error（action=error）。
- /mlops/pipeline/run 端点：非管理员 403、未开开关 409、管理员且开启后 200（含 stages）。
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


def test_pipeline_trains_on_drift(tmp_path):
    mlops.reset_state(); mlops.set_root(tmp_path)
    try:
        ds = mlops.export_training_dataset("t1", _mk_records(40, 0.0),
                                          ["f1", "f2"], "y", "base")
        obs = _mk_records(20, 100.0)  # 显著漂移
        out = mlops.run_training_pipeline(ds["id"], observed_records=obs,
                                          tenant_id="t1", auto_promote=False)
        assert out["action"] == "retrained"
        st = out["stages"]
        assert st["prepare"]["status"] == "ok"
        assert st["drift"]["overall_drift"] is True
        assert st["train"]["status"] == "ok" and "model_id" in st["train"]
        assert st["evaluate"]["status"] == "ok"
        assert st["promote"]["status"] == "skipped"  # 未请求晋升
        assert out["drift"]["overall_drift"] is True
    finally:
        mlops.reset_state()


def test_pipeline_skips_without_drift(tmp_path):
    mlops.reset_state(); mlops.set_root(tmp_path)
    try:
        ds = mlops.export_training_dataset("t1", _mk_records(40, 0.0),
                                          ["f1", "f2"], "y", "base")
        obs = _mk_records(40, 0.0)  # 与基线一致 → 不漂移
        out = mlops.run_training_pipeline(ds["id"], observed_records=obs, tenant_id="t1")
        assert out["action"] == "skip"
        assert out["stages"]["train"]["status"] == "skip"
        assert out["drift"]["overall_drift"] is False
    finally:
        mlops.reset_state()


def test_pipeline_skip_drift_check_trains_with_empty_observed(tmp_path):
    mlops.reset_state(); mlops.set_root(tmp_path)
    try:
        ds = mlops.export_training_dataset("t1", _mk_records(40, 0.0),
                                          ["f1", "f2"], "y", "base")
        # 跳过漂移检查，observed 为空也不应崩，应直接进入训练
        out = mlops.run_training_pipeline(ds["id"], observed_records=[],
                                          tenant_id="t1", skip_drift_check=True)
        assert out["action"] == "retrained"
        assert out["stages"]["drift"]["skipped_check"] is True
        assert out["stages"]["train"]["status"] == "ok"
    finally:
        mlops.reset_state()


def test_pipeline_auto_promote_when_no_production(tmp_path):
    mlops.reset_state(); mlops.set_root(tmp_path)
    try:
        ds = mlops.export_training_dataset("t1", _mk_records(40, 0.0),
                                          ["f1", "f2"], "y", "base")
        # 无现存生产模型 → 更优（prod=None）→ 晋升 done
        out = mlops.run_training_pipeline(ds["id"], observed_records=_mk_records(20, 100.0),
                                          tenant_id="t1", auto_promote=True)
        assert out["action"] == "retrained"
        assert out["promoted"] is True
        assert out["stages"]["promote"]["status"] == "done"
    finally:
        mlops.reset_state()


def test_pipeline_no_promote_when_not_strictly_better(tmp_path):
    mlops.reset_state(); mlops.set_root(tmp_path)
    try:
        ds = mlops.export_training_dataset("t1", _mk_records(40, 0.0),
                                          ["f1", "f2"], "y", "base")
        # 先在 production 放一个相同数据集训出的模型（指标相等）
        first = mlops.train_model("t1", ds["id"], "regression")
        mlops.promote_model(first["model_id"], mlops._AdminCtx("t1"))
        # 再编排：新模型指标相等 → 未严格更优 → 不晋升（防回退）
        out = mlops.run_training_pipeline(ds["id"], observed_records=_mk_records(20, 100.0),
                                          tenant_id="t1", auto_promote=True)
        assert out["action"] == "retrained"
        assert out["promoted"] is False
        assert out["stages"]["promote"]["status"] == "skipped"
        assert "reason_no_promote" in out
    finally:
        mlops.reset_state()


def test_pipeline_prepare_error_on_missing_dataset(tmp_path):
    mlops.reset_state(); mlops.set_root(tmp_path)
    try:
        out = mlops.run_training_pipeline("ds_nonexistent", tenant_id="t1")
        assert out["action"] == "error"
        assert out["stages"]["prepare"]["status"] == "error"
    finally:
        mlops.reset_state()


def test_pipeline_run_endpoint(tmp_path):
    mlops.reset_state(); mlops.set_root(tmp_path)
    orig = settings.MLOPS_AUTO_RETRAIN
    settings.MLOPS_AUTO_RETRAIN = True
    try:
        ds = mlops.export_training_dataset("t1", _mk_records(40, 0.0),
                                          ["f1", "f2"], "y", "b")
        c = TestClient(app)
        # 非管理员禁入
        r = c.post("/api/v1/mlops/pipeline/run",
                   json={"baseline_dataset_id": ds["id"],
                         "observed_records": _mk_records(10, 50.0)},
                   headers=_emp_headers(c))
        assert r.status_code == 403
        # 管理员 + 开开关 → 200，含 stages
        r = c.post("/api/v1/mlops/pipeline/run",
                   json={"baseline_dataset_id": ds["id"],
                         "observed_records": _mk_records(10, 50.0),
                         "skip_drift_check": True},
                   headers=_admin_headers(c))
        assert r.status_code == 200
        d = r.json()["data"]
        assert d["action"] in ("retrained", "skip")
        assert "stages" in d and "prepare" in d["stages"]
    finally:
        settings.MLOPS_AUTO_RETRAIN = orig
        mlops.reset_state()


def test_pipeline_run_requires_flag(tmp_path):
    mlops.reset_state(); mlops.set_root(tmp_path)
    orig = settings.MLOPS_AUTO_RETRAIN
    settings.MLOPS_AUTO_RETRAIN = False
    try:
        ds = mlops.export_training_dataset("t1", _mk_records(20, 0.0),
                                          ["f1", "f2"], "y", "b")
        c = TestClient(app)
        r = c.post("/api/v1/mlops/pipeline/run",
                   json={"baseline_dataset_id": ds["id"], "skip_drift_check": True},
                   headers=_admin_headers(c))
        assert r.status_code == 409  # 安全默认：未开开关先拦截
    finally:
        settings.MLOPS_AUTO_RETRAIN = orig
        mlops.reset_state()
