"""P2 · MLOps 灰度回滚闭环验证：promote → 线上退化 → rollback 恢复上一生产模型。

不依赖真实 IdP / 外部特征平台；用本地特征库造数据，验证：
- rollback_model 把坏的生产模型降级回 staging，并恢复同名上一生产模型。
- 无同名上一生产模型时仅降级、restored_model_id 为空。
- 仅 platform_admin 可回滚（非管理员 PermissionError）。
- /mlops/models/{id}/rollback 端点：管理员 200、非管理员 403、模型不存在 404。
"""
from app.main import app
from app.services import mlops
from fastapi.testclient import TestClient


class _Ctx:
    def __init__(self, role: str = "platform_admin", tenant_id: str = ""):
        self.role = role
        self.tenantId = tenant_id


def _make_model(tmp_path, tenant: str, base: float, name_hint: str):
    mlops.set_root(tmp_path)
    ds = mlops.export_training_dataset(tenant, _records(40, base),
                                      ["f1", "f2"], "y", name_hint)
    run = mlops.train_model(tenant, ds["id"], "regression")
    return run["model_id"]


def _records(n: int, base: float):
    return [{"f1": base + i * 0.1, "f2": base * 2 + i * 0.05, "y": base * 3 + i}
            for i in range(n)]


def _token(client: TestClient, admin: bool):
    code = "dev_admin" if admin else "dev"
    r = client.post("/api/v1/auth/sso/callback",
                    json={"provider": "wecom", "code": code, "device_id": "x"})
    return r.json()["data"]["token"]


def test_rollback_demotes_and_restores_previous(tmp_path):
    mlops.reset_state(); mlops.set_root(tmp_path)
    try:
        admin = _Ctx("platform_admin", "t1")
        # 上一生产模型（好）
        prev_id = _make_model(tmp_path, "t1", 0.0, "good")
        mlops.promote_model(prev_id, admin)
        # 新晋升的生产模型（坏）
        bad_id = _make_model(tmp_path, "t1", 0.0, "bad")
        mlops.promote_model(bad_id, admin)
        assert mlops.get_model(bad_id)["stage"] == "production"
        assert mlops.get_model(prev_id)["stage"] == "production"

        out = mlops.rollback_model(bad_id, admin)
        assert out["rolled_back"] == bad_id
        assert out["demoted_to"] == "staging"
        assert out["restored_model_id"] == prev_id
        assert mlops.get_model(bad_id)["stage"] == "staging"
        assert mlops.get_model(prev_id)["stage"] == "production"  # 恢复服务
    finally:
        mlops.reset_state()


def test_rollback_without_previous_production(tmp_path):
    mlops.reset_state(); mlops.set_root(tmp_path)
    try:
        admin = _Ctx("platform_admin", "t1")
        only_id = _make_model(tmp_path, "t1", 0.0, "only")
        mlops.promote_model(only_id, admin)
        out = mlops.rollback_model(only_id, admin)
        assert out["demoted_to"] == "staging"
        assert out["restored_model_id"] is None  # 无同名上一生产模型
        assert mlops.get_model(only_id)["stage"] == "staging"
    finally:
        mlops.reset_state()


def test_rollback_requires_admin(tmp_path):
    mlops.reset_state(); mlops.set_root(tmp_path)
    try:
        admin = _Ctx("platform_admin", "t1")
        emp = _Ctx("employee", "t1")
        mid = _make_model(tmp_path, "t1", 0.0, "x")
        mlops.promote_model(mid, admin)
        try:
            mlops.rollback_model(mid, emp)
            raise AssertionError("非管理员应被拒")
        except PermissionError:
            pass
    finally:
        mlops.reset_state()


def test_rollback_endpoint_authz_and_flow(tmp_path):
    mlops.reset_state(); mlops.set_root(tmp_path)
    try:
        admin = _Ctx("platform_admin", "t1")
        prev_id = _make_model(tmp_path, "t1", 0.0, "g")
        mlops.promote_model(prev_id, admin)
        bad_id = _make_model(tmp_path, "t1", 0.0, "b")
        mlops.promote_model(bad_id, admin)

        c = TestClient(app)
        # 非管理员 → 403
        r = c.post(f"/api/v1/mlops/models/{bad_id}/rollback",
                   headers={"Authorization": _token(c, False)})
        assert r.status_code == 403
        # 管理员 → 200 并恢复上一生产模型
        r = c.post(f"/api/v1/mlops/models/{bad_id}/rollback",
                   headers={"Authorization": _token(c, True)})
        assert r.status_code == 200
        d = r.json()["data"]
        assert d["demoted_to"] == "staging"
        assert d["restored_model_id"] == prev_id
        # 不存在的模型 → 404
        r = c.post("/api/v1/mlops/models/nope/rollback",
                   headers={"Authorization": _token(c, True)})
        assert r.status_code == 404
    finally:
        mlops.reset_state()
