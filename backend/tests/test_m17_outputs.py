"""M17 · 真模型接入 + 多格式产出物测试。

覆盖四件事：
1. 双通道接入：direct 模式下端点/鉴权头/请求体按厂商正确生成（不真发请求）
2. 失败不静默：LLM_STRICT=1 时抛 LLMUnavailable，invoke 转 502，绝不出 stub 结果
   —— 这是 M17 最重要的一条：「链路通了」不等于「效果达标」
3. 多格式渲染：xlsx/docx/pptx/html/pdf 五种都能出件，且结构正确（不是空壳文件）
4. 格式协商：默认 html（平台优先 HTML）、auto 按技能包产出契约猜（猜不到落 html）、非法值被丢弃
"""
import io
import re

import pytest
from fastapi.testclient import TestClient

from app.core.security import create_dev_token
from app.main import app
from app.services import artifact, model_gateway, renderers

client = TestClient(app)
ADMIN = {"Authorization": create_dev_token("p_admin", "d_platform", "platform_admin")}
EMP = {"Authorization": create_dev_token("u_zhang", "d_sales", "employee")}

SKILL_PROMPT = "你是「费用异常波动分析」执行专家。\n## 工作流程\n1. 环比 2. 量价分离\n"

ANSWER = """## 一、费用环比
保洁费由 10000 元涨至 12000 元，涨幅 20%。

## 二、量价分离
| 科目 | 本期 | 上期 | 涨幅 | 量差 | 价差 |
|---|---|---|---|---|---|
| 保洁费 | 12000 | 10000 | 20% | 0 | 2000 |

## 三、涨价归因
保洁费为纯价涨：包干改按量计费。

## 四、异常清单
| 科目 | 归因 | 说明 | 处置 |
|---|---|---|---|
| 保洁费 | 价涨 | 耗材改按量计费 | 重新议价 |
"""

META = {"agent_name": "费用异常波动分析", "agent_id": "a1",
        "input_files": ["费用台账.xlsx"], "model_id": "glm-4.6",
        "run_id": "run_t1", "trace_id": "tr_t1", "citations": []}

SUMMARY = [["文件", "工作表", "行数", "列数", "列名（类型）"],
           ["费用台账.xlsx", "Sheet1", "9", "4", "月份/科目/供应商/金额"]]


# ────────────── 1. 双通道接入（不真发请求，只验组装） ──────────────
def test_direct_provider_builds_vendor_endpoint(monkeypatch):
    """direct 模式：按 vendor 拼端点，密钥从厂商惯用环境变量取。"""
    monkeypatch.setattr(model_gateway.settings, "LLM_PROVIDER", "direct")
    monkeypatch.setattr(model_gateway.settings, "LLM_VENDOR", "zhipu")
    monkeypatch.setattr(model_gateway.settings, "LLM_BASE_URL", "")
    monkeypatch.setattr(model_gateway.settings, "LLM_API_KEY", "sk-test-123")

    assert model_gateway._endpoint() == "https://open.bigmodel.cn/api/paas/v4/chat/completions"
    assert model_gateway._headers()["Authorization"] == "Bearer sk-test-123"
    mode = model_gateway.access_mode()
    assert mode["provider"] == "direct" and mode["key_configured"] is True


def test_litellm_provider_keeps_proxy_endpoint(monkeypatch):
    """litellm 模式行为不变（老部署不因 M17 改配置）。"""
    monkeypatch.setattr(model_gateway.settings, "LLM_PROVIDER", "litellm")
    monkeypatch.setattr(model_gateway.settings, "LITELLM_BASE_URL", "http://127.0.0.1:4000")
    assert model_gateway._endpoint() == "http://127.0.0.1:4000/v1/chat/completions"


def test_payload_disables_thinking_for_glm46(monkeypatch):
    """结构化表格任务关掉混合思考：省 token 且同输入两次结论一致。"""
    monkeypatch.setattr(model_gateway.settings, "LLM_DISABLE_THINKING", True)
    body = model_gateway._payload("glm-4.6", [{"role": "user", "content": "x"}])
    assert body["thinking"] == {"type": "disabled"}
    assert body["max_tokens"] >= 4096          # 给小了多张表会被截断
    # 非思考型模型不加这个字段，避免厂商 400
    assert "thinking" not in model_gateway._payload("glm-4-flash", [{"role": "user", "content": "x"}])


def test_extract_falls_back_to_reasoning_content():
    """思考型模型正文空时要兜底，否则会返回空串还当成功。"""
    text, usage = model_gateway._extract({
        "choices": [{"message": {"content": "", "reasoning_content": "结论如下"}}],
        "usage": {"prompt_tokens": 12, "completion_tokens": 5},
    })
    assert text == "结论如下" and usage["prompt_tokens"] == 12


