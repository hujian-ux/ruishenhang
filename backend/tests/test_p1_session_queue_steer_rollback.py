"""P1-5 · 会话队列 / 中途 steer / 轮次回滚（#300）契约测试。

不依赖真模型：用 monkeypatch 把 agent_runtime.run_agent_stream 换成确定性异步生成器，验证：
(1) 同一会话正在生成时，非 steer 的第二条流返回 409 session_busy（绝不并发打穿）；
(2) steer 请求会先取消本会话正在进行的旧流（ACTIVE_STREAMS 置 cancelled），再重生成；
(3) steer 重生成不新增 user 轮（把「上一轮问题 + 补充约束」合并进同一轮助手答案）；
(4) steer 把补充约束写进助手轮的 extra.steer_constraints；
(5) 轮次回滚（默认回退一轮 / 显式 turn / 越界夹取）截断 turns 并写穿持久化；
(6) 回滚沿用 get_session 隔离，非会话 owner 操作返回 403。

说明：FastAPI TestClient 同步消费整个流后才返回，无法在 HTTP 层构造「旧流未结束就发 steer」
的真实时序；因此「取消旧流」「会话忙锁」分别用白盒（直接操纵 ACTIVE_STREAMS / SESSION_LOCKS）
前置态验证，与 (3)(4)(5) 的端到端落账结果合起来等价于真实链路。
"""
from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from app.core.security import create_dev_token
from app.main import app
from app.routers import invoke
from app.services import agent_runtime, agent_store, session_store

client = TestClient(app)
ADMIN = {"Authorization": create_dev_token("p_admin", "d_platform", "platform_admin")}
OTHER = {"Authorization": create_dev_token("p_other", "d_other", "user")}
AGENT = "ag_pub_web_lookup"


@pytest.fixture(scope="module")
def seeded():
    before = set(agent_store._AGENTS)
    agent_store.ensure_public_skills()
    yield
    for aid in list(agent_store._AGENTS):
        if aid not in before:
            agent_store._AGENTS.pop(aid, None)


