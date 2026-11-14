"""P1-12 · 上下文窗口配置 + 压缩（#307）契约测试。

覆盖：
1. 口径收敛：40/6/2 三组默认值、动态读 settings（修定义时绑定）、请求级覆盖夹取、
   坏配置回落、兼容别名。
2. 默认零行为变更：压缩关时 build_context 输出与改动前逐字节一致（含换行原文）。
3. 压缩：extractive 首句/去重/单轮/总长封顶、摘要块标注「仅作线索」、
   全局开关与强制 on/off 三态。
4. 可插拔与诚实降级：自定义通道、set/reset、llm 成功、real=False/异常/不可用/空文本
   全部回落 extractive 并带 fallback+error，stub 文本绝不冒充摘要。
5. 运行时：5 条路径统一 _mem、run_agent 两条分支与 run_agent_stream 透传 context_window、
   同步 /agent/invoke 与流式 finalized 的 context 观测字段。
6. HTTP：401、status shape、GET preview 404/403/200 与审计、POST 自定义试算与上限。

测试隔离：清空会话内存、恢复压缩器注册表、桩掉审计与模型网关，不联网、不依赖真模型。
"""
from __future__ import annotations

import inspect
import json
from typing import Any, Dict

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.security import create_dev_token
from app.main import app
from app.models.caller import CallerContext
from app.routers import invoke as invoke_router
from app.services import (agent_runtime, audit, model_gateway, semantic_cache,
                          session_store, usage_store)

client = TestClient(app)

EMP_A_H = {"Authorization": create_dev_token("u_a", "d_a", "employee", "t_a")}
EMP_B_H = {"Authorization": create_dev_token("u_b", "d_b", "employee", "t_b")}
ADMIN_H = {"Authorization": create_dev_token("p_admin", "d_platform", "platform_admin", "t_sys")}

EMP_A = CallerContext(userId="u_a", deptId="d_a", role="employee", tenantId="t_a")
EMP_B = CallerContext(userId="u_b", deptId="d_b", role="employee", tenantId="t_b")

_DEFAULTS = {
    "CTX_SESSION_TURNS": 40, "CTX_WINDOW_TURNS": 6, "CTX_RUNTIME_TURNS": 2,
    "CTX_MIN_TURNS": 1, "CTX_MAX_TURNS": 40,
    "CTX_COMPRESS_ENABLED": False, "CTX_COMPRESS_PROVIDER": "extractive",
    "CTX_COMPRESS_MAX_CHARS": 1200, "CTX_COMPRESS_TURN_CHARS": 160,
    "CTX_COMPRESS_MAX_TOKENS": 300,
}


@pytest.fixture
def env(monkeypatch):
    """清场：隔离全局变量与配置，避免全量下被其它文件的 monkeypatch 污染。"""
    saved_sessions = dict(session_store._SESSIONS)
    saved_compressors = dict(session_store._COMPRESSORS)
    session_store._SESSIONS.clear()
    session_store.reset_compressor()
    monkeypatch.setattr(audit, "record", lambda *a, **k: "trace_ok")
    for k, v in _DEFAULTS.items():
        monkeypatch.setattr(settings, k, v, raising=False)
    yield
    session_store._SESSIONS.clear()
    session_store._SESSIONS.update(saved_sessions)
    session_store._COMPRESSORS.clear()
    session_store._COMPRESSORS.update(saved_compressors)
    session_store.reset_compressor()


def _sess(n: int) -> Dict[str, Any]:
    return {"turns": [
        {"role": "user" if i % 2 == 0 else "assistant",
         "content": f"第{i}轮内容", "ts": float(i)}
        for i in range(n)
    ]}


def _ok_compressor(text: str = "自定义摘要", provider: str = "fake"):
    return lambda turns: {
        "ok": True, "text": text, "provider": provider, "real": True,
        "lines": 1, "truncated": False, "fallback": False, "error": "",
    }


