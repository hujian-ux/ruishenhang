"""P1 · 办公类（62 个）业务包领域硬规则验证。

平台级硬规则（hard_rules）已对所有 115 业务包统一注入；本模块额外给办公类业务包
补「办公域」红线。验证 backfill 能正确把办公域规则挂到办公类 agent、幂等、且非办公类不受影响。
"""
from fastapi.testclient import TestClient

from app.core import security
from app.main import app
from app.services import agent_store


def _admin_token():
    return security.create_dev_token("u_admin", "d_it", "platform_admin", "t_demo")


def test_backfill_office_rules_applies_and_idempotent():
    k_office = "ag_office_demo"
    k_other = "ag_mfg_demo"
    agent_store._AGENTS[k_office] = {
        "agent_id": k_office, "name": "办公技能", "category": "office", "category_cn": "办公",
        "builtin": False, "public": False, "system_prompt": "你是办公专家。",
    }
    agent_store._AGENTS[k_other] = {
        "agent_id": k_other, "name": "制造技能", "category": "mfg", "category_cn": "制造",
        "builtin": False, "public": False, "system_prompt": "你是制造专家。",
    }
    try:
        c = TestClient(app)
        H = {"Authorization": _admin_token()}
        r = c.post("/api/v1/ops/backfill-office-rules", headers=H)
        assert r.status_code == 200
        d = r.json()["data"]
        assert d["patched"] >= 1
        assert d["office_rules_version"]

        off = agent_store._AGENTS[k_office]
        assert "办公域硬规则" in off["system_prompt"]
        assert off.get("office_rules_version")
        # 非办公类不受影响
        assert "办公域硬规则" not in agent_store._AGENTS[k_other]["system_prompt"]

        # 幂等：再跑一次不应再 patched
        r2 = c.post("/api/v1/ops/backfill-office-rules", headers=H)
        assert r2.json()["data"]["patched"] == 0
        assert r2.json()["data"]["already"] >= 1
    finally:
        agent_store._AGENTS.pop(k_office, None)
        agent_store._AGENTS.pop(k_other, None)
