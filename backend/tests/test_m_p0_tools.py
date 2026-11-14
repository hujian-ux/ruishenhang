"""P0-3 沙箱边界 + P0-2 工具循环 测试。

不依赖真模型：工具循环通过脚本化 get_llm 驱动；run_agent 集成测试通过
monkeypatch model_gateway.chat_sync_ex 注入工具调用块。所有断言围绕「沙箱硬边界
先拦、高危转审批、循环自我纠正、注册表权威」展开。
"""
import asyncio
import pytest

from app.models.caller import CallerContext
from app.tools import sandbox, registry, loop as tool_loop
from app.services import agent_runtime, model_gateway

CALLER = CallerContext(userId="u1", deptId="d1", role="employee", tenantId="t1")


# ───────────────────────── P0-3 沙箱边界 ─────────────────────────
def test_sandbox_path_escape_rejected():
    r = sandbox.check("file_read", {"path": "../../etc/passwd"}, rules=sandbox.load_rules())
    assert r["allowed"] is False
    assert r["reason"].startswith("path_escape")


def test_sandbox_path_inside_allowed(tmp_path):
    rules = dict(sandbox.load_rules())
    rules["workspace_root"] = str(tmp_path)
    r = sandbox.check("file_read", {"path": "a/b.txt"}, rules=rules)
    assert r["allowed"] is True
    assert str(tmp_path) in r["resolved_path"]


def test_sandbox_secret_forbidden():
    r = sandbox.check("kb_write", {"api_key": "sk-1234567890abcdef"}, rules=sandbox.load_rules())
    assert r["allowed"] is False
    assert "secret_forbidden" in r["reason"]


def test_sandbox_secret_value_in_content():
    r = sandbox.check("kb_write", {"content": "token=AKIA1234567890ABCD"}, rules=sandbox.load_rules())
    assert r["allowed"] is False
    assert "secret_forbidden" in r["reason"]


def test_sandbox_shell_enabled_by_default_but_gated():
    # shell 在沙箱里「启用 + 白名单」才放行，放行后由 loop 高危分支转审批（不直执行）
    ok = sandbox.check("shell", {"command": "ls -la"}, rules=sandbox.load_rules())
    assert ok["allowed"] is True
    bad = sandbox.check("shell", {"command": "rm -rf /"}, rules=sandbox.load_rules())
    assert bad["allowed"] is False
    assert "command_not_whitelisted" in bad["reason"]


def test_sandbox_shell_whitelist(tmp_path):
    rules = dict(sandbox.load_rules())
    rules["shell"] = {"enabled": True, "command_whitelist": ["ls", "cat"]}
    ok = sandbox.check("shell", {"command": "ls -la"}, rules=rules)
    assert ok["allowed"] is True
    bad = sandbox.check("shell", {"command": "rm -rf /"}, rules=rules)
    assert bad["allowed"] is False
    assert "command_not_whitelisted" in bad["reason"]


def test_sandbox_tool_disabled():
    rules = dict(sandbox.load_rules())
    rules["tools"] = {"rag_retrieve": {"enabled": False}}
    r = sandbox.check("rag_retrieve", {}, rules=rules)
    assert r["allowed"] is False
    assert r["reason"] == "tool_disabled:rag_retrieve"


# ───────────────────────── P0-2 注册表 / 清单 ─────────────────────────
def test_registry_manifest_and_execute():
    def _double(args, caller):
        return {"ok": True, "content": str(args.get("x", 0) * 2)}
    registry.register("ut_double", "测试翻倍", {"x": "数字"}, _double)
    assert "ut_double" in registry.manifest_prompt()
    res = registry.execute("ut_double", {"x": 21}, None)
    assert res["ok"] is True
    assert res["content"] == "42"


def test_registry_unknown_tool():
    res = registry.execute("no_such_tool", {}, None)
    assert res["ok"] is False
    assert "unknown_or_unimplemented_tool" in res["error"]


