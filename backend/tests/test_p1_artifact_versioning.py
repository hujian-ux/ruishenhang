"""P1-7 · 制品版本化与回滚（#302）契约测试。

覆盖：
1. 逻辑制品键 = `会话ID::问题序号::格式`；同一键反复生成（含 steer 中途补充要求）
   自动升版，全新问题成新链（问题序号 = 会话 user 轮数）。
2. 非破坏式回滚：把历史版本字节复制成「最新版本」，旧版本全部保留、可再回滚。
3. 版本列表降序、链聚合（list_chains）按键分组并解析出会话/问题序号/格式。
4. 上传件（kind=upload）不参与版本链（artifact_key 为空、version=0）。
5. 跨租户隔离：其它租户既不能列版本也不能回滚（404，不区分是否存在/无权）。
6. 全链路：同步 invoke 两次不同问题 → 两条链各 v1；流式 steer 同键 → v1→v2。

不依赖真模型：流式用例用 monkeypatch 把 run_agent_stream 换成确定性生成器。
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.core.security import create_dev_token
from app.main import app
from app.models.caller import CallerContext
from app.routers import invoke
from app.services import agent_runtime, agent_store, artifact, file_store

client = TestClient(app)

EMP = {"Authorization": create_dev_token("u_zhang", "d_sales", "employee", "t_emp")}
OTHER = {"Authorization": create_dev_token("u_li", "d_other", "employee", "t_other")}
ADMIN = {"Authorization": create_dev_token("p_admin", "d_platform", "platform_admin", "t_emp")}

SKILL_PROMPT = ("你是「经营复盘」执行专家。\n## 工作流程\n1. 量价分离 2. 异常归因\n"
                "## 硬规则\n主指标达标但支撑指标反向必须改判存疑。")


@pytest.fixture
def clean_files():
    saved = dict(file_store._FILES)
    file_store._FILES.clear()
    yield
    file_store._FILES.clear()
    file_store._FILES.update(saved)


@pytest.fixture
def versioned_skill():
    agent = agent_store.save({
        "agent_id": "agt_p1_art",
        "name": "经营复盘",
        "adapter_type": "llm",
        "description": "月度经营复盘",
        "knowledge_scope": "department",
        "status": "published",
        "category": "industry", "category_cn": "制造业", "sub_function": "",
        "system_prompt": SKILL_PROMPT,
    })
    yield agent
    agent_store._AGENTS.pop("agt_p1_art", None)


def _bytes(html: str) -> bytes:
    return artifact.build("html", html, {"agent_name": "经营复盘"})


# ── 1. 同键升版 / 版本字段 ─────────────────────────────────────────────────────
def test_put_same_key_increments_version(clean_files):
    c = CallerContext(userId="u", deptId="d", role="employee", tenantId="t")
    k = "sess_x::1::html"
    r1 = file_store.put(_bytes("v1"), "a.html", c, kind=file_store.KIND_ARTIFACT,
                        artifact_key=k)
    r2 = file_store.put(_bytes("v2"), "a.html", c, kind=file_store.KIND_ARTIFACT,
                        artifact_key=k)
    assert r1["version"] == 1 and r2["version"] == 2
    assert r2["prev_file_id"] == r1["file_id"]
    # public_view 暴露三字段
    pv = file_store.public_view(r2)
    assert pv["artifact_key"] == k and pv["version"] == 2 and pv["prev_file_id"] == r1["file_id"]


def test_upload_is_not_versioned(clean_files):
    c = CallerContext(userId="u", deptId="d", role="employee", tenantId="t")
    r = file_store.put(_bytes("x"), "up.xlsx", c, kind=file_store.KIND_UPLOAD)
    assert r["artifact_key"] == "" and r["version"] == 0 and r["prev_file_id"] == ""


def test_versions_descending(clean_files):
    c = CallerContext(userId="u", deptId="d", role="employee", tenantId="t")
    k = "sess_x::1::html"
    for i in range(1, 4):
        file_store.put(_bytes(f"v{i}"), "a.html", c, kind=file_store.KIND_ARTIFACT, artifact_key=k)
    vs = file_store.versions(k, c)
    assert [v["version"] for v in vs] == [3, 2, 1]     # 降序，最新在前
    assert len(vs) == 3


def test_list_chains_groups_by_key(clean_files):
    c = CallerContext(userId="u", deptId="d", role="employee", tenantId="t")
    file_store.put(_bytes("1"), "a.html", c, kind=file_store.KIND_ARTIFACT, artifact_key="sess_x::1::html")
    file_store.put(_bytes("2"), "a.html", c, kind=file_store.KIND_ARTIFACT, artifact_key="sess_x::2::html")
    chains = file_store.list_chains(c)
    assert len(chains) == 2
    by_key = {ch["artifact_key"]: ch for ch in chains}
    assert by_key["sess_x::1::html"]["q_index"] == 1
    assert by_key["sess_x::2::html"]["q_index"] == 2
    assert by_key["sess_x::1::html"]["version_count"] == 1
    # 按会话过滤
    assert len(file_store.list_chains(c, session_id="sess_x")) == 2
    assert len(file_store.list_chains(c, session_id="sess_y")) == 0


# ── 2. 非破坏式回滚 ──────────────────────────────────────────────────────────
def test_rollback_non_destructive(clean_files):
    c = CallerContext(userId="u", deptId="d", role="employee", tenantId="t")
    k = "sess_x::1::html"
    r1 = file_store.put(_bytes("原始结论"), "a.html", c, kind=file_store.KIND_ARTIFACT, artifact_key=k)
    r2 = file_store.put(_bytes("改过一版"), "a.html", c, kind=file_store.KIND_ARTIFACT, artifact_key=k)
    # 回滚到 v1
    new_rec = file_store.rollback_version(k, 1, c)
    assert new_rec is not None
    assert new_rec["version"] == 3                      # 时间线向前：max+1
    assert new_rec["prev_file_id"] == r2["file_id"]
    assert new_rec["meta"]["rollback"] is True
    assert new_rec["meta"]["rollback_source_version"] == 1
    # 内容确实复制了 v1 的字节（回滚 = 字节级复制，HTML 包裹一并复制）
    assert file_store.read(new_rec) == file_store.read(r1)
    # 旧版本全部保留，仍可下载 / 再回滚
    assert len(file_store.versions(k, c)) == 3
    again = file_store.rollback_version(k, 2, c)
    assert again is not None and again["version"] == 4


def test_rollback_unknown_key_returns_none(clean_files):
    c = CallerContext(userId="u", deptId="d", role="employee", tenantId="t")
    assert file_store.rollback_version("nope::1::html", 1, c) is None


# ── 3. 跨租户隔离 ────────────────────────────────────────────────────────────
def test_cross_tenant_cannot_see_versions_or_rollback(clean_files):
    owner = CallerContext(userId="u", deptId="d", role="employee", tenantId="t_emp")
    intruder = CallerContext(userId="x", deptId="y", role="employee", tenantId="t_other")
    k = "sess_x::1::html"
    file_store.put(_bytes("v1"), "a.html", owner, kind=file_store.KIND_ARTIFACT, artifact_key=k)
    file_store.put(_bytes("v2"), "a.html", owner, kind=file_store.KIND_ARTIFACT, artifact_key=k)
    # 入侵者既列不到版本，也回滚不了（can_access 拦截 → 404）
    assert file_store.versions(k, intruder) == []
    assert file_store.rollback_version(k, 1, intruder) is None


# ── 4. 全链路：HTTP 层 ───────────────────────────────────────────────────────
def test_invoke_two_questions_make_two_chains(clean_files, versioned_skill):
    """同一会话连续两个问题 → 两条制品链（q_index 1、2），各自 version 1。"""
    r1 = client.post("/api/v1/agent/invoke", headers=EMP,
                     json={"agent_id": "agt_p1_art", "input": "做 6 月复盘"})
    assert r1.status_code == 200, r1.text[:300]
    sid = r1.json()["data"]["session"]["session_id"]
    a1 = r1.json()["data"]["artifacts"][0]
    assert a1["artifact_key"] == f"{sid}::1::html" and a1["version"] == 1

    r2 = client.post("/api/v1/agent/invoke", headers=EMP,
                     json={"agent_id": "agt_p1_art", "input": "换 7 月再复盘一次",
                           "session_id": sid})
    assert r2.status_code == 200, r2.text[:300]
    a2 = r2.json()["data"]["artifacts"][0]
    # 全新问题 → 新链 q_index=2，仍是 version 1
    assert a2["artifact_key"] == f"{sid}::2::html" and a2["version"] == 1
    assert a1["artifact_key"] != a2["artifact_key"]


def test_rollback_endpoint_and_versions_endpoint(clean_files):
    """直接构造一条 3 版本的链（EMP 租户），验证版本与回滚端点。"""
    c = CallerContext(userId="u_zhang", deptId="d_sales", role="employee", tenantId="t_emp")
    k = "sess_http::1::html"
    for i in range(1, 4):
        file_store.put(_bytes(f"内容{i}"), "a.html", c, kind=file_store.KIND_ARTIFACT, artifact_key=k)

    # 版本列表端点
    rv = client.get(f"/api/v1/agent/files/artifacts/{k}/versions", headers=EMP)
    assert rv.status_code == 200, rv.text[:300]
    assert rv.json()["data"]["count"] == 3
    assert [v["version"] for v in rv.json()["data"]["versions"]] == [3, 2, 1]

    # 回滚到 v1
    rb = client.post(f"/api/v1/agent/files/artifacts/{k}/rollback?version=1", headers=EMP)
    assert rb.status_code == 200, rb.text[:300]
    nd = rb.json()["data"]
    assert nd["rolled_back_from"] == 1 and nd["new_version"] == 4
    new_id = nd["artifact"]["file_id"]
    assert "内容1" in client.get(f"/api/v1/agent/files/{new_id}", headers=EMP).content.decode("utf-8")

    # 回滚后版本数变 4
    rv2 = client.get(f"/api/v1/agent/files/artifacts/{k}/versions", headers=EMP)
    assert rv2.json()["data"]["count"] == 4


def test_cross_tenant_rollback_endpoint_404(clean_files):
    c = CallerContext(userId="u_zhang", deptId="d_sales", role="employee", tenantId="t_emp")
    k = "sess_x::1::html"
    file_store.put(_bytes("v1"), "a.html", c, kind=file_store.KIND_ARTIFACT, artifact_key=k)
    # 其它租户列版本 / 回滚都应 404
    assert client.get(f"/api/v1/agent/files/artifacts/{k}/versions", headers=OTHER).status_code == 404
    assert client.post(f"/api/v1/agent/files/artifacts/{k}/rollback?version=1",
                       headers=OTHER).status_code == 404


def test_chains_endpoint_lists_and_filters(clean_files, versioned_skill):
    r1 = client.post("/api/v1/agent/invoke", headers=EMP,
                     json={"agent_id": "agt_p1_art", "input": "第一问"})
    sid = r1.json()["data"]["session"]["session_id"]
    rc = client.get(f"/api/v1/agent/files/artifacts?session_id={sid}", headers=EMP)
    assert rc.status_code == 200, rc.text[:300]
    assert rc.json()["data"]["count"] == 1
    ch = rc.json()["data"]["chains"][0]
    assert ch["session_id"] == sid and ch["q_index"] == 1 and ch["format"] == "html"
    assert ch["version_count"] == 1 and ch["latest_version"] == 1


# ── 5. 流式 steer 同键升版 ──────────────────────────────────────────────────
async def _fake_stream(agent_id, user_input, caller, history=None, system_prompt=None,
                       adapter_type="", file_context="", web_fetch_top=0, should_cancel=None, **kw):
    """确定性完整流：meta → token → done（携带完整 result）。"""
    yield {"type": "meta", "citations": [], "web_sources": [], "web_search": None,
           "tool_calls": [], "query_rewritten": False}
    yield {"type": "token", "text": "结论："}
    yield {"type": "token", "text": user_input[-4:]}
    yield {"type": "done", "result": {
        "answer": f"结论：{user_input[-4:]}", "citations": [], "web_sources": [], "web_search": None,
        "query_rewritten": False, "turn_no": 1,
        "usage": {"model_id": "glm-4.6", "real": True, "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                  "error": "", "latency_ms": 1}, "tool_calls": [], "skill_applied": False,
        "approval_required": False}}


def _run_stream(monkeypatch, session_id=None, steer=False, steer_input=None, input_text="第一问"):
    body = {"agent_id": "agt_p1_art", "input": input_text, "stream": True}
    if session_id:
        body["session_id"] = session_id
    if steer:
        body["steer"] = True
        body["steer_input"] = steer_input
    r = client.post("/api/v1/agent/stream", headers=EMP, json=body)
    sid = None
    fin = None
    if r.status_code == 200:
        for ln in r.text.splitlines():
            if ln.startswith("data: "):
                try:
                    ev = json.loads(ln[5:].strip())
                except Exception:
                    continue
                if ev["type"] == "finalized":
                    sid = ev.get("session_id")
                    fin = ev
    return r, sid, fin


def test_stream_then_steer_same_key_bumps_version(monkeypatch, clean_files, versioned_skill):
    """流式第一问生成 v1；steer（不新增 user 轮）重生成命中同键 → v2。"""
    monkeypatch.setattr(agent_runtime, "run_agent_stream", _fake_stream)
    r1, sid, fin1 = _run_stream(monkeypatch, input_text="第一问")
    assert r1.status_code == 200 and sid, r1.text[:300]
    a1 = fin1["artifacts"][0]
    key1 = a1["artifact_key"]
    assert key1 == f"{sid}::1::html" and a1["version"] == 1

    # steer 同一会话：不新增 user 轮 → 同一 key 升版
    r2, _, fin2 = _run_stream(monkeypatch, session_id=sid, steer=True,
                              steer_input="改成表格形式", input_text="改成表格形式")
    assert r2.status_code == 200, r2.text[:300]
    a2 = fin2["artifacts"][0]
    assert a2["artifact_key"] == key1          # 同键
    assert a2["version"] == 2                  # 升版
    assert a2["prev_file_id"] == a1["file_id"]

    # 版本列表端点确认两版都在，且 steer 没新增 user 轮（q_index 仍是 1）
    rv = client.get(f"/api/v1/agent/files/artifacts/{key1}/versions", headers=EMP)
    assert [v["version"] for v in rv.json()["data"]["versions"]] == [2, 1]
