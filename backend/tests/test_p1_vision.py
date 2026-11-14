"""P1-10 · 多模态图像理解（#305）契约测试。

覆盖：
1. 默认零行为变更：VISION_ENABLED=0 时 status/analyze 不读文件、不联网、不产出结论；
   图片上传仍是 415，/agent/invoke 的 file_ids 原样走旧链路。
2. 类型安全：扩展名 + 魔数双校验，改名的文本文件、超大图、超张数、内容丢失逐项跳过。
3. 可插拔通道：内置 stub 绝不伪造结论；openai_compatible 组装 OpenAI 兼容
   [text, image_url...] messages；HTTP 错误/空正文/网络异常/配置缺失全部结构化降级。
4. 权限：不可见文件统一 404，不区分「不存在」与「无权」。
5. 上下文：成功注入标注「模型解读」的结论，失败注入「不得据此推断」的缺料提示。
6. 集成：/agent/invoke 与 /agent/stream 两条路径都把视觉结论并入 file_context、
   图片不进 tabular.parse、带图轮次不复用语义缓存、响应回 vision 观测字段。
7. HTTP：/vision/status 与 /vision/analyze 的 401 / 404 / code=0 包装 / 审计落点。

测试隔离：清空 file_store 内存、注入假通道，monkeypatch httpx 与 audit.record，
不联网、不依赖任何真实视觉模型或 API key。
"""
from __future__ import annotations

import base64
import json
from typing import Any, Dict

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.security import create_dev_token
from app.main import app
from app.models.caller import CallerContext
from app.routers import invoke as invoke_router
from app.services import (agent_runtime, audit, file_store, semantic_cache,
                          session_store, usage_store, vision)

client = TestClient(app)
API = "/api/v1/vision"

ADMIN_H = {"Authorization": create_dev_token("p_admin", "d_platform", "platform_admin", "t_sys")}
EMP_A_H = {"Authorization": create_dev_token("u_a", "d_a", "employee", "t_a")}
EMP_B_H = {"Authorization": create_dev_token("u_b", "d_x", "employee", "t_b")}

ADMIN = CallerContext(userId="p_admin", deptId="d_platform", role="platform_admin", tenantId="t_sys")
EMP_A = CallerContext(userId="u_a", deptId="d_a", role="employee", tenantId="t_a")
EMP_B = CallerContext(userId="u_b", deptId="d_x", role="employee", tenantId="t_b")

PNG = b"\x89PNG\r\n\x1a\n" + bytes(range(32))
JPG = b"\xff\xd8\xff" + b"\x01" * 32
GIF = b"GIF89a" + b"\x02" * 32
WEBP = b"RIFF" + b"\x00" * 4 + b"WEBP" + b"\x03" * 32
BMP = b"BM" + b"\x04" * 32
DISGUISED = b"this is definitely not an image"


class _Resp:
    def __init__(self, status: int = 200, payload: Dict[str, Any] | None = None,
                 text: str = ""):
        self.status_code = status
        self._payload = payload if payload is not None else {}
        self.text = text

    def json(self):
        return self._payload


class FakeProvider:
    """注入用假视觉通道：可控地返回成功/失败/异常，绝不联网。"""

    name = "fake"
    model = "fake-vision-v1"

    def __init__(self, text: str = "图中表格显示 2026 年 3 月产量 1280 台。",
                 ok: bool = True, real: bool = True, error: str = "", boom: bool = False):
        self.text, self.ok, self.real, self.error, self.boom = text, ok, real, error, boom
        self.calls: list = []

    def describe(self, parts, prompt):
        self.calls.append({"parts": parts, "prompt": prompt})
        if self.boom:
            raise RuntimeError("vision backend exploded")
        return {"ok": self.ok, "text": self.text, "real": self.real,
                "provider": self.name, "model": self.model,
                "usage": {"prompt_tokens": 210, "completion_tokens": 45},
                "error": self.error}


def _put(filename: str, data: bytes, caller: CallerContext = EMP_A,
         kind: str = file_store.KIND_UPLOAD) -> Dict[str, Any]:
    return file_store.put(data, filename, caller, kind=kind,
                          reuse_same_hash=False, meta={"vision": vision.is_image(filename)})


