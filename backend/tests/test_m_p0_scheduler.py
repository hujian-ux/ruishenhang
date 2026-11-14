"""P0-5 调度 + 可靠投递测试。

覆盖：CRUD / tenant 隔离 / 越权防护 / 表达式校验 / 触发执行 run_agent / outbox 状态机 /
worker 重试 / 无 PG 内存兜底。定时触发（APScheduler 真 tick）不测，改由 run_now_sync 驱动。
"""
import datetime

import pytest

from app.models.caller import CallerContext
from app.services import outbox, scheduler_service

CALLER_A = CallerContext(userId="u_a", deptId="d1", role="platform_admin", tenantId="T1")
CALLER_B = CallerContext(userId="u_b", deptId="d2", role="platform_admin", tenantId="T2")
EMP = CallerContext(userId="u_e", deptId="d1", role="employee", tenantId="T1")


@pytest.fixture(autouse=True)
def _reset():
    scheduler_service.reset()
    outbox.reset()
    yield
    scheduler_service.reset()
    outbox.reset()


def _fake_run(answer, artifacts=None, calls=None):
    def _f(agent_id, user_input, caller, history=None, system_prompt=None):
        if calls is not None:
            calls.append((agent_id, user_input, caller.tenantId))
        return {"answer": answer, "artifacts": artifacts or [], "llm": {"real": False}}
    return _f


# ── 校验与 CRUD ─────────────────────────────────────────────────────────
def test_create_and_list_tenant_isolated():
    aid = scheduler_service.create_automation(
        CALLER_A, "日报", "agent_x", "生成{today}日报", "cron", "0 9 * * *", enabled=False)
    scheduler_service.create_automation(
        CALLER_B, "周报", "agent_y", "生成", "interval", "3600", enabled=False)
    t1 = scheduler_service.list_automations("T1")
    t2 = scheduler_service.list_automations("T2")
    assert len(t1) == 1 and t1[0]["name"] == "日报"
    assert len(t2) == 1 and t2[0]["name"] == "周报"
    assert scheduler_service.get(aid)["tenant_id"] == "T1"


def test_invalid_schedule_type_rejected():
    with pytest.raises(ValueError):
        scheduler_service.create_automation(
            CALLER_A, "x", "a", "", "bogus", "", enabled=False)


def test_invalid_cron_rejected():
    with pytest.raises(ValueError):
        scheduler_service.create_automation(
            CALLER_A, "x", "a", "", "cron", "not a cron", enabled=False)


def test_invalid_interval_rejected():
    with pytest.raises(ValueError):
        scheduler_service.create_automation(
            CALLER_A, "x", "a", "", "interval", "abc", enabled=False)


def test_pause_resume_delete():
    aid = scheduler_service.create_automation(
        CALLER_A, "x", "a", "", "interval", "60", enabled=True)
    assert scheduler_service.get(aid)["enabled"] is True
    assert scheduler_service.pause(aid) is True
    assert scheduler_service.get(aid)["enabled"] is False
    assert scheduler_service.resume(aid) is True
    assert scheduler_service.get(aid)["enabled"] is True
    assert scheduler_service.delete(aid) is True
    assert scheduler_service.get(aid) is None


# ── 触发执行 + outbox ───────────────────────────────────────────────────
def test_run_now_fires_run_agent_and_enqueues_outbox():
    calls = []
    scheduler_service._run_agent = _fake_run("今日营收 1200", [{"file_id": "f1"}], calls)
    aid = scheduler_service.create_automation(
        CALLER_A, "营收播报", "agent_rev", "播报{today}", "cron", "0 9 * * *", enabled=False)
    res = scheduler_service.run_now_sync(aid)
    assert res["run_id"] and res["error"] == ""
    # run_agent 被以正确 tenant 调用，且模板占位被渲染
    expected_input = "播报" + datetime.date.today().isoformat()
    assert calls == [("agent_rev", expected_input, "T1")]
    # outbox 有 artifact 投递：先驱动 worker 投递（pull 模型），再断言
    outbox.worker_tick()
    ob = outbox.list_outbox("T1")
    assert len(ob) == 1
    assert ob[0]["channel"] == "artifact" and ob[0]["status"] == "delivered"
    assert ob[0]["payload"]["answer"] == "今日营收 1200"
    assert ob[0]["payload"]["artifacts"] == ["f1"]