# ───────────────────────── P0-2 工具循环 ─────────────────────────
def _fake_llm_scripted(script):
    state = {"i": 0}
    def fn(messages):
        i = state["i"]
        state["i"] += 1
        text = script[i] if i < len(script) else script[-1]
        return {"text": text, "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
    return fn


def test_loop_runs_tool_then_final():
    def _add(args, caller):
        return {"ok": True, "content": str(args.get("a", 0) + args.get("b", 0))}
    registry.register("ut_add", "加法", {"a": "x", "b": "y"}, _add)
    script = [
        '先调用工具：```tool_call\n{"tool":"ut_add","args":{"a":2,"b":3}}\n```',
        '结果是 5。',
    ]
    res = tool_loop.run_tool_loop("算2+3", "sys", CALLER, _fake_llm_scripted(script), max_rounds=6)
    assert res["answer"] == "结果是 5。"
    assert len(res["tool_calls"]) == 1
    assert res["tool_calls"][0]["tool"] == "ut_add"
    assert res["tool_calls"][0]["allowed"] is True
    assert res["tool_calls"][0]["content"] == "5"
    assert res["approval_required"] is False


def test_loop_high_risk_escalates():
    # 高危转审批：shell 在沙箱里「已启用且命令在白名单」→ 越过沙箱 → 被 loop 转审批并落单
    rules = dict(sandbox.load_rules())
    rules["shell"] = {"enabled": True, "command_whitelist": ["ls"]}
    script = ['跑命令：```tool_call\n{"tool":"shell","args":{"command":"ls"}}\n```']
    res = tool_loop.run_tool_loop("列目录", "sys", CALLER, _fake_llm_scripted(script),
                                  max_rounds=6, rules=rules, agent_id="agent_x")
    assert res["approval_required"] is True
    assert res["approval"]["tool"] == "shell"
    assert res["approval"]["ticket_id"]
    assert res["tool_calls"][0]["reason"] == "high_risk_escalated_to_approval"
    # 真正落到 approval_store（复用 M46/M47 基础设施）
    from app.services import approval_store
    t = approval_store.get_ticket(res["approval"]["ticket_id"])
    assert t["ticket_type"] == "tool_execution"
    assert t["tenant_id"] == "t1"
    assert t["payload"]["tool"] == "shell"
    assert t["payload"]["args"]["command"] == "ls"
    assert t["status"] == "pending"


def test_loop_high_risk_shell_disabled_in_sandbox_stays_rejected():
    # 若沙箱把 shell 关掉，则直接在沙箱层拒绝（不会落单）—— 双重闸一致
    rules = dict(sandbox.load_rules())
    rules["shell"] = {"enabled": False}
    script = ['跑命令：```tool_call\n{"tool":"shell","args":{"command":"ls"}}\n```']
    res = tool_loop.run_tool_loop("列目录", "sys", CALLER, _fake_llm_scripted(script),
                                  max_rounds=6, rules=rules)
    assert res["approval_required"] is False
    assert "shell_disabled" in res["tool_calls"][0]["reason"]


def test_loop_sandbox_reject_self_corrects():
    script = [
        '读文件：```tool_call\n{"tool":"file_read","args":{"path":"../../etc/passwd"}}\n```',
        '无法读取该文件，已按要求说明无权访问。',
    ]
    res = tool_loop.run_tool_loop("读那个文件", "sys", CALLER, _fake_llm_scripted(script), max_rounds=6)
    assert res["tool_calls"][0]["allowed"] is False
    assert "path_escape" in res["tool_calls"][0]["reason"]
    assert res["answer"] == "无法读取该文件，已按要求说明无权访问。"


def test_loop_unknown_tool_rejected():
    script = [
        '调用未知工具：```tool_call\n{"tool":"ut_ghost","args":{}}\n```',
        '该工具不可用，给出常规回答。',
    ]
    res = tool_loop.run_tool_loop("x", "sys", CALLER, _fake_llm_scripted(script), max_rounds=6)
    assert res["tool_calls"][0]["allowed"] is False
    assert "tool_not_registered" in res["tool_calls"][0]["reason"]
    assert res["answer"] == "该工具不可用，给出常规回答。"


def test_loop_max_rounds():
    # 每轮都要求同一工具且不收敛，应在 max_rounds 后停止
    script = ['```tool_call\n{"tool":"ut_ping","args":{}}\n```']
    registry.register("ut_ping", "ping", {}, lambda a, c: {"ok": True, "content": "pong"})
    res = tool_loop.run_tool_loop("ping", "sys", CALLER, _fake_llm_scripted(script), max_rounds=2)
    assert res["rounds"] <= 3  # range(max_rounds+1) 上限
    assert "达到上限" in res["answer"]


# ───────────────────────── 集成：run_agent(enable_tools=True) ─────────────────────────
def test_run_agent_with_tools(monkeypatch):
    def _ut(args, caller):
        return {"ok": True, "content": "产能=1200"}
    registry.register("ut_cap", "产能查询", {"sku": "编码"}, _ut)
    script = [
        '查询：```tool_call\n{"tool":"ut_cap","args":{"sku":"A1"}}\n```',
        '该 SKU 产能为 1200。',
    ]
    state = {"i": 0}
    def fake_chat(model_id, messages, **kw):
        i = state["i"]
        state["i"] += 1
        text = script[i] if i < len(script) else script[-1]
        return {"text": text, "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                "real": True, "model_id": "m", "routed_tier": None, "routed_model": None,
                "latency_ms": 1, "error": "", "throttled": False, "rotated": False,
                "attempts": [], "prompt_text": "", "completion_text": text}
    monkeypatch.setattr(model_gateway, "chat_sync_ex", fake_chat)

    res = agent_runtime.run_agent("agent_x", "查A1产能", CALLER, enable_tools=True)
    assert "产能为 1200" in res["answer"]
    assert res["tool_invocations"][0]["tool"] == "ut_cap"
    assert res["approval_required"] is False
    assert res["skill_applied"] is False


def test_run_agent_tools_off_is_default(monkeypatch):
    """enable_tools 默认 False：行为与旧版一致（不走工具分支，无 tool_invocations）。"""
    def fake_chat(model_id, messages, **kw):
        return {"text": "直接回答（无工具）", "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                "real": True, "model_id": "m", "routed_tier": None, "routed_model": None,
                "latency_ms": 1, "error": "", "throttled": False, "rotated": False,
                "attempts": [], "prompt_text": "", "completion_text": "直接回答（无工具）"}
    monkeypatch.setattr(model_gateway, "chat_sync_ex", fake_chat)

    res = agent_runtime.run_agent("agent_x", "普通问题", CALLER)  # enable_tools=None→False
    assert "tool_invocations" not in res
    assert "直接回答" in res["answer"]


# ───────────────────────── 高危工具审批落单（P0-2 收尾） ─────────────────────────
def test_approval_store_ticket_types():
    from app.services import approval_store
    t1 = approval_store.create_ticket("agent_pub", "u_req", "d1", tenant_id="t1")
    assert t1["ticket_type"] == "agent_publish"
    assert t1["payload"] == {}
    t2 = approval_store.create_tool_approval_ticket(CALLER, "agent_x", "shell", {"command": "date"})
    assert t2["ticket_type"] == "tool_execution"
    assert t2["tenant_id"] == "t1"
    assert t2["payload"]["tool"] == "shell"
    assert t2["payload"]["args"]["command"] == "date"
    assert t2["status"] == "pending"
    assert t2["decision_result"] is None


def test_tool_approval_execute_on_approve():
    """高危工具审批通过 → 真正执行工具并回写 decision_result；驳回则不执行。

    复刻 approval 路由的分支逻辑（router 只是编排这几步），验证端到端落单闭环。
    """
    from app.services import approval_store
    from app.tools import registry as tool_registry

    # 1) 落单
    ticket = approval_store.create_tool_approval_ticket(
        CALLER, "agent_x", "shell", {"command": "date"},
    )
    tid = ticket["approval_id"]

    # 2) 驳回 → 不执行
    approval_store.decide(tid, "reject", "admin", "platform_admin", "d1", "no", "t1")
    t = approval_store.get_ticket(tid)
    assert t["status"] == "rejected"
    assert t["decision_result"] is None

    # 3) 重新落单并批准 → 执行 date（白名单内）
    ticket2 = approval_store.create_tool_approval_ticket(
        CALLER, "agent_x", "shell", {"command": "date"},
    )
    tid2 = ticket2["approval_id"]
    approval_store.decide(tid2, "approve", "admin", "platform_admin", "d1", None, "t1")
    t2 = approval_store.get_ticket(tid2)
    assert t2["status"] == "approved"
    # router 分支：用原申请人 caller 执行（租户/部门边界保留）
    tcaller = CallerContext(userId=t2["requester_id"], deptId=t2["dept_id"],
                            role="user", tenantId=t2["tenant_id"])
    payload = t2["payload"]
    execution = tool_registry.execute(payload["tool"], payload["args"], tcaller)
    approval_store.record_execution(tid2, execution)
    assert execution["ok"] is True
    assert "2026" in execution["content"]  # date 命令成功执行，输出了年份
    assert approval_store.get_ticket(tid2)["decision_result"]["ok"] is True


# ───────────────────────── P0 收尾1：工具循环流式版本 ─────────────────────────
def _scripted_stream_factory(script):
    """构造确定性流式 LLM：按调用次数依次吐出 script 中的整段文本 + 一个 end 事件。"""
    state = {"i": 0}

    async def gen(messages):
        i = state["i"]
        state["i"] += 1
        text = script[i] if i < len(script) else script[-1]
        yield {"t": "delta", "text": text}
        yield {"t": "end", "usage": {"prompt_tokens": 1, "completion_tokens": 1},
               "real": True, "model_id": "m", "routed_tier": None, "routed_model": None,
               "latency_ms": 1, "error": "", "throttled": False}

    return gen


async def _collect_loop(gen_factory, **kw):
    evs = []
    async for ev in tool_loop.run_tool_loop_stream("算2+3", "sys", CALLER, gen_factory, **kw):
        evs.append(ev)
    return evs


def test_loop_stream_runs_tool_then_final():
    def _add(args, caller):
        return {"ok": True, "content": str(args.get("a", 0) + args.get("b", 0))}
    registry.register("ut_stream_add", "加法", {"a": "x", "b": "y"}, _add)
    script = [
        '调用：```tool_call\n{"tool":"ut_stream_add","args":{"a":2,"b":3}}\n```',
        '结果是5。',
    ]
    evs = asyncio.run(_collect_loop(_scripted_stream_factory(script), max_rounds=6))
    types = [e["type"] for e in evs]
    assert types[0] == "token"
    assert "tool_call" in types and "tool_result" in types and "done" in types
    done = evs[-1]
    assert done["type"] == "done"
    assert done["result"]["answer"] == "结果是5。"
    assert done["result"]["tool_invocations"][0]["content"] == "5"


def test_loop_stream_high_risk_escalated_with_session():
    """流式高危分支：发 tool_escalated 事件，且透传的 session_id 落到 ticket payload。"""
    rules = dict(sandbox.load_rules())
    rules["shell"] = {"enabled": True, "command_whitelist": ["date"]}
    script = ['```tool_call\n{"tool":"shell","args":{"command":"date"}}\n```']
    evs = asyncio.run(_collect_loop(_scripted_stream_factory(script), max_rounds=6,
                                    rules=rules, session_id="sess_xyz"))
    types = [e["type"] for e in evs]
    assert "tool_escalated" in types
    esc = next(e for e in evs if e["type"] == "tool_escalated")
    assert esc["ticket_id"]
    from app.services import approval_store
    t = approval_store.get_ticket(esc["ticket_id"])
    assert t["payload"]["session_id"] == "sess_xyz"


def test_loop_high_risk_captures_session_id():
    """P0 收尾2：run_tool_loop（同步）高危落单把 session_id 写进 ticket payload。"""
    rules = dict(sandbox.load_rules())
    rules["shell"] = {"enabled": True, "command_whitelist": ["ls"]}
    script = ['跑命令：```tool_call\n{"tool":"shell","args":{"command":"ls"}}\n```']
    res = tool_loop.run_tool_loop("列目录", "sys", CALLER, _fake_llm_scripted(script),
                                  max_rounds=6, rules=rules, agent_id="agent_x", session_id="sess_abc")
    from app.services import approval_store
    t = approval_store.get_ticket(res["approval"]["ticket_id"])
    assert t["payload"]["session_id"] == "sess_abc"


# ───────────────────────── P0 收尾2：审批通过后回灌原会话 ─────────────────────────
def test_approval_tool_execution_backfills_session(monkeypatch):
    """高危工具审批通过 → 真实执行 → 结果作为 assistant 轮次回灌原会话（翻历史可见）。"""
    import app.tools.registry as reg
    from app.services import approval_store, session_store
    from app.models.caller import CallerContext
    from fastapi.testclient import TestClient
    from app.core.security import create_dev_token
    from app.main import app

    # mock 工具执行，避免依赖真实 shell 白名单/命令
    monkeypatch.setattr(reg, "execute",
                        lambda tool, args, caller: {"ok": True, "content": "2026-11-13 周五"})

    client = TestClient(app)
    ADMIN = {"Authorization": create_dev_token("p_admin", "d_platform", "platform_admin")}
    caller = CallerContext(userId="p_admin", deptId="d_platform", role="platform_admin", tenantId="")
    sess = session_store.get_or_create("ag_pub_web_lookup", caller, None)
    sid = sess["session_id"]

    ticket = approval_store.create_tool_approval_ticket(
        caller, "ag_pub_web_lookup", "shell", {"command": "date"}, session_id=sid)
    tid = ticket["approval_id"]

    r = client.post(f"/api/v1/approval/{tid}/decide", headers=ADMIN, json={"decision": "approve"})
    assert r.status_code == 200, r.text[:200]
    body = r.json()["data"]
    assert body["status"] == "approved"
    assert body["execution"]["ok"] is True

    turns = session_store.history_of(sid)
    assert any("已审批执行" in t["content"] for t in turns), "原会话应出现回灌的执行结果轮次"
    assert any("2026-11-13" in t["content"] for t in turns), "回灌内容应包含工具执行结果"
