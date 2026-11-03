"""M13 审计日志测试：存储（PG/SQLite 兜底）、RBAC 收敛、敏感字段脱敏。

conftest 默认 PG_PORT=1（不可达），本测试走 SQLite 兜底路径，验证降级正确。
"""
import uuid

from app.models.caller import CallerContext
from app.services import audit


def _u(suffix: str, role: str = "employee", dept: str = "d_x") -> CallerContext:
    return CallerContext(userId=f"u_{suffix}", deptId=dept, role=role, tenantId="t_demo")


def test_record_and_employee_isolation():
    a = _u("iso_a")
    b = _u("iso_b")
    audit.record("retrieve", a, target="q_a", meta={"hits": 1})
    audit.record("retrieve", b, target="q_b", meta={"hits": 2})
    rows_a = audit.get_logs(a)
    targets = {r["target"] for r in rows_a}
    assert "q_a" in targets and "q_b" not in targets  # 员工只能看自己


def test_dept_admin_sees_dept():
    emp = _u("dep_emp", dept="d_y")
    admin = _u("dep_admin", role="dept_admin", dept="d_y")
    audit.record("retrieve", emp, target="q_emp", meta={})
    rows = audit.get_logs(admin)
    assert any(r["target"] == "q_emp" for r in rows)  # 部门管理员看本部门


def test_platform_admin_sees_all():
    victim = _u(f"pa_{uuid.uuid4().hex[:6]}")
    audit.record("retrieve", victim, target="q_all", meta={})
    admin = _u("root", role="platform_admin", dept="d_z")
    rows = audit.get_logs(admin)
    assert any(r["target"] == "q_all" for r in rows)


def test_sanitize_blocks_sensitive_content():
    u = _u("san")
    audit.record("download", u, target="doc1",
                 meta={"content": "机密正文绝不可进审计", "title": "x"})
    rows = audit.get_logs(u, action="download")
    assert rows, "应至少有一条 download 审计"
    for r in rows:
        assert "content" not in r["meta_json"], "审计不得记录原文正文"


def test_available_is_true():
    assert audit.available() is True


def test_action_filter():
    u = _u("act")
    audit.record("retrieve", u, target="a1", meta={})
    audit.record("download", u, target="a2", meta={})
    dl = audit.get_logs(u, action="download")
    assert all(r["action"] == "download" for r in dl)
    assert any(r["target"] == "a2" for r in dl)