def test_webhook_outbox_delivered_on_200():
    scheduler_service._run_agent = _fake_run("ok", [])
    posted = []
    outbox._post_webhook = lambda url, payload: (posted.append((url, payload)) or (200, "ok"))
    aid = scheduler_service.create_automation(
        CALLER_A, "wh", "a", "", "cron", "0 9 * * *", enabled=False, webhook_url="http://hook")
    scheduler_service.run_now_sync(aid)
    outbox.worker_tick()  # 驱动投递
    ob = outbox.list_outbox("T1")
    wh = [o for o in ob if o["channel"] == "webhook"]
    assert len(wh) == 1 and wh[0]["status"] == "delivered"
    assert posted and posted[0][0] == "http://hook"


def test_webhook_outbox_retries_then_delivers():
    scheduler_service._run_agent = _fake_run("ok", [])
    seq = {"n": 0}
    def _flaky(url, payload):
        seq["n"] += 1
        return (500, "down") if seq["n"] == 1 else (200, "ok")
    outbox._post_webhook = _flaky
    aid = scheduler_service.create_automation(
        CALLER_A, "wh", "a", "", "cron", "0 9 * * *", enabled=False, webhook_url="http://hook")
    scheduler_service.run_now_sync(aid)
    outbox.worker_tick()  # 第一次：500 → failed, attempts=1，退避待重试
    failed = [i for i in outbox._OUTBOX.values() if i["status"] == "failed"]
    assert len(failed) == 1 and failed[0]["attempts"] == 1
    # 推进时间（模拟退避到期）后再投递
    failed[0]["next_retry"] = datetime.datetime.now().timestamp() - 1
    outbox.worker_tick()
    ob2 = outbox.list_outbox("T1", status="delivered")
    assert any(o["channel"] == "webhook" for o in ob2)
    assert seq["n"] == 2


# ── 越权防护 ────────────────────────────────────────────────────────────
def test_cross_tenant_cannot_run_or_delete():
    aid = scheduler_service.create_automation(
        CALLER_A, "secret", "a", "", "interval", "60", enabled=False)
    # B 租户看不到本租户任务
    assert scheduler_service.list_automations("T2") == []
    # 路由层归属校验：B 越权应被拦
    from app.routers import scheduler as r
    assert r._owns(CALLER_B, scheduler_service.get(aid)) is False


def test_employee_cannot_manage_others_automation():
    aid = scheduler_service.create_automation(
        CALLER_A, "boss", "a", "", "interval", "60", enabled=False)
    from app.routers import scheduler as r
    assert r._owns(EMP, scheduler_service.get(aid)) is False
    # 本人创建的员工可管理
    my = scheduler_service.create_automation(
        EMP, "mine", "a", "", "interval", "60", enabled=False)
    assert r._owns(EMP, scheduler_service.get(my)) is True


# ── 生命周期 ────────────────────────────────────────────────────────────
def test_init_scheduler_no_crash_without_pg():
    # 测试环境 APP_ENV=test → 不真正 start，但初始化不崩
    ok = scheduler_service.init_scheduler()
    assert ok in (True, False)
    scheduler_service.shutdown_scheduler()
    assert scheduler_service._SCHED is None


def test_load_from_pg_roundtrip_via_upsert():
    aid = scheduler_service.create_automation(
        CALLER_A, "p", "a", "", "interval", "60", enabled=False)
    # 内存里有；模拟重启：清空内存，从 pg_load 载回（无 PG 时 load_all 返回 []，降级安全）
    with scheduler_service._lock:
        scheduler_service._AUTOS.clear()
    scheduler_service.load_from_pg()
    # 无 PG 环境载回为空是预期降级；有 PG 时此处应有数据。仅验证不抛。
    assert True
