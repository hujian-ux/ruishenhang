"""M48 · 审批助手摘要回推 IM/OA 卡片：企微/钉钉卡片渲染摘要 + 按 approval_id 自动解析。"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # type: ignore

from app.core.security import create_dev_token
from app.main import app
from app.services import agent_store, approval_store, im_gateway, tenant_store

client = TestClient(app)


def _admin():
    return {"Authorization": create_dev_token("u_admin", "d_it", "platform_admin", "t_demo")}


def _register(tenant_id, name="M48IM", dept="d_sales"):
    tok = {"Authorization": create_dev_token("u_emp", dept, "employee", tenant_id)}
    r = client.post("/api/v1/agents",
                    json={"name": name, "adapter_type": "llm_retrieval",
                          "description": "M48 全局知识域测试", "knowledge_scope": "global"},
                    headers=tok)
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    return d["agent_id"], d["approval_id"], tok


def test_wecom_card_renders_summary():
    captured = {}
    orig = im_gateway._http_post

    def fake(url, data, headers):
        captured[url] = data
        return {"access_token": "TOK"} if "gettoken" in url else {"errcode": 0}

    im_gateway._http_post = fake
    try:
        ad = im_gateway.WeComAdapter("c", "s", "1", "t")
        ad.send_approval_card("zhangsan", "待审：全局助手", "https://x/y", summary="进度 1/2；⚠ 全局知识域")
        body = captured["https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token=TOK"]
        content = body["markdown"]["content"]
        assert "待审：全局助手" in content
        assert "进度 1/2；⚠ 全局知识域" in content
        assert "[点击处理](https://x/y)" in content
    finally:
        im_gateway._http_post = orig


def test_dingtalk_card_renders_summary():
    captured = {}
    orig = im_gateway._http_post

    def fake(url, data, headers):
        captured[url] = data
        return {"access_token": "TOK"} if "gettoken" in url else {"errcode": 0}

    im_gateway._http_post = fake
    try:
        ad = im_gateway.DingTalkAdapter("k", "s", "sec")
        ad.send_approval_card("z", "待审X", "https://x/y", summary="风险：批量过大")
        body = captured["https://oapi.dingtalk.com/topapi/message/corpconversation/asyncsend_v2?access_token=TOK"]
        content = body["markdown"]["text"]
        assert "待审X" in content and "风险：批量过大" in content
    finally:
        im_gateway._http_post = orig


def test_push_approval_with_assistant_resolves_from_id():
    tid = tenant_store.create_tenant("Co_M48", {})["tenant_id"]
    agent_id, apr_id, _ = _register(tid)
    recorded = {}

    class FakeAdapter:
        def send_approval_card(self, to_user, title, url, access_token="", summary=""):
            recorded.update(to_user=to_user, title=title, url=url, summary=summary)
            return {"ok": True}

    orig_adapter = im_gateway.IMGateway._adapter
    im_gateway.IMGateway._adapter = lambda self, t, p: FakeAdapter()
    try:
        res = im_gateway.gateway.push_approval_with_assistant(
            tid, "wecom", "zhangsan", apr_id, base_url="https://console.example.com")
    finally:
        im_gateway.IMGateway._adapter = orig_adapter

    assert res == {"ok": True}
    # 标题来自智能体名，摘要来自审批助手（含全局域风险），链接带 approval_id
    assert recorded["title"] == "M48IM"
    assert "全局" in recorded["summary"]
    assert f"https://console.example.com/approval?approval_id={apr_id}" in recorded["url"]


def test_push_without_base_url_has_no_link():
    tid = tenant_store.create_tenant("Co_M48D", {})["tenant_id"]
    _, apr_id, _ = _register(tid)
    recorded = {}

    class FakeAdapter:
        def send_approval_card(self, to_user, title, url, access_token="", summary=""):
            recorded.update(url=url, summary=summary)
            return {"ok": True}

    orig_adapter = im_gateway.IMGateway._adapter
    im_gateway.IMGateway._adapter = lambda self, t, p: FakeAdapter()
    try:
        im_gateway.gateway.push_approval_with_assistant(tid, "wecom", "zhangsan", apr_id)
    finally:
        im_gateway.IMGateway._adapter = orig_adapter
    # 未配置 base_url：卡片只带摘要、不带跳转链接（设计预期）
    assert recorded["url"] == ""
    assert "全局" in recorded["summary"]


def test_endpoint_notify_approval_with_id_carries_summary():
    tid = tenant_store.create_tenant("Co_M48B", {})["tenant_id"]
    agent_id, apr_id, _ = _register(tid)
    recorded = {}

    class FakeAdapter:
        def send_approval_card(self, to_user, title, url, access_token="", summary=""):
            recorded.update(to_user=to_user, summary=summary)
            return {"ok": True}

    orig_adapter = im_gateway.IMGateway._adapter
    im_gateway.IMGateway._adapter = lambda self, t, p: FakeAdapter()
    try:
        r = client.post("/api/v1/integrations/im/notify-approval",
                        json={"provider": "wecom", "to_user": "lisi", "approval_id": apr_id},
                        headers=_admin())
    finally:
        im_gateway.IMGateway._adapter = orig_adapter

    assert r.status_code == 200, r.text
    assert recorded["to_user"] == "lisi"
    assert "全局" in recorded["summary"]  # 审批助手摘要被自动注入


def test_endpoint_notify_approval_unknown_id_404():
    r = client.post("/api/v1/integrations/im/notify-approval",
                    json={"provider": "wecom", "to_user": "x", "approval_id": "apr_nope"},
                    headers=_admin())
    assert r.status_code == 404
