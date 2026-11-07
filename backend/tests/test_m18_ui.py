"""M18 · 对外界面所需的接口能力。

这一版把内部仪表盘改成对外产品形态，界面要的东西后端得先有：
1. 侧栏历史对话要有「人话标题」，不能显示 sess_9f3a… 内部 ID
2. 点开历史对话要能恢复完整轮次，否则刷新一次对话就变空白
3. 产出物要挂在轮次上，换设备打开历史会话还能下载
4. 打开即可提问：内置通用助手，且它不占用 115 个技能包的口径
   （catalog 不含它；技能库数字变了会被客户当成"多送了一个"）
"""
import io

from fastapi.testclient import TestClient

from app.core.security import create_dev_token
from app.main import app
from app.models.caller import CallerContext
from app.services import agent_store, session_store

client = TestClient(app)
ADMIN = {"Authorization": create_dev_token("p_admin", "d_platform", "platform_admin")}
EMP = {"Authorization": create_dev_token("u_zhang", "d_sales", "employee")}


def test_session_title_from_first_user_turn():
    caller = CallerContext(userId="u_title", deptId="d1", role="employee", tenantId="t1")
    sess = session_store.create_session("ag_x", caller)
    session_store.append_turn(sess["session_id"], "assistant", "先说话的是助手")
    session_store.append_turn(sess["session_id"], "user", "  帮我分析\n这份费用台账的异常科目 ")
    assert session_store.session_title(sess) == "帮我分析 这份费用台账的异常科目"
    # 空会话也要有兜底标题，不能返回空串让侧栏出现一条没有文字的行
    empty = session_store.create_session("ag_y", caller)
    assert session_store.session_title(empty) == "新对话"


def test_session_title_truncates():
    caller = CallerContext(userId="u_title2", deptId="d1", role="employee", tenantId="t1")
    sess = session_store.create_session("ag_x", caller)
    session_store.append_turn(sess["session_id"], "user", "字" * 60)
    title = session_store.session_title(sess)
    assert len(title) == 27 and title.endswith("…")


def test_session_detail_returns_turns_and_artifacts():
    caller = CallerContext(userId="u_detail", deptId="d1", role="employee", tenantId="t1")
    sess = session_store.create_session("ag_x", caller)
    session_store.append_turn(sess["session_id"], "user", "导出一下")
    session_store.append_turn(sess["session_id"], "assistant", "好了")
    session_store.attach_meta(sess["session_id"], "assistant",
                              {"artifacts": [{"file_id": "file_1", "filename": "a.xlsx"}]})

    r = client.get(f"/api/v1/agent/sessions/{sess['session_id']}",
                   headers={"Authorization": create_dev_token("u_detail", "d1", "employee", tenant_id="t1")})
    assert r.status_code == 200
    d = r.json()["data"]
    assert [t["role"] for t in d["turns"]] == ["user", "assistant"]
    assert d["turns"][1]["artifacts"][0]["filename"] == "a.xlsx"
    # 用户轮没有产出物时就不该带这个键，前端不用做无意义的空数组判断
    assert "artifacts" not in d["turns"][0]


def test_attach_meta_targets_last_matching_turn():
    caller = CallerContext(userId="u_meta", deptId="d1", role="employee", tenantId="t1")
    sess = session_store.create_session("ag_x", caller)
    session_store.append_turn(sess["session_id"], "assistant", "旧的一轮")
    session_store.append_turn(sess["session_id"], "user", "再来")
    session_store.append_turn(sess["session_id"], "assistant", "新的一轮")
    session_store.attach_meta(sess["session_id"], "assistant", {"run_id": "run_new"})
    assert sess["turns"][0].get("run_id") is None
    assert sess["turns"][2]["run_id"] == "run_new"


def test_builtin_assistant_is_idempotent_and_outside_catalog():
    a = agent_store.ensure_builtin_assistant()
    b = agent_store.ensure_builtin_assistant()
    assert a["agent_id"] == b["agent_id"]
    assert a["status"] == "published"      # 打开就能用，不该被"未发布不可调用"挡住
    assert a["builtin"] is True
    assert not a["system_prompt"]          # 留空=沿用运行时默认提示词，不重复维护一份

    admin = CallerContext(userId="p_admin", deptId="d_platform",
                          role="platform_admin", tenantId="t_platform")
    names = [x["name"] for cat in agent_store.catalog(admin)["categories"]
             for g in cat["groups"] for x in g["agents"]]
    assert "通用助手" not in names


def test_builtin_assistant_callable_without_review():
    """内置助手是平台自带能力，不是待评审的技能包——不该 403。"""
    agent_store.ensure_builtin_assistant()
    r = client.post("/api/v1/agent/invoke",
                    json={"agent_id": agent_store.BUILTIN_ASSISTANT_ID, "input": "你好"},
                    headers=EMP)
    assert r.status_code == 200
    assert r.json()["data"]["answer"]


def test_session_detail_exposes_llm_real_flag():
    """刷新页面后仍要知道「这轮是不是真模型答的」。

    只把 real 放在当次响应里是不够的：历史会话一打开，红色提示就没了，
    用户会把占位内容当成模型结论。
    """
    caller = CallerContext(userId="u_real", deptId="d1", role="employee", tenantId="t1")
    sess = session_store.create_session("ag_x", caller)
    session_store.append_turn(sess["session_id"], "assistant", "[stub] 占位答案")
    session_store.attach_meta(sess["session_id"], "assistant", {"llm_real": False})

    r = client.get(f"/api/v1/agent/sessions/{sess['session_id']}",
                   headers={"Authorization": create_dev_token("u_real", "d1", "employee", tenant_id="t1")})
    assert r.json()["data"]["turns"][0]["llm_real"] is False


def test_sessions_list_has_title_for_sidebar():
    caller = CallerContext(userId="u_list", deptId="d1", role="employee", tenantId="t1")
    sess = session_store.create_session("ag_x", caller)
    session_store.append_turn(sess["session_id"], "user", "把上周的报销单核一遍")
    r = client.get("/api/v1/agent/sessions?limit=5",
                   headers={"Authorization": create_dev_token("u_list", "d1", "employee", tenant_id="t1")})
    assert r.status_code == 200
    row = next(s for s in r.json()["data"]["sessions"] if s["session_id"] == sess["session_id"])
    assert row["title"] == "把上周的报销单核一遍"