# ────────────── 2. 失败不静默 ──────────────
def test_strict_raises_instead_of_stub(monkeypatch):
    monkeypatch.setattr(model_gateway.settings, "LLM_STRICT", True)
    monkeypatch.setattr(model_gateway, "llm_available", lambda force=False: False)
    with pytest.raises(model_gateway.LLMUnavailable):
        model_gateway.chat_sync_ex("glm-4.6", [{"role": "user", "content": "hi"}])


def test_non_strict_returns_error_field(monkeypatch):
    """开发/CI 仍允许回退 stub，但必须带 error，让上层能看见真相。"""
    monkeypatch.setattr(model_gateway.settings, "LLM_STRICT", False)
    monkeypatch.setattr(model_gateway, "llm_available", lambda force=False: False)
    out = model_gateway.chat_sync_ex("glm-4.6", [{"role": "user", "content": "hi"}])
    assert out["real"] is False and out["error"]
    assert out["text"].startswith("[stub")


def test_llm_unavailable_is_not_runtime_error():
    """不能继承 RuntimeError：否则会被 agent_runtime 的 LangGraph 降级分支误捕。"""
    assert not issubclass(model_gateway.LLMUnavailable, RuntimeError)


def test_invoke_returns_502_when_model_fails(monkeypatch):
    from app.services import agent_store
    agent_store.save({"agent_id": "ag_m17_fail", "name": "M17 故障用例",
                      "adapter_type": "llm", "knowledge_scope": "global",
                      "model_id": "glm-4.6", "registered_by": "p_admin",
                      "dept_id": "d_platform", "status": "published",
                      "system_prompt": SKILL_PROMPT})
    monkeypatch.setattr(model_gateway.settings, "LLM_STRICT", True)
    monkeypatch.setattr(model_gateway, "llm_available", lambda force=False: False)
    r = client.post("/api/v1/agent/invoke",
                    json={"agent_id": "ag_m17_fail", "input": "跑一下", "stream": False},
                    headers=ADMIN)
    assert r.status_code == 502, r.text
    assert "模型调用失败" in r.json()["detail"]


# ────────────── 3. 多格式渲染 ──────────────
def test_all_five_formats_produce_real_files():
    sizes = {}
    for fmt in ("xlsx", "docx", "pptx", "html", "pdf"):
        data = artifact.build(fmt, ANSWER, META, SUMMARY, None)
        assert isinstance(data, bytes) and len(data) > 2000, fmt
        sizes[fmt] = len(data)
    assert set(sizes) == set(artifact.SUPPORTED_FORMATS)


def test_docx_structure_has_tables_and_headings():
    from docx import Document
    d = Document(io.BytesIO(artifact.build("docx", ANSWER, META, SUMMARY, None)))
    texts = "\n".join(p.text for p in d.paragraphs)
    assert "费用异常波动分析" in texts
    assert "量价分离" in texts
    assert len(d.tables) >= 3           # 元信息 + 两张数据表


def test_pptx_has_cover_conclusion_and_table_slides():
    from pptx import Presentation
    prs = Presentation(io.BytesIO(artifact.build("pptx", ANSWER, META, SUMMARY, None)))
    assert len(prs.slides) >= 4
    # 每页都要有内容（空页是最常见的"渲染成功但没用"）
    for s in prs.slides:
        assert len(s.shapes) >= 1


def test_html_is_standalone_and_escaped():
    html = artifact.build("html", ANSWER + "\n<script>alert(1)</script>",
                          META, SUMMARY, None).decode("utf-8")
    assert html.startswith("<!DOCTYPE html>")
    assert "1F4E79" in html                       # 品牌色进了样式
    assert "<script>alert(1)</script>" not in html  # 模型输出必须转义
    assert "&lt;script&gt;" in html


def test_pdf_is_valid_and_multipage_for_long_table():
    data = artifact.build("pdf", ANSWER, META, SUMMARY, None)
    assert data[:5] == b"%PDF-"
    long_answer = "## 一、明细\n| 序号 | 科目 | 金额 |\n|---|---|---|\n" + \
                  "".join(f"| {i} | 科目{i} | {i * 100} |\n" for i in range(1, 120))
    big = artifact.build("pdf", long_answer, META, None, None)
    pages = len(re.findall(rb"/Type\s*/Page[^s]", big))
    assert pages >= 2, "长表必须分页，不能截断"