def _fake_chat(*a, **k):
    return {"text": "模拟答案", "real": False, "model_id": "stub",
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            "latency_ms": 1, "error": "", "throttled": False,
            "routed_tier": None, "routed_model": None,
            "rotated": False, "attempts": []}


# ── 1. 配置解析与口径收敛 ────────────────────────────────────────────────────
def test_window_config_shape(env):
    c = session_store.window_config()
    for k in ("session_turns", "window_turns", "runtime_turns", "min_turns",
              "max_turns", "compress_enabled", "compress_provider",
              "compress_max_chars", "compress_turn_chars", "compress_max_tokens",
              "providers"):
        assert k in c
    assert "extractive" in c["providers"] and "llm" in c["providers"]


def test_legacy_aliases_match_settings(env):
    assert session_store.MAX_SESSION_TURNS == 40
    assert session_store.MAX_CONTEXT_TURNS == 6


def test_resolve_defaults_are_40_6_2(env):
    assert session_store.resolve_session_turns() == 40
    assert session_store.resolve_window_turns() == 6
    assert session_store.resolve_runtime_turns() == 2


def test_resolve_reads_settings_at_call_time(env, monkeypatch):
    """旧实现把常量绑进默认参数，改配置要重启；新实现每次调用都重读。"""
    monkeypatch.setattr(settings, "CTX_RUNTIME_TURNS", 5)
    monkeypatch.setattr(settings, "CTX_SESSION_TURNS", 12)
    assert session_store.resolve_runtime_turns() == 5
    assert session_store.resolve_session_turns() == 12


def test_build_context_default_is_not_bound_at_definition(env, monkeypatch):
    monkeypatch.setattr(settings, "CTX_WINDOW_TURNS", 3)
    ctx = session_store.build_context(_sess(10))
    assert len(ctx.splitlines()) == 3
    assert "第9轮内容" in ctx and "第6轮内容" not in ctx


def test_clip_low_and_high(env, monkeypatch):
    monkeypatch.setattr(settings, "CTX_MIN_TURNS", 2)
    monkeypatch.setattr(settings, "CTX_MAX_TURNS", 8)
    assert session_store.resolve_runtime_turns(0) == 2
    assert session_store.resolve_runtime_turns(999) == 8
    assert session_store.resolve_window_turns(4) == 4


def test_clip_non_numeric_override_falls_back(env):
    assert session_store.resolve_runtime_turns("abc") == 2


def test_bad_config_value_falls_back(env, monkeypatch):
    monkeypatch.setattr(settings, "CTX_WINDOW_TURNS", "oops")
    assert session_store.resolve_window_turns() == 6


def test_context_observation_shape(env):
    o = session_store.context_observation(_sess(10))
    assert o["window_turns"] == 2 and o["total_turns"] == 10
    assert o["kept_turns"] == 2 and o["dropped_turns"] == 8
    assert o["compress_enabled"] is False and o["compress_provider"] == ""
    assert o["context_window_override"] is None


def test_context_observation_with_override(env):
    o = session_store.context_observation(_sess(10), 4)
    assert o["window_turns"] == 4 and o["kept_turns"] == 4
    assert o["dropped_turns"] == 6 and o["context_window_override"] == 4


def test_context_observation_does_not_invoke_compressor(env, monkeypatch):
    """响应观测只算轮数，不能再跑一次压缩器（会多调一次模型）。"""
    called = []
    monkeypatch.setattr(session_store, "compress_history",
                        lambda *a, **k: called.append(1) or {})
    session_store.context_observation(_sess(10))
    assert called == []


# ── 2. 默认零行为变更 ────────────────────────────────────────────────────────
def test_default_output_is_byte_identical_to_legacy(env):
    turns = _sess(20)["turns"]
    legacy = "\n".join(
        f"{'用户' if t['role'] == 'user' else '助手'}: {t['content']}"
        for t in turns[-6:]
    )
    assert session_store.build_context({"turns": turns}) == legacy


def test_newlines_in_content_preserved(env):
    s = {"turns": [{"role": "user", "content": "第一行\n第二行"}]}
    assert session_store.build_context(s) == "用户: 第一行\n第二行"


