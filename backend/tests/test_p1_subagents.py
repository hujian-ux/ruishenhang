"""P1-9 · 子代理 / 专家执行（#304）契约测试。

覆盖：
1. dispatch：专家人设注入 / 三段式 messages / 上游上下文贯穿 / opt-in few-shot 与材料、
   诚实降级（real=False 不伪造结论与 answer）、异常转结构化失败。
2. 安全：限深防递归、跨租户/跨部门不可委派、内置助手拒绝、不存在统一 404、
   task 空/超长 400、不污染主会话 turns 与 q_index（不写 session_store）。
3. batch：并发委派、单条失败不影响其它、超限拒绝、用量聚合。
4. 台账：RBAC 隔离（platform_admin 全量 / dept_admin 本部门 / employee 本人）、
   status 过滤、stats 聚合、明细不可见 404。
5. 工具：delegate_expert 注册 + 进 manifest + guardrails 白名单正常放行 +
   执行体走 dispatch 且 depth=1。
6. HTTP：/stats 不被路径参数吞、code=0 包装、无鉴权 401、404 不区分、各端点透传。

测试隔离：清空 agent_store._AGENTS 与 subagents._RUNS，monkeypatch 模型网关
（不联网、不依赖真 key）与 audit.record（不落 SQLite）。
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.core.security import create_dev_token
from app.main import app
from app.models.caller import CallerContext
from app.services import (agent_store, audit, guardrails, model_gateway,
                          session_store, subagents)
from app.tools import registry

client = TestClient(app)
API = "/api/v1/experts"

ADMIN_H = {"Authorization": create_dev_token("p_admin", "d_platform", "platform_admin", "t_sys")}
EMP_A_H = {"Authorization": create_dev_token("u_a", "d_a", "employee", "t_a")}
EMP_B_H = {"Authorization": create_dev_token("u_b", "d_x", "employee", "t_b")}

ADMIN = CallerContext(userId="p_admin", deptId="d_platform", role="platform_admin", tenantId="t_sys")
EMP_A = CallerContext(userId="u_a", deptId="d_a", role="employee", tenantId="t_a")
EMP_B = CallerContext(userId="u_b", deptId="d_x", role="employee", tenantId="t_b")
DEPT_ADMIN_A = CallerContext(userId="d_boss", deptId="d_a", role="dept_admin", tenantId="t_a")

PERSONA = "你是「经营复盘」专家。\n## 硬规则\n主指标达标但支撑指标反向必须改判存疑。"


# ── fixtures / helpers ──────────────────────────────────────────────────────
@pytest.fixture
def env(monkeypatch):
    """清场 + 桩：模型网关可控，审计不落盘。"""
    saved_agents = dict(agent_store._AGENTS)
    saved_sessions = dict(session_store._SESSIONS)
    agent_store._AGENTS.clear()
    session_store._SESSIONS.clear()
    subagents.reset_state()

    monkeypatch.setattr(audit, "record", lambda *a, **k: "trace_ok")

    calls = []

    def fake_llm(messages, **kw):
        calls.append({"messages": messages, "kw": kw})
        return {"text": "结论：营收环比下滑 3%，主因是量而非价。", "real": True,
                "model_id": "glm-4.6", "usage": {"prompt_tokens": 120, "completion_tokens": 30},
                "error": ""}

    monkeypatch.setattr(model_gateway, "routed_chat_sync_ex", fake_llm)
    yield {"calls": calls}
    session_store._SESSIONS.clear()
    session_store._SESSIONS.update(saved_sessions)
    agent_store._AGENTS.clear()
    agent_store._AGENTS.update(saved_agents)
    subagents.reset_state()


def _put(agent_id: str, **kw):
    a = {
        "agent_id": agent_id,
        "name": kw.pop("name", agent_id),
        "description": "测试用专家技能包",
        "sub_function": "经营分析",
        "system_prompt": PERSONA,
        "adapter_type": "llm",
        "status": "published",
        "category": "industry",
        "category_cn": "制造业",
        "role_primary": "manager",
        "knowledge_scope": "global",
        "tenant_id": "",              # 平台共享，全租户可见
        "registered_by": "bootstrap",
        "dept_id": "d_platform",
    }
    a.update(kw)
    agent_store._AGENTS[agent_id] = a
    return a


def _seed():
    _put("ex_finance", name="经营复盘", role_primary="manager")
    _put("ex_quality", name="品质异常分析", category_cn="制造业", role_primary="quality")
    _put("ex_public", name="通用文案", public=True, category="public",
         category_cn="公共库", role_primary="")
    _put("ex_private", name="私有专家", tenant_id="t_a", knowledge_scope="department",
         dept_id="d_a", registered_by="u_a")
    _put("ex_dept_b", name="B 部门专家", tenant_id="", knowledge_scope="department",
         dept_id="d_b", registered_by="bootstrap")
    _put("bi", name="通用助手", builtin=True)


# ══════════════════════════════════════════════════════════════════════════
# 1. dispatch 人设 / 上下文 / 降级
# ══════════════════════════════════════════════════════════════════════════
def test_dispatch_injects_persona_and_context(env):
    _put("ex1")
    r = subagents.dispatch("ex1", "做 6 月复盘", ADMIN,
                           context="主任务：月度经营会", parent_agent_id="main_agt")
    assert r["ok"] is True and r["real"] is True and r["status"] == "ok"
    assert r["answer"].startswith("结论：")
    assert r["parent_agent_id"] == "main_agt" and r["source"] == "api"
    msgs = env["calls"][0]["messages"]
    assert len(msgs) == 2 and msgs[0]["role"] == "system" and msgs[1]["role"] == "user"
    # 人设 + 子代理约束都在 system
    assert "经营复盘" in msgs[0]["content"] and "硬规则" in msgs[0]["content"]
    assert "子代理" in msgs[0]["content"] and "不得再委派" in msgs[0]["content"]
    # 三段式 user：任务 + 上游上下文
    assert "做 6 月复盘" in msgs[1]["content"]
    assert "月度经营会" in msgs[1]["content"]


def test_dispatch_honest_degrade_no_fake_answer(env, monkeypatch):
    _put("ex1")
    monkeypatch.setattr(model_gateway, "routed_chat_sync_ex",
                        lambda messages, **kw: {"text": "stub 伪造文本", "real": False,
                                                "error": "未配置模型密钥", "usage": {},
                                                "model_id": "glm-4.6"})
    r = subagents.dispatch("ex1", "复盘", ADMIN)
    assert r["ok"] is False and r["status"] == "unavailable" and r["real"] is False
    assert r["answer"] == "" and r["error"] == "未配置模型密钥"
    assert "stub" not in str(r)


def test_dispatch_empty_answer_is_unavailable(env, monkeypatch):
    _put("ex1")
    monkeypatch.setattr(model_gateway, "routed_chat_sync_ex",
                        lambda messages, **kw: {"text": "  ", "real": True,
                                                "error": "", "usage": {}, "model_id": "glm-4.6"})
    r = subagents.dispatch("ex1", "复盘", ADMIN)
    assert r["ok"] is False and r["status"] == "unavailable" and r["answer"] == ""


def test_dispatch_llm_exception_becomes_structured_failure(env, monkeypatch):
    _put("ex1")

    def boom(messages, **kw):
        raise RuntimeError("上游连接断开")

    monkeypatch.setattr(model_gateway, "routed_chat_sync_ex", boom)
    r = subagents.dispatch("ex1", "复盘", ADMIN)
    assert r["ok"] is False and r["status"] == "error" and r["answer"] == ""
    assert "RuntimeError" in r["error"]


def test_dispatch_persona_calls_opt_in_composers(env, monkeypatch):
    """人设组装与 agent_runtime 同源：无条件调用两个 compose_*（开关由它们内部判定）。

    桩掉 compose_* 本身而非 settings 全局开关，避免全量下被其它测试污染开关状态；
    同时验证「两个 compose 都被调用、返回值串进 system_prompt」这一契约。
    """
    _put("ex1")
    seen = []

    def fake_fewshot(sp, agent):
        seen.append("fewshot")
        return sp + "\n## FEWSHOT_MARK\n样例问"

    def fake_materials(sp, agent):
        seen.append("materials")
        return sp + "\n## MATERIALS_MARK\n验收标准"

    monkeypatch.setattr(subagents.fewshot, "compose_with_fewshot", fake_fewshot)
    monkeypatch.setattr(subagents.solution_materials, "compose_materials", fake_materials)
    r = subagents.dispatch("ex1", "复盘", ADMIN)
    assert r["ok"] is True
    assert seen == ["fewshot", "materials"]
    sys_prompt = env["calls"][0]["messages"][0]["content"]
    assert "FEWSHOT_MARK" in sys_prompt and "MATERIALS_MARK" in sys_prompt
    assert "子代理" in sys_prompt   # 子代理约束仍接在末尾


def test_dispatch_does_not_touch_session_turns(env):
    """独立上下文：委派不能写主会话 turns / 不能占 q_index。"""
    _put("ex1")
    sess = session_store.create_session("ex1", ADMIN, session_id="sess_main")
    session_store.append_turn(sess["session_id"], "user", "原始问题")
    before = [dict(t) for t in session_store.history_of(sess["session_id"])]
    subagents.dispatch("ex1", "复盘", ADMIN)
    after = session_store.history_of(sess["session_id"])
    assert after == before and len(after) == 1


# ══════════════════════════════════════════════════════════════════════════
# 2. 安全 / 限深 / 可见域
# ══════════════════════════════════════════════════════════════════════════
def test_dispatch_depth_limit_blocks_recursion(env):
    _put("ex1")
    r = subagents.dispatch("ex1", "复盘", ADMIN, depth=subagents.MAX_DEPTH - 1)
    assert r["ok"] is True and r["depth"] == subagents.MAX_DEPTH - 1
    with pytest.raises(HTTPException) as e:
        subagents.dispatch("ex1", "再委派", ADMIN, depth=subagents.MAX_DEPTH)
    assert e.value.status_code == 400 and "深度超限" in str(e.value.detail)


def test_dispatch_rejects_builtin_and_missing(env):
    _put("bi", builtin=True)
    for aid in ("bi", "not_exist"):
        with pytest.raises(HTTPException) as e:
            subagents.dispatch(aid, "复盘", ADMIN)
        assert e.value.status_code == 404


def test_dispatch_cross_tenant_and_cross_dept_invisible(env):
    _seed()
    # EMP_B（t_b）不可委派 t_a 私有包
    with pytest.raises(HTTPException) as e:
        subagents.dispatch("ex_private", "复盘", EMP_B)
    assert e.value.status_code == 404
    # platform_admin / 本租户本人可委派
    assert subagents.dispatch("ex_private", "复盘", ADMIN)["ok"] is True
    assert subagents.dispatch("ex_private", "复盘", EMP_A)["ok"] is True
    # 部门级共享包：跨部门 employee 与 dept_admin 都不可见（dept_id 不匹配）
    with pytest.raises(HTTPException) as e3:
        subagents.dispatch("ex_dept_b", "复盘", EMP_A)
    assert e3.value.status_code == 404
    with pytest.raises(HTTPException) as e4:
        subagents.dispatch("ex_dept_b", "复盘", DEPT_ADMIN_A)
    assert e4.value.status_code == 404
    # 本部门 dept_admin 可见（customer 语义：dept_id 匹配即放行）
    DEPT_ADMIN_B = CallerContext(userId="boss_b", deptId="d_b", role="dept_admin", tenantId="t_b")
    assert subagents.dispatch("ex_dept_b", "复盘", DEPT_ADMIN_B)["ok"] is True


def test_dispatch_empty_or_overlong_task(env):
    _put("ex1")
    for bad in ("", "   "):
        with pytest.raises(HTTPException) as e:
            subagents.dispatch("ex1", bad, ADMIN)
        assert e.value.status_code == 400
    with pytest.raises(HTTPException) as e2:
        subagents.dispatch("ex1", "x" * (subagents.MAX_TASK_CHARS + 1), ADMIN)
    assert e2.value.status_code == 400


def test_context_is_truncated(env):
    _put("ex1")
    long_ctx = "上" * (subagents.MAX_CONTEXT_CHARS + 500)
    r = subagents.dispatch("ex1", "复盘", ADMIN, context=long_ctx)
    assert r["context_len"] == subagents.MAX_CONTEXT_CHARS


# ══════════════════════════════════════════════════════════════════════════
# 3. list_experts / batch
# ══════════════════════════════════════════════════════════════════════════
def test_list_experts_excludes_builtin_and_honors_visibility(env):
    _seed()
    d = subagents.list_experts(ADMIN)
    ids = {x["agent_id"] for x in d["items"]}
    assert "bi" not in ids and "ex_public" in ids
    assert d["total"] == 5   # _seed 造 6 个，builtin 的 bi 被排除
    # 公共库可按 kind 过滤
    assert {x["agent_id"] for x in subagents.list_experts(ADMIN, kind="public")["items"]} == {"ex_public"}
    # 岗位过滤：本岗 + 公共库
    q = subagents.list_experts(ADMIN, role="quality")
    assert {x["agent_id"] for x in q["items"]} == {"ex_quality", "ex_public"}
    # 搜索 + 行业
    assert {x["agent_id"] for x in subagents.list_experts(ADMIN, q="品质")["items"]} == {"ex_quality"}


def test_list_experts_employee_visibility(env):
    _seed()
    ids = {x["agent_id"] for x in subagents.list_experts(EMP_A)["items"]}
    assert "ex_private" in ids          # 本人注册
    assert "ex_dept_b" not in ids       # 跨部门不可见
    assert "bi" not in ids


def test_dispatch_batch_concurrent_and_aggregate(env):
    _seed()
    tasks = [{"agent_id": "ex_finance", "task": "复盘 6 月"},
             {"agent_id": "ex_quality", "task": "分析异常"},
             {"agent_id": "ex_public", "task": "写文案"}]
    d = subagents.dispatch_batch(ADMIN, tasks, context="主任务：月度经营会")
    assert d["total"] == 3 and d["ok_count"] == 3 and d["real_count"] == 3
    assert d["usage"]["calls"] == 3 and d["usage"]["prompt_tokens"] == 360
    assert all(i["batch_id"] == d["batch_id"] for i in d["items"])
    assert all(i["source"] == "batch" for i in d["items"])
    # 上游共享上下文贯穿到每个专家
    assert all("月度经营会" in c["messages"][1]["content"] for c in env["calls"])


def test_dispatch_batch_single_failure_does_not_block_others(env):
    _seed()
    d = subagents.dispatch_batch(ADMIN, [
        {"agent_id": "ex_finance", "task": "复盘 6 月"},
        {"agent_id": "not_exist", "task": "不该崩"},
        {"agent_id": "ex_quality", "task": "分析异常"},
    ])
    assert d["total"] == 3 and d["ok_count"] == 2 and d["failed_count"] == 1
    assert d["items"][1]["status"] == "denied" and d["items"][1]["answer"] == ""


def test_dispatch_batch_limits(env):
    _seed()
    with pytest.raises(HTTPException) as e:
        subagents.dispatch_batch(ADMIN, [])
    assert e.value.status_code == 400
    with pytest.raises(HTTPException) as e2:
        subagents.dispatch_batch(ADMIN, [{"agent_id": "ex_finance", "task": "x"}] * (subagents.MAX_BATCH + 1))
    assert e2.value.status_code == 400


# ══════════════════════════════════════════════════════════════════════════
# 4. 台账 RBAC / stats
# ══════════════════════════════════════════════════════════════════════════
def test_runs_rbac_isolation(env):
    _put("ex1")
    ra = subagents.dispatch("ex1", "A 的任务", EMP_A)
    rb = subagents.dispatch("ex1", "B 的任务", EMP_B)
    assert subagents.list_runs(EMP_A)["total"] == 1
    assert subagents.list_runs(EMP_A)["items"][0]["run_id"] == ra["run_id"]
    assert subagents.list_runs(EMP_B)["items"][0]["run_id"] == rb["run_id"]
    assert subagents.list_runs(ADMIN)["total"] == 2          # 超管全量
    assert subagents.get_run(EMP_A, rb["run_id"]) is None     # 越权明细 None
    assert subagents.get_run(ADMIN, rb["run_id"]) is not None


def test_runs_status_filter_and_pagination(env):
    _put("ex1")
    subagents.dispatch("ex1", "t1", ADMIN)
    subagents.dispatch("ex1", "t2", ADMIN)
    assert subagents.list_runs(ADMIN, status="ok")["total"] == 2
    assert subagents.list_runs(ADMIN, status="denied")["total"] == 0
    p = subagents.list_runs(ADMIN, limit=1, offset=1)
    assert p["total"] == 2 and p["count"] == 1
    assert subagents.list_runs(ADMIN, agent_id="nope")["total"] == 0


def test_stats_aggregation(env):
    _put("ex1")
    subagents.dispatch("ex1", "t1", ADMIN)
    subagents.dispatch("ex1", "t2", ADMIN)
    s = subagents.stats(ADMIN)
    assert s["total"] == 2 and s["ok"] == 2 and s["real_rate"] == 1.0
    assert s["usage"]["prompt_tokens"] == 240
    assert s["by_agent"][0]["agent_id"] == "ex1" and s["by_agent"][0]["count"] == 2


def test_stats_counts_degraded_runs(env, monkeypatch):
    _put("ex1")
    monkeypatch.setattr(model_gateway, "routed_chat_sync_ex",
                        lambda messages, **kw: {"text": "stub", "real": False,
                                                "error": "无密钥", "usage": {}, "model_id": ""})
    subagents.dispatch("ex1", "t1", ADMIN)
    s = subagents.stats(ADMIN)
    assert s["total"] == 1 and s["ok"] == 0 and s["unavailable"] == 1 and s["real_rate"] == 0.0


# ══════════════════════════════════════════════════════════════════════════
# 5. 委派工具
# ══════════════════════════════════════════════════════════════════════════
def test_delegate_tool_registered_and_allowed(env):
    t = registry.get(subagents.TOOL_NAME)
    assert t is not None and t["risk"] == "normal"
    assert subagents.TOOL_NAME in registry.manifest_prompt()
    g = guardrails.guard_tool_call(subagents.TOOL_NAME,
                                   {"agent_id": "ex1", "task": "复盘"}, ADMIN)
    assert g["allowed"] is True and g["reason"] == "ok"


def test_delegate_tool_executes_dispatch_with_depth1(env):
    _put("ex1", name="经营复盘")
    r = registry.execute(subagents.TOOL_NAME,
                         {"agent_id": "ex1", "task": "工具内委派", "context": "上游背景"},
                         ADMIN)
    assert r["ok"] is True and "经营复盘" in r["content"]
    run = subagents.list_runs(ADMIN)["items"][0]
    assert run["source"] == "tool" and run["depth"] == 1
    assert "上游背景" in env["calls"][0]["messages"][1]["content"]


def test_delegate_tool_structured_failure_on_denied_and_degraded(env, monkeypatch):
    _put("ex1")
    bad = registry.execute(subagents.TOOL_NAME, {"agent_id": "nope", "task": "x"}, ADMIN)
    assert bad["ok"] is False and "delegate_denied" in bad["error"]
    empty = registry.execute(subagents.TOOL_NAME, {"task": "x"}, ADMIN)
    assert empty["ok"] is False
    monkeypatch.setattr(model_gateway, "routed_chat_sync_ex",
                        lambda messages, **kw: {"text": "stub", "real": False,
                                                "error": "无密钥", "usage": {}, "model_id": ""})
    deg = registry.execute(subagents.TOOL_NAME, {"agent_id": "ex1", "task": "x"}, ADMIN)
    assert deg["ok"] is False and "delegate_unavailable" in deg["error"]


# ══════════════════════════════════════════════════════════════════════════
# 6. HTTP 路由
# ══════════════════════════════════════════════════════════════════════════
def test_http_stats_route_not_swallowed_by_path_param(env):
    _put("ex1")
    r = client.get(f"{API}/stats", headers=ADMIN_H)
    assert r.status_code == 200 and r.json()["code"] == 0
    assert "by_agent" in r.json()["data"] and "usage" in r.json()["data"]


def test_http_experts_dispatch_and_runs(env):
    _put("ex1")
    idx = client.get(API, headers=ADMIN_H)
    assert idx.status_code == 200 and idx.json()["data"]["total"] == 1
    r = client.post(f"{API}/dispatch", headers=ADMIN_H,
                    json={"agent_id": "ex1", "task": "做 6 月复盘", "context": "经营会"})
    assert r.status_code == 200 and r.json()["code"] == 0
    d = r.json()["data"]
    assert d["ok"] is True and d["answer"].startswith("结论：")
    rid = d["run_id"]
    # 明细端点（两段路径，不被静态段吞）
    assert client.get(f"{API}/runs/{rid}", headers=ADMIN_H).json()["data"]["run_id"] == rid
    assert client.get(f"{API}/runs", headers=ADMIN_H).json()["data"]["total"] == 1
    # 批量
    b = client.post(f"{API}/dispatch-batch", headers=ADMIN_H, json={
        "tasks": [{"agent_id": "ex1", "task": "复盘7月"}, {"agent_id": "ex1", "task": "做归因"}],
        "context": "共享背景",
    })
    assert b.status_code == 200 and b.json()["data"]["total"] == 2


def test_http_requires_auth_and_404(env):
    _put("ex1")
    assert client.get(API).status_code == 401
    assert client.get(f"{API}/stats").status_code == 401
    assert client.post(f"{API}/dispatch", headers=ADMIN_H,
                       json={"agent_id": "nope", "task": "x"}).status_code == 404
    assert client.get(f"{API}/runs/nope", headers=ADMIN_H).status_code == 404
    # 空任务 → 400
    assert client.post(f"{API}/dispatch", headers=ADMIN_H,
                       json={"agent_id": "ex1", "task": ""}).status_code == 400


def test_http_employee_cannot_dispatch_private_expert(env):
    _seed()
    # EMP_B 委派 t_a 私有专家 → 404（不区分不存在/无权限）
    assert client.post(f"{API}/dispatch", headers=EMP_B_H,
                       json={"agent_id": "ex_private", "task": "x"}).status_code == 404
    # EMP_A 目录里看不到部门 B 私有包
    ids = {x["agent_id"] for x in client.get(API, headers=EMP_A_H).json()["data"]["items"]}
    assert "ex_dept_b" not in ids