async def _fake_complete(agent_id, user_input, caller, history=None, system_prompt=None,
                         adapter_type="", file_context="", web_fetch_top=0, should_cancel=None, **kw):
    """确定性完整流：meta → 若干 token → done（携带完整 result）。"""
    yield {"type": "meta", "citations": [], "web_sources": [], "web_search": None,
           "tool_calls": [], "query_rewritten": False}
    for i in range(2):
        yield {"type": "token", "text": f"答{i}。"}
    yield {"type": "done", "result": {
        "answer": "答0。答1。", "citations": [], "web_sources": [], "web_search": None,
        "query_rewritten": False, "turn_no": 1,
        "usage": {"model_id": "glm-4.6", "real": True, "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                  "error": "", "latency_ms": 1}, "tool_calls": [], "skill_applied": False,
        "approval_required": False}}


def _run_stream(monkeypatch, session_id=None, steer=False, steer_input=None, input_text="第一问"):
    # 注意：fake 由调用方通过 monkeypatch.setattr 预先设置（便于在合并测试里换 recorder）
    body = {"agent_id": AGENT, "input": input_text, "stream": True}
    if session_id:
        body["session_id"] = session_id
    if steer:
        body["steer"] = True
        body["steer_input"] = steer_input
    r = client.post("/api/v1/agent/stream", headers=ADMIN, json=body)
    sid = None
    if r.status_code == 200:
        for ln in r.text.splitlines():
            if ln.startswith("data: "):
                try:
                    ev = json.loads(ln[5:].strip())
                except Exception:
                    continue
                if ev["type"] == "finalized":
                    sid = ev.get("session_id")
    return r, sid


def _get_turns(sid):
    r = client.get(f"/api/v1/agent/sessions/{sid}", headers=ADMIN)
    assert r.status_code == 200, r.text[:200]
    return r.json()["data"]["turns"]


def test_concurrent_stream_to_same_session_returns_409(monkeypatch, seeded):
    """同一会话正忙时，第二条非 steer 流必须 409（白盒：把会话锁置为已占用）。"""
    monkeypatch.setattr(agent_runtime, "run_agent_stream", _fake_complete)
    _, sid = _run_stream(monkeypatch, input_text="先问一句")
    assert sid
    # 白盒：模拟「旧流仍持有会话锁」的并发态
    lock = invoke._session_lock(sid)
    lock._locked = True
    try:
        r, _ = _run_stream(monkeypatch, session_id=sid, input_text="又问一句（应被拒）")
        assert r.status_code == 409, r.text[:200]
        assert "session_busy" in r.json()["detail"]
    finally:
        lock._locked = False


def test_steer_cancels_old_stream_and_regenerates(monkeypatch, seeded):
    """steer 先取消本会话旧流（ACTIVE_STREAMS 置 cancelled），再重生成且不新增 user 轮。

    后端口径：steer 只跳过「新增 user 轮」，但仍会追加一条新的 assistant 轮（重生成的答案），
    并把补充约束写进该助手轮 extra。因此轮数变为 [user, assistant, assistant]。
    """
    monkeypatch.setattr(agent_runtime, "run_agent_stream", _fake_complete)
    # 先跑一轮，拿到会话；此时 ACTIVE_STREAMS 已随流结束清空，手动伪造一条「进行中」旧流
    _, sid = _run_stream(monkeypatch, input_text="原始问题：写一份储能方案")
    assert sid
    rid_old = "st_old_in_progress"
    invoke.ACTIVE_STREAMS[rid_old] = {"cancelled": False, "caller_id": "p_admin", "session_id": sid}
    turns_before = _get_turns(sid)
    assert len(turns_before) == 2  # [user, assistant]

    # 发 steer：应取消 rid_old 并重生成（追加新 assistant 轮）
    r, sid2 = _run_stream(monkeypatch, session_id=sid, steer=True,
                          steer_input="改成更短的要点版", input_text="改成更短的要点版")
    assert r.status_code == 200, r.text[:200]
    assert sid2 == sid
    # 旧流被取消
    assert invoke.ACTIVE_STREAMS.get(rid_old, {}).get("cancelled") is True
    invoke.ACTIVE_STREAMS.pop(rid_old, None)

    # 不新增 user 轮，但追加一条新 assistant 轮：共 [user, assistant, assistant]
    turns_after = _get_turns(sid)
    assert len(turns_after) == 3, turns_after
    user_turns = [t for t in turns_after if t["role"] == "user"]
    assert len(user_turns) == 1, "steer 不应新增 user 轮"
    # 补充约束写进「最后一条」助手轮 extra；原助手轮不应带约束
    assert "steer_constraints" not in turns_after[1]
    last = turns_after[-1]
    assert last["role"] == "assistant"
    assert last.get("steer_constraints") == "改成更短的要点版"


def test_steer_merges_last_user_question(monkeypatch, seeded):
    """steer 把「上一轮用户问题 + 补充约束」合并成新输入（端到端捕获 run_agent_stream 收到的 input）。"""
    recorded = []

    async def _recorder(agent_id, user_input, caller, history=None, system_prompt=None,
                       adapter_type="", file_context="", web_fetch_top=0, should_cancel=None, **kw):
        recorded.append(user_input)
        async for ev in _fake_complete(agent_id, user_input, caller, history=history,
                                       system_prompt=system_prompt, adapter_type=adapter_type,
                                       file_context=file_context, web_fetch_top=web_fetch_top,
                                       should_cancel=should_cancel, **kw):
            yield ev

    monkeypatch.setattr(agent_runtime, "run_agent_stream", _recorder)
    _, sid = _run_stream(monkeypatch, input_text="原始问题：储能电站选址要点")
    assert sid
    # 第二次（steer）复用同一 recorder，不再被 _run_stream 内部覆盖
    r, _ = _run_stream(monkeypatch, session_id=sid, steer=True,
                       steer_input="补充：要考虑土地性质", input_text="补充：要考虑土地性质")
    assert r.status_code == 200
    # 第二条（steer）收到的输入应包含上一轮用户问题与补充约束
    assert len(recorded) >= 2
    merged = recorded[-1]
    assert "储能电站选址要点" in merged
    assert "要考虑土地性质" in merged
    assert "中途补充要求" in merged


def test_rollback_drops_last_round_and_persists(monkeypatch, seeded):
    """两轮对话后默认回滚一轮（删最后一轮助手回答），截断写穿持久化。"""
    _, sid = _run_stream(monkeypatch, input_text="第一轮问题")
    _, sid = _run_stream(monkeypatch, session_id=sid, input_text="第二轮问题")
    turns = _get_turns(sid)
    assert len(turns) == 4  # 两轮 user+assistant

    r = client.post(f"/api/v1/agent/sessions/{sid}/rollback", headers=ADMIN)
    assert r.status_code == 200, r.text[:200]
    body = r.json()["data"]
    assert body["kept_turns"] == 3 and body["dropped_turns"] == 1 and body["remaining"] == 3

    # 持久化：再拉一次仍是 3 轮
    assert len(_get_turns(sid)) == 3


def test_rollback_explicit_turn_and_clamping(monkeypatch, seeded):
    """显式 turn 与越界夹取：turn=0 清空、turn>=n 等价不回滚、turn=2 保留前 2 轮。"""
    _, sid = _run_stream(monkeypatch, input_text="Q1")
    _, sid = _run_stream(monkeypatch, session_id=sid, input_text="Q2")
    _, sid = _run_stream(monkeypatch, session_id=sid, input_text="Q3")
    assert len(_get_turns(sid)) == 6

    # turn=2：保留前 2 轮（删掉后 4 轮）
    r = client.post(f"/api/v1/agent/sessions/{sid}/rollback?turn=2", headers=ADMIN)
    assert r.status_code == 200
    assert r.json()["data"] == {"session_id": sid, "kept_turns": 2, "dropped_turns": 4, "remaining": 2}
    assert len(_get_turns(sid)) == 2

    # 重建到 6 轮再测越界
    for _ in range(2):
        _, sid = _run_stream(monkeypatch, session_id=sid, input_text="再补")
    assert len(_get_turns(sid)) == 6
    # turn=99 越界 → 夹取到 n=6（不回滚，dropped=0）
    r = client.post(f"/api/v1/agent/sessions/{sid}/rollback?turn=99", headers=ADMIN)
    assert r.json()["data"]["kept_turns"] == 6 and r.json()["data"]["dropped_turns"] == 0
    # turn=0 → 清空全部
    r = client.post(f"/api/v1/agent/sessions/{sid}/rollback?turn=0", headers=ADMIN)
    assert r.json()["data"]["kept_turns"] == 0 and r.json()["data"]["remaining"] == 0
    assert len(_get_turns(sid)) == 0


def test_rollback_requires_owner(monkeypatch, seeded):
    """回滚沿用 get_session 隔离：非会话 owner（不同用户）操作返回 403。"""
    _, sid = _run_stream(monkeypatch, input_text="属于 p_admin 的会话")
    r = client.post(f"/api/v1/agent/sessions/{sid}/rollback", headers=OTHER)
    assert r.status_code == 403, r.text[:200]