def test_empty_session_returns_empty(env):
    assert session_store.build_context({"turns": []}) == ""
    assert session_store.build_context({}) == ""


def test_fewer_turns_than_window_all_kept(env):
    ctx = session_store.build_context(_sess(3))
    assert len(ctx.splitlines()) == 3 and "第0轮内容" in ctx


def test_compress_false_equals_default(env):
    s = _sess(20)
    assert session_store.build_context(s, compress=False) == session_store.build_context(s)


def test_disabled_detail_summary_and_text(env):
    d = session_store.build_context_detail(_sess(20))
    assert d["summary"]["enabled"] is False and d["summary"]["text"] == ""
    assert d["summary_text"] == "" and d["context"] == d["recent_text"]
    assert d["dropped_turns"] == 14


def test_legacy_m4_window_cap_assertions_still_hold(env):
    sess = {"turns": [{"role": "user", "content": f"问题{i}"} for i in range(20)]}
    ctx = session_store.build_context(sess)
    assert "问题19" in ctx and "问题0" not in ctx
    s2 = session_store.create_session("ag_x", EMP_A)
    for i in range(50):
        session_store.append_turn(s2["session_id"], "user", f"t{i}")
    turns = session_store.history_of(s2["session_id"])
    assert len(turns) == session_store.MAX_SESSION_TURNS
    assert turns[-1]["content"] == "t49" and turns[0]["content"] != "t0"


# ── 3. 窗口截断与详情 ────────────────────────────────────────────────────────
def test_window_truncates_to_recent_6(env):
    ctx = session_store.build_context(_sess(20))
    assert len(ctx.splitlines()) == 6
    assert "第19轮内容" in ctx and "第13轮内容" not in ctx


def test_context_window_override(env):
    ctx = session_store.build_context(_sess(20), context_window=3)
    assert len(ctx.splitlines()) == 3 and "第19轮内容" in ctx


def test_max_turns_kwarg_still_compatible(env):
    ctx = session_store.build_context(_sess(20), 4)
    assert len(ctx.splitlines()) == 4


def test_runtime_flag_uses_2(env):
    d = session_store.build_context_detail(_sess(20), runtime=True)
    assert d["window_turns"] == 2 and d["kept_turns"] == 2
    assert d["dropped_turns"] == 18 and d["runtime"] is True


def test_detail_fields_consistent(env):
    s = _sess(9)
    d = session_store.build_context_detail(s, context_window=4)
    assert d["total_turns"] == 9 and d["window_turns"] == 4
    assert d["kept_turns"] == 4 and d["dropped_turns"] == 5
    assert d["context_chars"] == len(d["context"])
    assert d["estimated_tokens"] == len(d["context"]) // 2 + 1
    assert d["config"]["window_turns"] == 6
    assert d["context"] == session_store.build_context(s, context_window=4)


def test_append_turn_honors_dynamic_cap(env, monkeypatch):
    monkeypatch.setattr(settings, "CTX_SESSION_TURNS", 5)
    s = session_store.create_session("ag_x", EMP_A)
    for i in range(12):
        session_store.append_turn(s["session_id"], "user", f"t{i}")
    turns = session_store.history_of(s["session_id"])
    assert len(turns) == 5 and turns[-1]["content"] == "t11"


# ── 4. 压缩：extractive ──────────────────────────────────────────────────────
def test_force_compress_produces_summary(env):
    d = session_store.build_context_detail(_sess(20), compress=True)
    s = d["summary"]
    assert s["enabled"] is True and s["provider"] == "extractive"
    assert s["real"] is False and s["turns"] == 14 and s["lines"] > 0
    assert s["text"] and "【历史摘要" in d["summary_text"]


def test_extractive_assistant_only_first_sentence(env):
    turns = [{"role": "assistant", "content": "第一句结论。第二句详细展开。第三句。"},
             {"role": "assistant", "content": "另一轮结论。补充说明。"}]
    r = session_store.compress_history(turns, force=True)
    assert "第一句结论。" in r["text"]
    assert "第二句详细展开" not in r["text"] and "第三句" not in r["text"]


