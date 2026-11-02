"""M10 多租户规模化与异步导入的回归测试。

覆盖两件在规模化后才会暴露的事：
  1. **异步导入任务**：提交即返回、进度单调、终态可查、跨用户不可见、上限拒绝。
  2. **多租户隔离在「候选阶段预筛」后依然零泄露**，且不会因为跨部门候选把
     本部门的正确答案挤出窗口而返回空结果（这是 M10 压测发现并修掉的缺陷）。
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # type: ignore
from fastapi.testclient import TestClient  # type: ignore

from app.main import app  # noqa: E402
from app.models.caller import CallerContext  # noqa: E402
from app.services import ingest_jobs, kb_store, knowledge  # noqa: E402

client = TestClient(app)

# 本模块会往语料里灌近千篇文档。它们都在 t_demo/d_sales，如果不清理，
# 后面跑的 M4/M6 召回类断言会因为语料被稀释而假性失败（第一版就是这么挂的）。
_TITLE_PREFIXES = ("M10 任务", "M10 进度", "M10 私单", "M10 待删", "甲部", "乙部")


@pytest.fixture(scope="module", autouse=True)
def _cleanup_corpus():
    yield
    drop = [d for d in list(kb_store._DOCS.values())
            if any(str(d.get("title", "")).startswith(p) for p in _TITLE_PREFIXES)]
    for d in drop:
        kb_store.delete(d["doc_id"])
    ingest_jobs.reset()


def _login(device_id: str = "dev") -> str:
    r = client.post("/api/v1/auth/sso/callback",
                    json={"provider": "wecom", "code": device_id, "device_id": device_id, "state": "x"})
    return r.json()["data"]["token"]


def _docs(n: int, prefix: str) -> list:
    return [{"title": f"{prefix}-{i:03d}",
             "content": f"{prefix} 第 {i} 篇：区域备货上限 120 套，超储需区域经理签批。",
             "filename": f"{prefix}_{i:03d}.txt"} for i in range(n)]


# ── 异步导入任务 ────────────────────────────────────────────────────────────
def test_ingest_job_lifecycle():
    ingest_jobs.reset()
    token = _login()
    r = client.post("/api/v1/kb/ingest",
                    json={"documents": _docs(300, "M10 任务"), "scope": "department"},
                    headers={"Authorization": token})
    assert r.status_code == 200, r.text
    job = r.json()["data"]
    assert job["total"] == 300 and job["status"] in ("queued", "running")
    assert job["processed"] <= job["total"]

    done = ingest_jobs.wait(job["job_id"], timeout=120)
    assert done and done["status"] == "done", done
    assert done["processed"] == 300
    assert done["added"] == 300 and done["duplicated"] == 0
    assert done["elapsed_s"] is not None and done["elapsed_s"] >= 0

    got = client.get(f"/api/v1/kb/ingest/{job['job_id']}", headers={"Authorization": token})
    assert got.status_code == 200 and got.json()["data"]["status"] == "done"

    listed = client.get("/api/v1/kb/ingest?limit=5", headers={"Authorization": token}).json()["data"]
    assert job["job_id"] in [j["job_id"] for j in listed["jobs"]]


def test_ingest_job_progress_monotonic():
    ingest_jobs.reset()
    token = _login()
    r = client.post("/api/v1/kb/ingest",
                    json={"documents": _docs(500, "M10 进度"), "scope": "department"},
                    headers={"Authorization": token})
    job_id = r.json()["data"]["job_id"]
    samples = []
    for _ in range(6):
        j = client.get(f"/api/v1/kb/ingest/{job_id}", headers={"Authorization": token}).json()["data"]
        samples.append(j["processed"])
        if j["status"] in ("done", "failed"):
            break
    assert samples == sorted(samples), samples      # 进度只能往上走
    assert all(0 <= s <= 500 for s in samples)
    ingest_jobs.wait(job_id, timeout=120)


def test_ingest_job_rejects_oversize():
    token = _login()
    r = client.post("/api/v1/kb/ingest",
                    json={"documents": _docs(ingest_jobs.MAX_DOCS + 1, "M10 超限"),
                          "scope": "department"},
                    headers={"Authorization": token})
    assert r.status_code == 413
    assert "最多" in r.json()["detail"]


def test_ingest_job_invisible_to_other_user():
    ingest_jobs.reset()
    token = _login()
    r = client.post("/api/v1/kb/ingest",
                    json={"documents": _docs(30, "M10 私单"), "scope": "department"},
                    headers={"Authorization": token})
    job_id = r.json()["data"]["job_id"]
    ingest_jobs.wait(job_id, timeout=60)
    # dev 登录只有两个身份（员工 u_1024 / 平台管理员 u_9000），所以 RBAC 在服务层验：
    # 同租户的别人看不到、平台管理员看得到、跨租户连管理员也看不到。
    peer = CallerContext(userId="u_peer", deptId="d_sales", role="employee", tenantId="t_demo")
    assert ingest_jobs.get(job_id, peer) is None, "同租户的其他用户不该看到别人的任务"
    boss = CallerContext(userId="u_9000", deptId="d_it", role="platform_admin", tenantId="t_demo")
    assert ingest_jobs.get(job_id, boss) is not None, "平台管理员应可查看本租户任务"
    stranger = CallerContext(userId="u_x", deptId="d_x", role="platform_admin", tenantId="t_other")
    assert ingest_jobs.get(job_id, stranger) is None, "跨租户即便管理员也不可见"


# ── 多租户隔离与预筛 ────────────────────────────────────────────────────────
def _seed_depts():
    """两个部门各灌 120 篇「同题不同部门」的文档（正文不同，避免被去重）。"""
    for dept, tag in (("d_m10_a", "甲部"), ("d_m10_b", "乙部")):
        caller = CallerContext(userId=f"u_{dept}", deptId=dept, role="dept_admin",
                               tenantId="t_demo")
        rows = [{"title": f"{tag}区域备货规则（2026-{i:03d}",
                 "content": f"{tag}第 {i} 号文件：华南大区区域备货上限 {120 + i} 套，"
                            f"超储需区域经理签批，执行口径以本文件为准。"} for i in range(120)]
        kb_store.register_many(rows, caller, scope="department", dept_id=dept)


def test_cross_dept_zero_leak_and_no_empty():
    _seed_depts()
    query = "华南大区区域备货上限超过多少需要区域经理签批"
    for dept in ("d_m10_a", "d_m10_b"):
        caller = CallerContext(userId=f"u_{dept}", deptId=dept, role="dept_admin",
                               tenantId="t_demo")
        hits, _filtered = knowledge.retrieve(query, caller, top_k=10)
        # 1) 不能空：跨部门候选再多，也不该把本部门的答案挤没（M10 修的就是这个）
        assert hits, f"{dept} 检索返回空结果——候选窗口被别的部门吃掉了"
        # 2) 零泄露：结果里只能有本部门 / global / 本人私有
        for h in hits:
            if h["scope"] == "department":
                assert h["dept"] == dept, f"{dept} 收到了 {h['dept']} 的文档"
            assert h["tenant_id"] == "t_demo"


def test_prefilter_mask_reused_and_invalidated():
    _seed_depts()
    caller = CallerContext(userId="u_d_m10_a", deptId="d_m10_a", role="dept_admin",
                           tenantId="t_demo")
    knowledge.retrieve("区域备货上限", caller, top_k=5)
    cached = dict(knowledge._MASK_CACHE)
    assert cached, "第一次检索应建立身份掩码"
    knowledge.retrieve("区域备货上限", caller, top_k=5)
    assert knowledge._MASK_CACHE == cached, "第二次检索应复用掩码，不重建"

    kb_store.register("M10 掩码失效验证", "新增文档后掩码必须重建，否则新人看不到新制度。",
                      "department", CallerContext(userId="u_d_m10_a", deptId="d_m10_a",
                                                  role="dept_admin", tenantId="t_demo"))
    assert not knowledge._MASK_CACHE, "写入后掩码应被清空"


def test_remove_after_async_ingest_not_recalled():
    ingest_jobs.reset()
    token = _login()
    r = client.post("/api/v1/kb/ingest",
                    json={"documents": [{"title": "M10 待删", "content": "M10 待删文档：过期的备货口径。"}],
                          "scope": "department"},
                    headers={"Authorization": token})
    job_id = r.json()["data"]["job_id"]
    ingest_jobs.wait(job_id, timeout=60)
    docs = client.get("/api/v1/kb/documents", headers={"Authorization": token}).json()["data"]["documents"]
    target = next((d for d in docs if d["title"] == "M10 待删"), None)
    assert target, "异步导入的文档应出现在列表里"

    assert client.delete(f"/api/v1/kb/documents/{target['doc_id']}",
                         headers={"Authorization": token}).status_code == 200
    caller = CallerContext(userId="u_1024", deptId="d_sales", role="dept_admin", tenantId="t_demo")
    hits, _ = knowledge.retrieve("过期的备货口径", caller, top_k=10)
    assert target["doc_id"] not in {h["doc_id"] for h in hits}