def _enabled(monkeypatch, **kw):
    monkeypatch.setattr(settings, "VISION_ENABLED", True)
    for k, v in kw.items():
        monkeypatch.setattr(settings, k, v)


@pytest.fixture
def env(monkeypatch):
    """清场 + 桩：不落审计、不联网；默认保持开关关闭。"""
    saved_files = dict(file_store._FILES)
    saved_sessions = dict(session_store._SESSIONS)
    file_store._FILES.clear()
    session_store._SESSIONS.clear()
    vision.reset_state()
    monkeypatch.setattr(audit, "record", lambda *a, **k: "trace_ok")
    monkeypatch.setattr(settings, "VISION_ENABLED", False)
    monkeypatch.setattr(settings, "VISION_PROVIDER", "stub")
    monkeypatch.setattr(settings, "VISION_MODEL", "")
    yield
    file_store._FILES.clear()
    file_store._FILES.update(saved_files)
    session_store._SESSIONS.clear()
    session_store._SESSIONS.update(saved_sessions)
    vision.reset_state()


# ── A. 格式识别 ──────────────────────────────────────────────────────────────
def test_ext_of_and_is_image(env):
    assert vision.ext_of("报价.PNG") == "png"
    assert vision.is_image("a.png") and vision.is_image("B.JPEG") and vision.is_image("x.webp")
    assert vision.is_image("a.gif") and vision.is_image("a.bmp") and vision.is_image("a.jpg")
    assert not vision.is_image("台账.xlsx")
    assert not vision.is_image("readme")
    assert not vision.is_image("")


def test_sniff_all_supported_formats(env):
    assert vision.sniff(PNG) == "png"
    assert vision.sniff(JPG) == "jpg"
    assert vision.sniff(GIF) == "gif"
    assert vision.sniff(WEBP) == "webp"
    assert vision.sniff(BMP) == "bmp"


def test_sniff_rejects_disguised_text(env):
    assert vision.sniff(DISGUISED) == ""
    assert vision.sniff(b"") == ""
    assert vision.sniff(b"RIFF\x00\x00\x00\x00JPEG") == ""


def test_mime_of_and_fallback(env):
    assert vision.mime_of("png") == "image/png"
    assert vision.mime_of("jpg") == "image/jpeg"
    assert vision.mime_of("", "chart.webp") == "image/webp"
    assert vision.mime_of("", "a.txt") == "application/octet-stream"


def test_default_prompt_forbids_speculation(env):
    assert "不要推测" in vision.DEFAULT_PROMPT
    assert "无法辨认" in vision.DEFAULT_PROMPT


# ── B. 状态与默认零行为变更 ──────────────────────────────────────────────────
def test_status_disabled_by_default(env):
    st = vision.status()
    assert st["enabled"] is False and st["ready"] is False
    assert st["provider"] == "stub" and st["model"] == ""
    assert "VISION_ENABLED" in st["reason"]
    assert set(st["allowed_ext"]) == vision.IMAGE_EXT
    assert st["registered_providers"] == ["openai_compatible", "stub"]


def test_analyze_disabled_does_not_touch_files(env, monkeypatch):
    def must_not_read(*a, **k):
        raise AssertionError("开关关闭时不应读取任何文件")

    monkeypatch.setattr(file_store, "get", must_not_read)
    monkeypatch.setattr(file_store, "read", must_not_read)
    r = vision.analyze(["file_does_not_exist"], EMP_A)
    assert r["ok"] is False and r["reason"] == "disabled"
    assert r["text"] == "" and r["real"] is False
    assert r["images"] == [] and "VISION_ENABLED" in r["error"]


def test_status_enabled_without_model_is_not_ready(env, monkeypatch):
    _enabled(monkeypatch, VISION_PROVIDER="openai_compatible", VISION_MODEL="")
    st = vision.status()
    assert st["enabled"] is True and st["ready"] is False
    assert st["provider"] == "stub"
    assert "VISION_MODEL" in st["reason"] or "未配置" in st["reason"]


def test_status_ready_with_injected_provider(env, monkeypatch):
    _enabled(monkeypatch)
    vision.set_provider(FakeProvider())
    st = vision.status()
    assert st["ready"] is True and st["provider"] == "fake"
    assert st["model"] == "fake-vision-v1" and st["reason"] == ""
    assert st["override_active"] is True