def test_extractive_dedup(env):
    turns = [{"role": "user", "content": "同一个问题"}] * 5
    r = session_store.compress_history(turns, force=True)
    assert r["lines"] == 1


def test_extractive_per_turn_truncation(env, monkeypatch):
    monkeypatch.setattr(settings, "CTX_COMPRESS_TURN_CHARS", 40)
    turns = [{"role": "user", "content": "很" * 200}]
    r = session_store.compress_history(turns, force=True)
    assert "…" in r["text"] and "很" * 50 not in r["text"]


def test_extractive_total_cap_truncated(env, monkeypatch):
    monkeypatch.setattr(settings, "CTX_COMPRESS_MAX_CHARS", 120)
    turns = [{"role": "user", "content": f"问题{i}" + "长内容" * 30} for i in range(20)]
    r = session_store.compress_history(turns, force=True)
    assert r["truncated"] is True and len(r["text"]) <= 121


def test_summary_block_marks_unreliable(env):
    d = session_store.build_context_detail(_sess(20), compress=True)
    assert "仅作线索" in d["summary_text"] and "可能不完整" in d["summary_text"]
    assert "以下近窗原文为准" in d["summary_text"]


def test_context_includes_summary_and_recent_marker(env):
    d = session_store.build_context_detail(_sess(20), compress=True)
    assert "【近窗对话" in d["context"]
    assert d["context"].startswith("【历史摘要")
    assert "第19轮内容" in d["context"]


def test_global_switch_enables_compression(env, monkeypatch):
    monkeypatch.setattr(settings, "CTX_COMPRESS_ENABLED", True)
    d = session_store.build_context_detail(_sess(20))
    assert d["summary"]["enabled"] is True and d["summary"]["provider"] == "extractive"


def test_compress_false_overrides_global_switch(env, monkeypatch):
    monkeypatch.setattr(settings, "CTX_COMPRESS_ENABLED", True)
    d = session_store.build_context_detail(_sess(20), compress=False)
    assert d["summary"]["enabled"] is False and d["context"] == d["recent_text"]


def test_no_dropped_turns_gives_empty_summary(env):
    d = session_store.build_context_detail(_sess(3), compress=True)
    assert d["summary"]["enabled"] is True and d["summary"]["turns"] == 0
    assert d["summary_text"] == "" and d["context"] == d["recent_text"]


def test_compress_history_disabled_shape(env):
    r = session_store.compress_history(_sess(5)["turns"])
    assert r["enabled"] is False and r["text"] == "" and r["fallback"] is False


# ── 5. 可插拔通道与诚实降级 ──────────────────────────────────────────────────
def test_register_compressor_custom(env, monkeypatch):
    session_store.register_compressor("fake", _ok_compressor())
    monkeypatch.setattr(settings, "CTX_COMPRESS_PROVIDER", "fake")
    r = session_store.compress_history(_sess(5)["turns"], force=True)
    assert r["text"] == "自定义摘要" and r["provider"] == "fake"


def test_set_and_reset_compressor(env):
    session_store.register_compressor("fake", _ok_compressor())
    session_store.set_compressor("fake")
    r = session_store.compress_history(_sess(5)["turns"], force=True)
    assert r["provider"] == "fake"
    session_store.reset_compressor()
    r2 = session_store.compress_history(_sess(5)["turns"], force=True)
    assert r2["provider"] == "extractive"


def test_set_unknown_compressor_raises(env):
    with pytest.raises(KeyError):
        session_store.set_compressor("nope")


def test_explicit_provider_argument_wins(env):
    session_store.register_compressor("fake", _ok_compressor())
    r = session_store.compress_history(_sess(5)["turns"], force=True, provider="fake")
    assert r["provider"] == "fake"