def test_renderers_share_one_model():
    """四种格式共用文档模型：章节/表格解析只做一次，避免各格式字段漂移。"""
    m = renderers.build_model(ANSWER, META, SUMMARY)
    assert m.title == "费用异常波动分析"
    assert [s.heading for s in m.sections] == ["费用环比", "量价分离", "涨价归因", "异常清单"]
    # 表格必须挂在自己的章节下 —— 早先版本把表全堆文末，
    # 导致「量价分离」这种只有表没有正文的章节标题整段消失。
    assert [len(s.tables) for s in m.sections] == [0, 1, 0, 1]
    assert m.sections[1].tables[0]["headers"][:2] == ["科目", "本期"]
    assert len(m.tables) == 2


# ────────────── 4. 格式协商 ──────────────
def test_normalize_formats_defaults_to_html():
    """平台默认优先 HTML：不传/空 → html，对方要其它格式再显式传。"""
    assert artifact.normalize_formats(None) == ["html"]
    assert artifact.normalize_formats([]) == ["html"]


def test_normalize_formats_dedupes_and_drops_unknown():
    assert artifact.normalize_formats(["DOCX", "docx", "pdf", "exe"]) == ["docx", "pdf"]


def test_normalize_formats_auto_from_contract():
    assert artifact.normalize_formats(["auto"], "产出：汇报用 PPT 一份") == ["pptx"]
    assert artifact.normalize_formats(["auto"], "输出 Word 文档") == ["docx"]
    # 契约没点名任何格式 → 平台默认 HTML（不再回退 xlsx）
    assert artifact.normalize_formats(["auto"], "") == ["html"]


# ────────────── 5. 端到端：一次调用出多格式 ──────────────
def _xlsx_bytes():
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.append(["月份", "科目", "供应商", "金额"])
    ws.append(["2026-02", "保洁费", "外包A", 10000])
    ws.append(["2026-03", "保洁费", "外包A", 12000])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_invoke_outputs_multiple_formats_and_downloads():
    from app.services import agent_store
    agent_store.save({"agent_id": "ag_m17_multi", "name": "M17 多格式用例",
                      "adapter_type": "llm", "knowledge_scope": "global",
                      "model_id": "glm-4.6", "registered_by": "p_admin",
                      "dept_id": "d_platform", "status": "published",
                      "system_prompt": SKILL_PROMPT})

    up = client.post("/api/v1/agent/files",
                     files={"file": ("费用台账.xlsx", _xlsx_bytes(), "application/octet-stream")},
                     headers=ADMIN)
    assert up.status_code == 200, up.text
    fid = up.json()["data"]["file_id"]

    r = client.post("/api/v1/agent/invoke",
                    json={"agent_id": "ag_m17_multi", "input": "做量价分离", "stream": False,
                          "file_ids": [fid], "output_formats": ["xlsx", "docx", "html"]},
                    headers=ADMIN)
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    fmts = {a["meta"].get("format") for a in data["artifacts"]}
    assert fmts == {"xlsx", "docx", "html"}
    assert data["output_formats"] == sorted(fmts) or set(data["output_formats"]) == fmts
    assert data["artifact_errors"] == []

    for a in data["artifacts"]:
        got = client.get(a["download_url"], headers=ADMIN)
        assert got.status_code == 200
        assert len(got.content) > 1000
        # Content-Type 必须与实际格式一致，否则浏览器会存成 bin
        assert a["content_type"] == artifact.MEDIA_TYPES[a["meta"]["format"]]


def test_invoke_default_format_is_html():
    """不传 output_formats → 平台默认 HTML（优先网页交付）。"""
    from app.services import agent_store
    agent_store.save({"agent_id": "ag_m17_default", "name": "M17 默认格式",
                      "adapter_type": "llm", "knowledge_scope": "global",
                      "model_id": "glm-4.6", "registered_by": "p_admin",
                      "dept_id": "d_platform", "status": "published",
                      "system_prompt": SKILL_PROMPT})
    r = client.post("/api/v1/agent/invoke",
                    json={"agent_id": "ag_m17_default", "input": "跑一下", "stream": False},
                    headers=ADMIN)
    assert r.status_code == 200, r.text
    fmts = [a["meta"]["format"] for a in r.json()["data"]["artifacts"]]
    assert fmts == ["html"]


def test_gateway_status_exposes_provider_and_key_state():
    r = client.get("/api/v1/models/gateway", headers=EMP)
    assert r.status_code == 200
    g = r.json()["data"]
    for k in ("provider", "vendor", "key_configured", "strict", "default_model"):
        assert k in g
    assert g["default_model"] == model_gateway.settings.LLM_DEFAULT_MODEL