def test_register_provider_and_reset_state(env, monkeypatch):
    class Custom:
        name, model = "custom", "c1"

    vision.register_provider("custom", Custom)
    monkeypatch.setattr(settings, "VISION_PROVIDER", "custom")
    assert vision.get_provider().name == "custom"
    assert "custom" in vision.status()["registered_providers"]
    vision.reset_state()
    assert "custom" not in vision.status()["registered_providers"]


def test_unknown_provider_falls_back_to_stub(env, monkeypatch):
    monkeypatch.setattr(settings, "VISION_PROVIDER", "no_such_channel")
    assert isinstance(vision.get_provider(), vision.StubProvider)


# ── C. 诚实降级 ──────────────────────────────────────────────────────────────
def test_stub_provider_never_fabricates(env):
    r = vision.StubProvider().describe([{"type": "image_url"}], "描述这张图")
    assert r["ok"] is False and r["text"] == "" and r["real"] is False
    assert "未配置" in r["error"]


def test_analyze_with_stub_enabled_reports_unavailable(env, monkeypatch):
    _enabled(monkeypatch)
    rec = _put("chart.png", PNG)
    r = vision.analyze([rec["file_id"]], EMP_A)
    assert r["ok"] is False and r["text"] == "" and r["reason"] == "unavailable"
    assert r["real"] is False and r["error"]
    assert [i["filename"] for i in r["images"]] == ["chart.png"]
    assert r["total_bytes"] == len(PNG)


def test_analyze_no_images(env, monkeypatch):
    _enabled(monkeypatch)
    vision.set_provider(FakeProvider())
    r = vision.analyze([], EMP_A)
    assert r["ok"] is False and r["reason"] == "no_images" and r["text"] == ""
    assert "没有可识别" in r["error"]


def test_analyze_provider_exception_is_structured(env, monkeypatch):
    _enabled(monkeypatch)
    vision.set_provider(FakeProvider(boom=True))
    rec = _put("chart.png", PNG)
    r = vision.analyze([rec["file_id"]], EMP_A)
    assert r["ok"] is False and r["text"] == "" and r["reason"] == "unavailable"
    assert "provider_error" in r["error"] and "RuntimeError" in r["error"]


def test_analyze_drops_text_when_provider_says_not_real(env, monkeypatch):
    _enabled(monkeypatch)
    vision.set_provider(FakeProvider(text="看起来像 1280 台", real=False))
    rec = _put("chart.png", PNG)
    r = vision.analyze([rec["file_id"]], EMP_A)
    assert r["ok"] is False and r["text"] == "" and r["real"] is False


# ── D. prepare：权限、格式、限额 ─────────────────────────────────────────────
def test_prepare_builds_openai_compatible_parts(env, monkeypatch):
    _enabled(monkeypatch)
    rec = _put("chart.png", PNG)
    out = vision.prepare([rec["file_id"]], EMP_A)
    assert len(out["parts"]) == 1 and out["parts"][0]["type"] == "image_url"
    url = out["parts"][0]["image_url"]["url"]
    assert url.startswith("data:image/png;base64,")
    assert base64.b64decode(url.split(",", 1)[1]) == PNG
    assert out["images"][0]["format"] == "png" and out["images"][0]["size"] == len(PNG)
    assert out["total_bytes"] == len(PNG) and out["errors"] == []


def test_prepare_missing_and_inaccessible_file_are_404(env):
    with pytest.raises(HTTPException) as e:
        vision.prepare(["file_nonexistent"], EMP_A)
    assert e.value.status_code == 404
    other = _put("private.png", PNG, caller=EMP_B)
    with pytest.raises(HTTPException) as e2:
        vision.prepare([other["file_id"]], EMP_A)
    assert e2.value.status_code == 404


def test_prepare_platform_admin_can_read_any_visible_image(env):
    other = _put("private.png", PNG, caller=EMP_B)
    out = vision.prepare([other["file_id"]], ADMIN)
    assert len(out["parts"]) == 1