def test_unknown_provider_falls_back_to_extractive(env, monkeypatch):
    monkeypatch.setattr(settings, "CTX_COMPRESS_PROVIDER", "nope")
    r = session_store.compress_history(_sess(5)["turns"], force=True)
    assert r["provider"] == "extractive" and r["ok"] is True


def test_llm_channel_success(env, monkeypatch):
    monkeypatch.setattr(settings, "CTX_COMPRESS_PROVIDER", "llm")
    monkeypatch.setattr(model_gateway, "llm_available", lambda *a, **k: True)
    monkeypatch.setattr(model_gateway, "chat_sync_ex", lambda *a, **k: {
        "text": "模型给出的摘要", "real": True, "model_id": "m1",
        "usage": {"prompt_tokens": 10}, "error": ""})
    r = session_store.compress_history(_sess(5)["turns"], force=True)
    assert r["provider"] == "llm" and r["real"] is True
    assert r["text"] == "模型给出的摘要" and r["fallback"] is False


def test_llm_real_false_falls_back(env, monkeypatch):
    monkeypatch.setattr(settings, "CTX_COMPRESS_PROVIDER", "llm")
    monkeypatch.setattr(model_gateway, "llm_available", lambda *a, **k: True)
    monkeypatch.setattr(model_gateway, "chat_sync_ex", lambda *a, **k: {
        "text": "stub占位文本", "real": False, "error": "model unavailable"})
    r = session_store.compress_history(_sess(5)["turns"], force=True)
    assert r["fallback"] is True and r["provider"] == "extractive"
    assert r["real"] is False and r["requested_provider"] == "llm"
    assert "stub占位文本" not in r["text"] and r["error"]


def test_llm_exception_falls_back(env, monkeypatch):
    monkeypatch.setattr(settings, "CTX_COMPRESS_PROVIDER", "llm")
    monkeypatch.setattr(model_gateway, "llm_available", lambda *a, **k: True)

    def boom(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr(model_gateway, "chat_sync_ex", boom)
    r = session_store.compress_history(_sess(5)["turns"], force=True)
    assert r["fallback"] is True and "network down" in r["error"]
    assert r["provider"] == "extractive"


def test_llm_unavailable_skips_model_call(env, monkeypatch):
    monkeypatch.setattr(settings, "CTX_COMPRESS_PROVIDER", "llm")
    monkeypatch.setattr(model_gateway, "llm_available", lambda *a, **k: False)
    called = []
    monkeypatch.setattr(model_gateway, "chat_sync_ex",
                        lambda *a, **k: called.append(1) or _fake_chat())
    r = session_store.compress_history(_sess(5)["turns"], force=True)
    assert called == [] and r["error"] == "llm_unavailable"
    assert r["fallback"] is True


def test_llm_empty_text_falls_back(env, monkeypatch):
    monkeypatch.setattr(settings, "CTX_COMPRESS_PROVIDER", "llm")
    monkeypatch.setattr(model_gateway, "llm_available", lambda *a, **k: True)
    monkeypatch.setattr(model_gateway, "chat_sync_ex", lambda *a, **k: {
        "text": "   ", "real": True, "error": ""})
    r = session_store.compress_history(_sess(5)["turns"], force=True)
    assert r["fallback"] is True and r["provider"] == "extractive"


def test_custom_compressor_ok_false_falls_back(env, monkeypatch):
    session_store.register_compressor("boom", lambda turns: {
        "ok": False, "text": "", "provider": "boom", "error": "channel error"})
    monkeypatch.setattr(settings, "CTX_COMPRESS_PROVIDER", "boom")
    r = session_store.compress_history(_sess(5)["turns"], force=True)
    assert r["fallback"] is True and r["requested_provider"] == "boom"
    assert r["error"] == "channel error" and r["provider"] == "extractive"


def test_fallback_summary_block_is_labelled(env, monkeypatch):
    monkeypatch.setattr(settings, "CTX_COMPRESS_ENABLED", True)
    monkeypatch.setattr(settings, "CTX_COMPRESS_PROVIDER", "llm")
    monkeypatch.setattr(model_gateway, "llm_available", lambda *a, **k: False)
    d = session_store.build_context_detail(_sess(20))
    assert "已回落" in d["summary_text"]


# ── 6. 运行时五条路径同口径 ──────────────────────────────────────────────────
def test_mem_helper_uses_runtime_resolution(env):
    ctx = agent_runtime._mem(_sess(10)["turns"])
    assert len(ctx.splitlines()) == 2
    assert len(agent_runtime._mem(_sess(10)["turns"], 4).splitlines()) == 4


def test_no_hardcoded_max_turns_in_runtime(env):
    """防「改窗口只改一处」：源码里不应再有 max_turns=2 硬编码。"""
    src = inspect.getsource(agent_runtime)
    assert "max_turns=2" not in src
    assert src.count("_mem(") >= 5  # 1 处定义 + 5 处调用（含注释不计）


def test_run_agent_passes_window_to_mem(env, monkeypatch):
    seen = []
    monkeypatch.setattr(agent_runtime, "_mem",
                        lambda h, cw=None: seen.append(cw) or "MEM")
    monkeypatch.setattr(model_gateway, "chat_sync_ex", _fake_chat)
    agent_runtime.run_agent("", "你好", EMP_A, history=_sess(4)["turns"],
                            adapter_type="llm", context_window=4)
    assert seen and all(v == 4 for v in seen)


def test_run_agent_stub_fallback_passes_window(env, monkeypatch):
    seen = []
    monkeypatch.setattr(agent_runtime, "_mem",
                        lambda h, cw=None: seen.append(cw) or "MEM")
    monkeypatch.setattr(model_gateway, "chat_sync_ex", _fake_chat)

    def no_graph(*a, **k):
        raise RuntimeError("LangGraph 未安装")

    monkeypatch.setattr(agent_runtime, "build_graph", no_graph)
    agent_runtime.run_agent("", "你好", EMP_A, history=_sess(4)["turns"],
                            adapter_type="llm", context_window=3)
    assert seen and all(v == 3 for v in seen)


def test_run_agent_default_passes_no_override(env, monkeypatch):
    seen = []
    monkeypatch.setattr(agent_runtime, "_mem",
                        lambda h, cw=None: seen.append(cw) or "MEM")
    monkeypatch.setattr(model_gateway, "chat_sync_ex", _fake_chat)
    agent_runtime.run_agent("", "你好", EMP_A, history=[], adapter_type="llm")
    # 调用方只传原始 override；解析统一发生在 _mem 内部（None → CTX_RUNTIME_TURNS=2），
    # 这样 5 条路径不可能出现「一处解析、一处没解析」的口径分叉。
    assert seen == [None]
    assert len(session_store.build_context(
        _sess(10), context_window=session_store.resolve_runtime_turns(None)).splitlines()) == 2


# ── 7. HTTP：状态与预览 ──────────────────────────────────────────────────────
def test_status_requires_auth(env):
    assert client.get("/api/v1/agent/context/status").status_code == 401


def test_status_shape(env):
    r = client.get("/api/v1/agent/context/status", headers=EMP_A_H)
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["session_turns"] == 40 and d["window_turns"] == 6
    assert d["runtime_turns"] == 2 and d["compress_enabled"] is False
    assert d["legacy_aliases"]["MAX_SESSION_TURNS"] == 40


def test_get_preview_requires_auth(env):
    assert client.get("/api/v1/agent/context/preview",
                      params={"session_id": "whatever"}).status_code == 401


def test_get_preview_unknown_session_404(env):
    r = client.get("/api/v1/agent/context/preview",
                   params={"session_id": "sess_nope"}, headers=EMP_A_H)
    assert r.status_code == 404


def test_get_preview_foreign_session_403(env):
    s = session_store.create_session("ag_x", EMP_A)
    r = client.get("/api/v1/agent/context/preview",
                   params={"session_id": s["session_id"]}, headers=EMP_B_H)
    assert r.status_code == 403


def test_get_preview_own_session_200(env):
    s = session_store.create_session("ag_x", EMP_A)
    for i in range(20):
        session_store.append_turn(s["session_id"], "user", f"问题{i}")
    r = client.get("/api/v1/agent/context/preview",
                   params={"session_id": s["session_id"], "compress": "true",
                           "runtime": "true", "context_window": 3},
                   headers=EMP_A_H)
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["total_turns"] == 20 and d["window_turns"] == 3
    assert d["dropped_turns"] == 17 and d["summary"]["enabled"] is True
    assert "问题19" in d["context"]


def test_get_preview_records_audit(env, monkeypatch):
    called = []
    monkeypatch.setattr(audit, "record",
                        lambda action, *a, **k: called.append((action, k)) or "t")
    s = session_store.create_session("ag_x", EMP_A)
    r = client.get("/api/v1/agent/context/preview",
                   params={"session_id": s["session_id"]}, headers=EMP_A_H)
    assert r.status_code == 200
    assert called and called[0][0] == "context_preview"
    assert called[0][1]["meta"]["runtime"] is False


def test_post_preview_custom_turns(env):
    body = {"turns": _sess(20)["turns"], "compress": True, "runtime": True}
    r = client.post("/api/v1/agent/context/preview", json=body, headers=EMP_A_H)
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["total_turns"] == 20 and d["kept_turns"] == 2
    assert d["summary"]["provider"] == "extractive"


def test_post_preview_compress_off_and_config(env, monkeypatch):
    monkeypatch.setattr(settings, "CTX_COMPRESS_ENABLED", True)
    turns = _sess(20)["turns"]
    off = client.post("/api/v1/agent/context/preview",
                      json={"turns": turns, "compress": False}, headers=EMP_A_H)
    cfg = client.post("/api/v1/agent/context/preview",
                      json={"turns": turns}, headers=EMP_A_H)
    assert off.json()["data"]["summary"]["enabled"] is False
    assert cfg.json()["data"]["summary"]["enabled"] is True


def test_post_preview_limits(env):
    turns = [{"role": "user", "content": "长" * 5000} for _ in range(250)]
    r = client.post("/api/v1/agent/context/preview",
                    json={"turns": turns, "compress": True}, headers=EMP_A_H)
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["total_turns"] == 200
    # 后端只取前 200 轮、单轮内容截到 4000 字；不改写调用方传入的数组
    assert len(turns) == 250 and len(turns[0]["content"]) == 5000
    assert "长" * 4001 not in d["context"]


def test_sessions_endpoint_context_preview_unchanged(env):
    s = session_store.create_session("ag_x", EMP_A)
    for i in range(20):
        session_store.append_turn(s["session_id"], "user", f"问题{i}")
    r = client.get(f"/api/v1/agent/sessions/{s['session_id']}", headers=EMP_A_H)
    assert r.status_code == 200
    cp = r.json()["data"]["context_preview"]
    assert "问题19" in cp and "问题0" not in cp


# ── 8. invoke 同步 / 流式观测字段与透传 ──────────────────────────────────────
def _install_invoke_stubs(monkeypatch, seen):
    from app.routers import agents as agents_router
    saved = dict(agents_router._AGENTS)
    agents_router._AGENTS.clear()
    agents_router._AGENTS["a1"] = {
        "agent_id": "a1", "name": "测试智能体", "status": "published",
        "system_prompt": "你是测试助手", "adapter_type": "llm", "outputs_contract": "",
        "tenant_id": "", "model_id": "",
    }

    def fake_run_agent(*a, **kw):
        seen["cw"] = kw.get("context_window")
        return {
            "answer": "模拟回答", "citations": [], "web_sources": [],
            "web_search": None, "query_rewritten": False, "turn_no": 1,
            "usage": {"model_id": "stub", "real": False, "usage": {},
                      "latency_ms": 1, "prompt_text": "", "completion_text": ""},
            "routed_tier": None, "routed_model": None, "tool_calls": [],
            "skill_applied": False,
        }

    async def fake_stream(*a, **kw):
        seen["stream_cw"] = kw.get("context_window")
        yield {"type": "done", "result": fake_run_agent(*a, **kw)}

    monkeypatch.setattr(agent_runtime, "run_agent", fake_run_agent)
    monkeypatch.setattr(agent_runtime, "run_agent_stream", fake_stream)
    monkeypatch.setattr(usage_store, "enforce_quota", lambda caller: None)
    monkeypatch.setattr(usage_store, "record", lambda *a, **k: {
        "model_id": "stub", "prompt_tokens": 1, "completion_tokens": 1,
        "cost": 0.0, "real": False, "latency_ms": 1})
    monkeypatch.setattr(usage_store, "quota_status", lambda caller: {})
    monkeypatch.setattr(invoke_router, "_build_artifacts", lambda *a, **k: [])
    monkeypatch.setattr(invoke_router, "_schedule_memory_extract", lambda *a, **k: None)
    monkeypatch.setattr(invoke_router, "_m27_postprocess", lambda *a, **k: {
        "guardrails": {}, "cache": {}, "routing": {}, "confidence": None,
        "handoff": None, "quality_recorded": False, "errors": []})
    monkeypatch.setattr(semantic_cache, "get", lambda *a, **k: None)
    return saved, agents_router


def test_invoke_sync_context_observation_and_passthrough(env, monkeypatch):
    seen: Dict[str, Any] = {}
    saved, agents_router = _install_invoke_stubs(monkeypatch, seen)
    try:
        r = client.post("/api/v1/agent/invoke", headers=EMP_A_H, json={
            "agent_id": "a1", "input": "你好", "context_window": 4})
        assert r.status_code == 200, r.text
        d = r.json()["data"]
        assert seen["cw"] == 4
        assert d["context"]["window_turns"] == 4
        assert d["context"]["context_window_override"] == 4
        assert d["context"]["compress_enabled"] is False
    finally:
        agents_router._AGENTS.clear()
        agents_router._AGENTS.update(saved)


def test_invoke_context_window_clamped(env, monkeypatch):
    seen: Dict[str, Any] = {}
    saved, agents_router = _install_invoke_stubs(monkeypatch, seen)
    try:
        r = client.post("/api/v1/agent/invoke", headers=EMP_A_H, json={
            "agent_id": "a1", "input": "你好", "context_window": 999})
        assert r.status_code == 200, r.text
        assert r.json()["data"]["context"]["window_turns"] == 40
    finally:
        agents_router._AGENTS.clear()
        agents_router._AGENTS.update(saved)


def test_invoke_without_override_keeps_default(env, monkeypatch):
    seen: Dict[str, Any] = {}
    saved, agents_router = _install_invoke_stubs(monkeypatch, seen)
    try:
        r = client.post("/api/v1/agent/invoke", headers=EMP_A_H, json={
            "agent_id": "a1", "input": "你好"})
        assert r.status_code == 200, r.text
        assert seen["cw"] is None
        assert r.json()["data"]["context"]["window_turns"] == 2
        assert r.json()["data"]["context"]["context_window_override"] is None
    finally:
        agents_router._AGENTS.clear()
        agents_router._AGENTS.update(saved)


def test_stream_finalized_context_observation(env, monkeypatch):
    seen: Dict[str, Any] = {}
    saved, agents_router = _install_invoke_stubs(monkeypatch, seen)
    try:
        r = client.post("/api/v1/agent/stream", headers=EMP_A_H, json={
            "agent_id": "a1", "input": "你好", "context_window": 5})
        assert r.status_code == 200, r.text
        events = [json.loads(line[6:]) for line in r.text.splitlines()
                  if line.startswith("data: ")]
        fin = next(e for e in events if e.get("type") == "finalized")
        assert seen["stream_cw"] == 5
        assert fin["context"]["window_turns"] == 5
    finally:
        agents_router._AGENTS.clear()
        agents_router._AGENTS.update(saved)