def test_prepare_skips_non_image_with_error(env):
    rec = _put("ledger.csv", b"a,b\n1,2\n")
    out = vision.prepare([rec["file_id"]], EMP_A)
    assert out["parts"] == [] and out["images"] == []
    assert "非图片" in out["errors"][0]["error"]


def test_prepare_rejects_disguised_extension(env):
    rec = _put("fake.png", DISGUISED)
    out = vision.prepare([rec["file_id"]], EMP_A)
    assert out["parts"] == []
    assert "文件头不符" in out["errors"][0]["error"]


def test_prepare_caps_image_count(env, monkeypatch):
    _enabled(monkeypatch, VISION_MAX_IMAGES=2)
    ids = [_put(f"c{i}.png", PNG + bytes([i]))["file_id"] for i in range(3)]
    out = vision.prepare(ids, EMP_A)
    assert len(out["parts"]) == 2 and out["skipped"] == 1
    assert "超过上限" in out["errors"][-1]["error"]


def test_prepare_caps_image_bytes(env, monkeypatch):
    _enabled(monkeypatch, VISION_MAX_IMAGE_BYTES=10)
    rec = _put("big.png", PNG)
    out = vision.prepare([rec["file_id"]], EMP_A)
    assert out["parts"] == [] and "超过上限" in out["errors"][0]["error"]


def test_prepare_lost_content_does_not_break_other_images(env, monkeypatch):
    good = _put("good.png", PNG)
    bad = _put("bad.jpg", JPG)
    real_read = file_store.read

    def fake_read(rec):
        return None if rec["file_id"] == bad["file_id"] else real_read(rec)

    monkeypatch.setattr(file_store, "read", fake_read)
    out = vision.prepare([bad["file_id"], good["file_id"]], EMP_A)
    assert len(out["parts"]) == 1
    assert "丢失" in out["errors"][0]["error"]


# ── E. OpenAI 兼容通道 ───────────────────────────────────────────────────────
def _ok_payload(content="图中可见：产量 1280 台。", model="glm-4v-flash"):
    return {"model": model, "choices": [{"message": {"content": content}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20}}


def test_openai_provider_payload_and_success(env, monkeypatch):
    calls = {}

    def fake_post(url, **kw):
        calls.update({"url": url, **kw})
        return _Resp(200, _ok_payload())

    monkeypatch.setattr(vision.httpx, "post", fake_post)
    p = vision.OpenAICompatibleProvider(model="glm-4v-flash",
                                        base_url="https://vision.example.com/v4",
                                        api_key="secret")
    part = {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}
    r = p.describe([part], "读一下这张图")
    assert r["ok"] is True and r["real"] is True
    assert r["text"] == "图中可见：产量 1280 台。"
    assert r["usage"]["prompt_tokens"] == 100 and r["model"] == "glm-4v-flash"
    assert calls["url"] == "https://vision.example.com/v4/chat/completions"
    body = calls["json"]
    assert body["model"] == "glm-4v-flash" and body["temperature"] == 0
    assert body["messages"][0]["role"] == "system"
    content = body["messages"][1]["content"]
    assert content[0] == {"type": "text", "text": "读一下这张图"}
    assert content[1] == part
    assert calls["headers"]["Authorization"] == "Bearer secret"


def test_openai_provider_http_error_and_empty_body(env, monkeypatch):
    monkeypatch.setattr(vision.httpx, "post",
                        lambda url, **kw: _Resp(400, text="bad model"))
    r1 = vision.OpenAICompatibleProvider(model="m", base_url="https://x/v1").describe([], "q")
    assert r1["ok"] is False and r1["text"] == "" and "HTTP 400" in r1["error"]

    monkeypatch.setattr(vision.httpx, "post",
                        lambda url, **kw: _Resp(200, _ok_payload(content="   ")))
    r2 = vision.OpenAICompatibleProvider(model="m", base_url="https://x/v1").describe([], "q")
    assert r2["ok"] is False and "正文为空" in r2["error"]


def test_openai_provider_missing_config(env, monkeypatch):
    def must_not_post(*a, **k):
        raise AssertionError("缺少配置时不应发起网络请求")

    monkeypatch.setattr(vision.httpx, "post", must_not_post)
    p = vision.OpenAICompatibleProvider(model="", base_url="", api_key="")
    r = p.describe([], "q")
    assert r["ok"] is False and "VISION_BASE_URL" in r["error"]


def test_openai_provider_network_exception(env, monkeypatch):
    def boom(*a, **k):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(vision.httpx, "post", boom)
    r = vision.OpenAICompatibleProvider(model="m", base_url="https://x/v1").describe([], "q")
    assert r["ok"] is False and r["text"] == "" and "ConnectError" in r["error"]


def test_openai_provider_joins_content_blocks(env, monkeypatch):
    blocks = [{"type": "text", "text": "图中可见 "}, {"type": "text", "text": "产量 1280 台"}]
    monkeypatch.setattr(vision.httpx, "post",
                        lambda url, **kw: _Resp(200, _ok_payload(content=blocks)))
    r = vision.OpenAICompatibleProvider(model="m", base_url="https://x/v1").describe([], "q")
    assert r["ok"] is True and r["text"] == "图中可见 产量 1280 台"


def test_openai_provider_endpoint_keeps_full_url(env):
    p = vision.OpenAICompatibleProvider(model="m", base_url="https://x/v1/chat/completions")
    assert p.endpoint() == "https://x/v1/chat/completions"


# ── F. analyze 结果与上下文 ──────────────────────────────────────────────────
def test_analyze_success_with_fake_provider(env, monkeypatch):
    _enabled(monkeypatch)
    fake = FakeProvider()
    vision.set_provider(fake)
    rec = _put("chart.png", PNG)
    r = vision.analyze([rec["file_id"]], EMP_A, prompt="读出产量")
    assert r["ok"] is True and r["reason"] == "ok" and r["real"] is True
    assert r["text"] == "图中表格显示 2026 年 3 月产量 1280 台。"
    assert r["provider"] == "fake" and r["model"] == "fake-vision-v1"
    assert r["usage"]["prompt_tokens"] == 210
    assert r["prompt"] == "读出产量"
    assert fake.calls[0]["parts"][0]["type"] == "image_url"
    assert r["elapsed_ms"] >= 0


def test_analyze_truncates_prompt(env, monkeypatch):
    _enabled(monkeypatch, VISION_MAX_PROMPT_CHARS=20)
    vision.set_provider(FakeProvider())
    rec = _put("chart.png", PNG)
    r = vision.analyze([rec["file_id"]], EMP_A, prompt="甲" * 100)
    assert len(r["prompt"]) == 20


def test_build_context_success(env, monkeypatch):
    r = {"ok": True, "text": "产量 1280 台",
         "images": [{"filename": "chart.png", "file_id": "f1"}]}
    ctx = vision.build_context(r)
    assert "图像理解结果" in ctx and "模型解读" in ctx and "chart.png" in ctx
    assert "产量 1280 台" in ctx
    assert vision.build_failure_hint(r) == ""
    assert vision.build_context({"ok": False, "text": "随便猜的"}) == ""


def test_build_failure_hint_blocks_speculation(env):
    r = {"ok": False, "reason": "unavailable", "error": "视觉通道未配置",
         "images": [{"filename": "chart.png"}]}
    hint = vision.build_failure_hint(r)
    assert "未产出结论" in hint and "chart.png" in hint
    assert "不得据此推断" in hint and "视觉通道未配置" in hint


def test_analyze_context_field_disabled_is_empty(env):
    r = vision.analyze([], EMP_A)
    assert r["context"] == "" and r["reason"] == "disabled"


def test_analyze_context_field_success(env, monkeypatch):
    _enabled(monkeypatch)
    vision.set_provider(FakeProvider())
    rec = _put("chart.png", PNG)
    r = vision.analyze([rec["file_id"]], EMP_A)
    assert "图像理解结果" in r["context"] and "模型解读" in r["context"]
    assert r["text"] in r["context"]


def test_analyze_context_field_failure_is_hint(env, monkeypatch):
    _enabled(monkeypatch)
    rec = _put("chart.png", PNG)
    r = vision.analyze([rec["file_id"]], EMP_A)
    assert r["ok"] is False and "不得据此推断" in r["context"]


# ── G. HTTP 路由 ─────────────────────────────────────────────────────────────
def test_http_status_requires_auth(env):
    assert client.get(f"{API}/status").status_code == 401
    r = client.get(f"{API}/status", headers=ADMIN_H)
    assert r.status_code == 200 and r.json()["code"] == 0
    assert r.json()["data"]["enabled"] is False


def test_http_analyze_disabled(env):
    r = client.post(f"{API}/analyze", headers=EMP_A_H, json={"file_ids": [], "prompt": "x"})
    assert r.status_code == 200 and r.json()["code"] == 0
    data = r.json()["data"]
    assert data["ok"] is False and data["reason"] == "disabled" and data["text"] == ""


def test_http_analyze_requires_auth(env):
    assert client.post(f"{API}/analyze", json={"file_ids": []}).status_code == 401


def test_http_analyze_inaccessible_file_404(env, monkeypatch):
    _enabled(monkeypatch)
    other = _put("private.png", PNG, caller=EMP_B)
    r = client.post(f"{API}/analyze", headers=EMP_A_H,
                    json={"file_ids": [other["file_id"]]})
    assert r.status_code == 404


def test_http_analyze_success_and_audit(env, monkeypatch):
    _enabled(monkeypatch)
    vision.set_provider(FakeProvider())
    rec = _put("chart.png", PNG)
    seen = []
    monkeypatch.setattr(audit, "record", lambda *a, **k: seen.append((a, k)) or "trace_ok")
    r = client.post(f"{API}/analyze", headers=EMP_A_H,
                    json={"file_ids": [rec["file_id"]], "prompt": "读图"})
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["ok"] is True and data["text"].startswith("图中表格显示")
    assert seen and seen[0][0][0] == "vision_analyze"
    assert seen[0][1]["result"] == "ok" and seen[0][1]["meta"]["images"] == 1


# ── H. 上传白名单与图片预览 ──────────────────────────────────────────────────
def test_upload_image_415_when_disabled(env):
    r = client.post("/api/v1/agent/files", headers=EMP_A_H,
                    files={"file": ("chart.png", PNG, "image/png")})
    assert r.status_code == 415


def test_upload_image_ok_when_enabled(env, monkeypatch):
    _enabled(monkeypatch)
    r = client.post("/api/v1/agent/files", headers=EMP_A_H,
                    files={"file": ("chart.png", PNG, "image/png")})
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["meta"]["vision"] is True and d["content_type"] == "image/png"
    assert d["preview"]["kind"] == "image" and d["preview"]["valid"] is True
    assert d["preview"]["format"] == "png" and d["meta"]["parse_ok"] is True


def test_upload_image_does_not_go_through_tabular(env, monkeypatch):
    _enabled(monkeypatch)
    r = client.post("/api/v1/agent/files", headers=EMP_A_H,
                    files={"file": ("chart.png", PNG, "image/png")})
    p = r.json()["data"]["preview"]
    assert p["ok"] is True and p["kind"] == "image"
    assert "hint" in p and "表格解析" in p["hint"]


def test_preview_image_returns_data_url(env, monkeypatch):
    _enabled(monkeypatch)
    rec = _put("chart.png", PNG)
    r = client.get(f"/api/v1/agent/files/{rec['file_id']}/preview", headers=EMP_A_H)
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["kind"] == "image" and d["data_url"].startswith("data:image/png;base64,")
    assert base64.b64decode(d["data_url"].split(",", 1)[1]) == PNG


def test_download_image_has_correct_content_type(env, monkeypatch):
    rec = _put("chart.png", PNG)
    r = client.get(f"/api/v1/agent/files/{rec['file_id']}", headers=EMP_A_H)
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/png")
    assert r.content == PNG


# ── I. invoke / stream 集成 ──────────────────────────────────────────────────
def test_split_file_ids_disabled_keeps_legacy_path(env):
    ids = ["f1", "f2"]
    assert invoke_router._split_file_ids(ids, EMP_A) == (ids, [])


def test_split_file_ids_enabled_separates_images(env, monkeypatch):
    _enabled(monkeypatch)
    csv = _put("ledger.csv", b"a,b\n1,2\n")
    png = _put("chart.png", PNG)
    table_ids, image_ids = invoke_router._split_file_ids(
        [csv["file_id"], png["file_id"]], EMP_A)
    assert table_ids == [csv["file_id"]] and image_ids == [png["file_id"]]


def test_split_file_ids_keeps_inaccessible_image_in_table_path(env, monkeypatch):
    _enabled(monkeypatch)
    other = _put("private.png", PNG, caller=EMP_B)
    table_ids, image_ids = invoke_router._split_file_ids([other["file_id"]], EMP_A)
    assert table_ids == [other["file_id"]] and image_ids == []
    with pytest.raises(HTTPException) as e:
        invoke_router._load_attachments(table_ids, EMP_A)
    assert e.value.status_code == 404


def test_build_file_context_matches_legacy_shape(env):
    rec = _put("ledger.csv", b"a,b\n1,2\n")
    from app.services import tabular
    parsed = tabular.parse(b"a,b\n1,2\n", "ledger.csv")
    ctx = invoke_router._build_file_context([(rec, parsed)])
    assert "【本次随附材料】" in ctx and "ledger.csv" in ctx
    assert "清单里已有的资料不得写成「未提供」" in ctx
    assert invoke_router._build_file_context([]) == ""


def test_vision_context_success_and_failure(env, monkeypatch):
    _enabled(monkeypatch)
    rec = _put("chart.png", PNG)
    vision.set_provider(FakeProvider())
    ok_ctx, ok_info = invoke_router._vision_context([rec["file_id"]], EMP_A, "读图")
    assert "图像理解结果" in ok_ctx and "1280" in ok_ctx
    assert ok_info["ok"] is True and ok_info["provider"] == "fake"

    vision.clear_provider()
    fail_ctx, fail_info = invoke_router._vision_context([rec["file_id"]], EMP_A, "读图")
    assert "未产出结论" in fail_ctx and "不得据此推断" in fail_ctx
    assert fail_info["ok"] is False and fail_info["real"] is False
    assert fail_info["reason"] == "unavailable" and fail_info["error"]


def test_vision_context_handles_channel_exception(env, monkeypatch):
    _enabled(monkeypatch)
    rec = _put("chart.png", PNG)

    def boom(*a, **k):
        raise RuntimeError("channel down")

    monkeypatch.setattr(vision, "analyze", boom)
    ctx, info = invoke_router._vision_context([rec["file_id"]], EMP_A, "读图")
    assert "不得据此推断" in ctx and "RuntimeError" in ctx
    assert info["ok"] is False and info["reason"] == "provider_error"
    assert "RuntimeError" in info["error"]


def _install_invoke_stubs(monkeypatch, seen):
    """把 /agent/invoke 的外部依赖桩掉，只留我们要验证的编排逻辑。"""
    from app.routers import agents as agents_router
    saved = dict(agents_router._AGENTS)
    agents_router._AGENTS.clear()
    agents_router._AGENTS["a1"] = {
        "agent_id": "a1", "name": "测试智能体", "status": "published",
        "system_prompt": "你是测试助手", "adapter_type": "llm", "outputs_contract": "",
        "tenant_id": "", "model_id": "",
    }

    def fake_run_agent(*a, **kw):
        seen["file_context"] = kw.get("file_context") or ""
        seen["run_agent_called"] = True
        return {
            "answer": "已结合图片给出结论。", "citations": [], "web_sources": [],
            "web_search": None, "query_rewritten": False, "turn_no": 1,
            "usage": {"model_id": "glm-4.6", "real": True, "usage": {},
                      "latency_ms": 10, "prompt_text": "", "completion_text": ""},
            "routed_tier": None, "routed_model": None, "tool_calls": [],
            "skill_applied": False,
        }

    async def fake_stream(*a, **kw):
        seen["stream_file_context"] = kw.get("file_context") or ""
        yield {"type": "done", "result": fake_run_agent()}

    monkeypatch.setattr(agent_runtime, "run_agent", fake_run_agent)
    monkeypatch.setattr(agent_runtime, "run_agent_stream", fake_stream)
    monkeypatch.setattr(usage_store, "enforce_quota", lambda caller: None)
    monkeypatch.setattr(usage_store, "record", lambda *a, **k: {
        "model_id": "glm-4.6", "prompt_tokens": 10, "completion_tokens": 5,
        "cost": 0.0, "real": True, "latency_ms": 10})
    monkeypatch.setattr(usage_store, "quota_status", lambda caller: {})
    monkeypatch.setattr(invoke_router, "_build_artifacts", lambda *a, **k: [])
    monkeypatch.setattr(invoke_router, "_schedule_memory_extract", lambda *a, **k: None)
    monkeypatch.setattr(invoke_router, "_m27_postprocess", lambda *a, **k: {
        "guardrails": {}, "cache": {}, "routing": {}, "confidence": None,
        "handoff": None, "quality_recorded": False, "errors": []})
    monkeypatch.setattr(semantic_cache, "get", lambda *a, **k: {
        "answer": "这是缓存答案", "meta": {"real": True}, "sim_mode": ""})
    return saved, agents_router


def test_invoke_injects_vision_context_and_skips_cache(env, monkeypatch):
    _enabled(monkeypatch)
    vision.set_provider(FakeProvider())
    img = _put("chart.png", PNG)
    seen: Dict[str, Any] = {}
    saved, agents_router = _install_invoke_stubs(monkeypatch, seen)
    monkeypatch.setattr(settings, "SEMANTIC_CACHE_SERVE", True)
    monkeypatch.setattr(settings, "SEMANTIC_CACHE_ENABLED", True)
    try:
        # 只挂图片、不挂表格：这样才能单独验证「not image_ids」这一条缓存红线，
        # 否则 attachments 非空本来就会挡住缓存，测不到图片的作用。
        r = client.post("/api/v1/agent/invoke", headers=EMP_A_H, json={
            "agent_id": "a1", "input": "根据图片给出结论",
            "file_ids": [img["file_id"]],
        })
        assert r.status_code == 200, r.text
        d = r.json()["data"]
        assert d["vision"]["ok"] is True and d["vision"]["provider"] == "fake"
        # 图片不进表格解析链，也不会出现在 files_used 里
        assert d["files_used"] == []
        # 视觉结论进入 file_context（本次没有表格附件，故无「随附材料」段）
        assert "图像理解结果" in seen["file_context"] and "1280" in seen["file_context"]
        assert "【本次随附材料】" not in seen["file_context"]
        # 带图轮次不得复用语义缓存答案
        assert seen["run_agent_called"] is True
        assert d["answer"] == "已结合图片给出结论。"
        assert not d.get("cached")
    finally:
        agents_router._AGENTS.clear()
        agents_router._AGENTS.update(saved)


def test_invoke_disabled_keeps_legacy_behavior(env, monkeypatch):
    """开关关闭时：不出现 vision 成功、file_ids 全部走旧解析链。"""
    csv = _put("ledger.csv", b"a,b\n1,2\n")
    seen: Dict[str, Any] = {}
    saved, agents_router = _install_invoke_stubs(monkeypatch, seen)
    try:
        r = client.post("/api/v1/agent/invoke", headers=EMP_A_H, json={
            "agent_id": "a1", "input": "总结台账", "file_ids": [csv["file_id"]],
        })
        assert r.status_code == 200, r.text
        d = r.json()["data"]
        assert d["vision"]["enabled"] is False and d["vision"]["ok"] is False
        assert "图像理解结果" not in seen["file_context"]
        assert [f["filename"] for f in d["files_used"]] == ["ledger.csv"]
    finally:
        agents_router._AGENTS.clear()
        agents_router._AGENTS.update(saved)


def test_stream_injects_vision_context(env, monkeypatch):
    _enabled(monkeypatch)
    vision.set_provider(FakeProvider())
    img = _put("chart.png", PNG)
    seen: Dict[str, Any] = {}
    saved, agents_router = _install_invoke_stubs(monkeypatch, seen)
    try:
        r = client.post("/api/v1/agent/stream", headers=EMP_A_H, json={
            "agent_id": "a1", "input": "读图", "file_ids": [img["file_id"]],
        })
        assert r.status_code == 200, r.text
        events = [json.loads(line[6:]) for line in r.text.splitlines()
                  if line.startswith("data: ")]
        fin = next(e for e in events if e.get("type") == "finalized")
        assert fin["vision"]["ok"] is True
        assert "图像理解结果" in seen["stream_file_context"]
        assert "1280" in seen["stream_file_context"]
    finally:
        agents_router._AGENTS.clear()
        agents_router._AGENTS.update(saved)
