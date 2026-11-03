"""M16 · 文件进出测试（P0：xlsx 上传 → 按技能包执行 → 产出物下载）。

覆盖：
1. 表格解析的「真」行为：标题行干扰下仍能认出表头（真实台账第一行常是标题）
2. 解析失败降级不抛异常（上传损坏文件不该让 invoke 500）
3. 上传→invoke→产出物→下载 全链路，产出物是合法 xlsx 且带「执行结论」
4. 越权隔离：别的部门既下不到输入件，也不能拿别人的 file_id 去执行
5. 回归护栏：持久化表白名单必须包含 agents / agent_files
   （M15 曾建了 agents 表却漏加白名单，写穿 PG 静默失效）
"""
import io

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook, load_workbook

from app.core.security import create_dev_token
from app.main import app
from app.services import agent_store, artifact, file_store, pg_store, tabular

client = TestClient(app)

ADMIN = {"Authorization": create_dev_token("p_admin", "d_platform", "platform_admin")}
EMP = {"Authorization": create_dev_token("u_zhang", "d_sales", "employee")}
OTHER = {"Authorization": create_dev_token("u_li", "d_other", "employee")}

SKILL_PROMPT = ("你是「经营复盘」执行专家。\n## 工作流程\n1. 量价分离 2. 异常归因\n"
                "## 硬规则\n主指标达标但支撑指标反向必须改判存疑。")


def _xlsx(rows, title="6月费用", with_title_row=True) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = title
    if with_title_row:
        ws.append([f"2026年{title}"])          # 真实台账常见的标题行（干扰项）
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


LEDGER = [
    ["部门", "科目", "本月金额", "上月金额", "同比"],
    ["销售部", "差旅", 12000, 10400, 0.154],
    ["销售部", "招待", 8000, 11500, -0.304],
    ["生产部", "耗材", 23000, 16200, 0.420],
]


@pytest.fixture
def published_skill():
    """一个已发布的技能包（产出契约要求出表）。"""
    agent = agent_store.save({
        "agent_id": "agt_m16_review",
        "name": "经营复盘",
        "adapter_type": "llm",
        "description": "月度经营复盘",
        "knowledge_scope": "department",
        "status": "published",
        "category": "industry", "category_cn": "制造业", "sub_function": "",
        "system_prompt": SKILL_PROMPT,
    })
    yield agent
    agent_store._AGENTS.pop("agt_m16_review", None)


@pytest.fixture
def clean_files():
    saved = dict(file_store._FILES)
    file_store._FILES.clear()
    yield
    file_store._FILES.clear()
    file_store._FILES.update(saved)


def _upload(headers=EMP, name="费用台账.xlsx", data: bytes | None = None):
    return client.post("/api/v1/agent/files", headers=headers,
                       files={"file": (name, data if data is not None else _xlsx(LEDGER),
                                       "application/octet-stream")})


# ── 1. 解析 ──────────────────────────────────────────────────────────────────
def test_header_detection_skips_title_row():
    """第一行是「2026年6月费用」这种标题时，表头不能被识别成标题行。"""
    parsed = tabular.parse(_xlsx(LEDGER), "费用台账.xlsx")
    assert parsed["ok"] is True
    sh = parsed["sheets"][0]
    assert sh["headers"] == ["部门", "科目", "本月金额", "上月金额", "同比"]
    assert sh["rows"] == 3
    cols = {c["name"]: c for c in sh["columns"]}
    assert cols["本月金额"]["type"] == "number"
    assert cols["部门"]["type"] == "text"


def test_prompt_context_carries_profile_and_sample():
    ctx = tabular.to_prompt_context(tabular.parse(_xlsx(LEDGER), "费用台账.xlsx"))
    assert "费用台账.xlsx" in ctx and "3 行" in ctx
    assert "列画像" in ctx and "本月金额(number)" in ctx
    assert "生产部" in ctx          # 样本行带真实数据，模型才有东西可算


def test_parse_failure_degrades_without_raising():
    """损坏文件：不抛异常，返回 ok=False + 可读错误 + 给模型的降级提示。"""
    parsed = tabular.parse(b"not a real xlsx at all", "坏文件.xlsx")
    assert parsed["ok"] is False
    assert "解析失败" in parsed["error"] or "解析" in parsed["error"]
    ctx = tabular.to_prompt_context(parsed)
    assert "附件不可用" in ctx and "不要臆造" in ctx


def test_unsupported_ext_rejected():
    r = _upload(name="程序.exe", data=b"MZ")
    assert r.status_code == 415


def test_empty_file_rejected():
    r = _upload(name="空.xlsx", data=b"")
    assert r.status_code == 400


# ── 2. 上传 ──────────────────────────────────────────────────────────────────
def test_upload_returns_preview_and_meta(clean_files):
    r = _upload()
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["kind"] == "upload" and d["filename"] == "费用台账.xlsx"
    assert d["preview"]["ok"] is True
    assert d["preview"]["sheets"][0]["rows"] == 3
    assert d["meta"]["parse_ok"] is True
    assert d["download_url"].endswith(d["file_id"])


def test_upload_same_file_reuses_record(clean_files):
    """同一用户重复上传同一份文件 → 复用记录，不在对象存储堆副本。"""
    a = _upload().json()["data"]["file_id"]
    b = _upload().json()["data"]["file_id"]
    assert a == b


# ── 3. 全链路 ────────────────────────────────────────────────────────────────
def test_invoke_with_file_returns_artifact(clean_files, published_skill):
    fid = _upload().json()["data"]["file_id"]
    r = client.post("/api/v1/agent/invoke", headers=EMP,
                    json={"agent_id": "agt_m16_review", "input": "做 6 月费用复盘",
                          "file_ids": [fid]})
    assert r.status_code == 200
    d = r.json()["data"]

    assert d["files_used"][0]["file_id"] == fid
    assert d["files_used"][0]["parse_ok"] is True
    assert d["files_used"][0]["rows"] == 3
    assert "file_parse" in [t["tool"] for t in d["tool_calls"]]
    assert d["agent"]["skill_applied"] is True

    arts = d["artifacts"]
    assert len(arts) == 1 and arts[0]["kind"] == "artifact"
    assert arts[0]["filename"].endswith(".xlsx")

    dl = client.get(f"/api/v1/agent/files/{arts[0]['file_id']}", headers=EMP)
    assert dl.status_code == 200
    assert "spreadsheetml" in dl.headers["content-type"]
    wb = load_workbook(io.BytesIO(dl.content))
    assert "执行结论" in wb.sheetnames
    assert "输入数据摘要" in wb.sheetnames     # 可溯源：附件行列与列名
    # 执行结论页要能看出这是哪个技能包、吃了哪些输入
    text = "\n".join(str(c.value) for row in wb["执行结论"].iter_rows() for c in row if c.value)
    assert "经营复盘" in text and "费用台账.xlsx" in text


def test_skill_agent_gets_artifact_even_without_files(clean_files, published_skill):
    """技能包的产出契约就是文件：没附件也要给产出物，否则下载按钮时有时无。"""
    r = client.post("/api/v1/agent/invoke", headers=EMP,
                    json={"agent_id": "agt_m16_review", "input": "空跑一次"})
    d = r.json()["data"]
    assert len(d["artifacts"]) == 1
    assert d["files_used"] == []


def test_plain_assistant_without_files_has_no_artifact(clean_files):
    """平台默认助手纯闲聊不生成产出物，避免存储里堆满小文件。"""
    from app.core.security import create_dev_token as _t
    h = {"Authorization": _t("p_admin", "d_platform", "platform_admin")}
    agent_store.save({"agent_id": "agt_m16_plain", "name": "默认助手",
                      "adapter_type": "llm", "description": "x",
                      "knowledge_scope": "department", "status": "published"})
    try:
        r = client.post("/api/v1/agent/invoke", headers=h,
                        json={"agent_id": "agt_m16_plain", "input": "你好"})
        assert r.json()["data"]["artifacts"] == []
    finally:
        agent_store._AGENTS.pop("agt_m16_plain", None)


# ── 4. 越权 ──────────────────────────────────────────────────────────────────
def test_cross_dept_cannot_download(clean_files):
    fid = _upload().json()["data"]["file_id"]
    r = client.get(f"/api/v1/agent/files/{fid}", headers=OTHER)
    assert r.status_code == 404          # 不区分「不存在」与「无权」，防枚举


def test_cannot_invoke_with_foreign_file(clean_files, published_skill):
    fid = _upload().json()["data"]["file_id"]
    r = client.post("/api/v1/agent/invoke", headers=OTHER,
                    json={"agent_id": "agt_m16_review", "input": "x", "file_ids": [fid]})
    assert r.status_code == 404


def test_missing_file_id_is_404(clean_files, published_skill):
    r = client.post("/api/v1/agent/invoke", headers=EMP,
                    json={"agent_id": "agt_m16_review", "input": "x",
                          "file_ids": ["file_nope"]})
    assert r.status_code == 404


# ── 5. 产出物生成器（不依赖模型输出）──────────────────────────────────────────
def test_extract_markdown_tables_multi_sheet():
    answer = ("## 一、量价分离\n结论如下。\n\n"
              "| 科目 | 影响金额 | 归因 |\n|---|---|---|\n| 耗材 | +6800 | 单价上涨 |\n\n"
              "## 二、行动项\n"
              "| 责任部门 | 行动 |\n|---|---|\n| 生产部 | 重新议价 |\n")
    data = artifact.build_xlsx(answer, {"agent_name": "经营复盘"})
    wb = load_workbook(io.BytesIO(data))
    assert "执行结论" in wb.sheetnames
    # 表前的小标题被用作 sheet 名（技能包习惯用「一、量价分离」这种标题）
    assert "量价分离" in wb.sheetnames
    assert "行动项" in wb.sheetnames
    assert wb["量价分离"].max_row == 2


def test_extract_json_array_as_sheet():
    answer = '结论：\n```json\n[{"线别":"A","得分":88},{"线别":"B","得分":73}]\n```'
    data = artifact.build_xlsx(answer, {"agent_name": "复盘"})
    wb = load_workbook(io.BytesIO(data))
    assert "产出表1" in wb.sheetnames
    assert wb["产出表1"]["A1"].value == "线别"


def test_artifact_always_has_content_sheet():
    """模型只说了两句话也要给出可用文件：执行结论页兜住答案全文。"""
    data = artifact.build_xlsx("本期无异常。", {"agent_name": "复盘"})
    wb = load_workbook(io.BytesIO(data))
    assert wb.sheetnames == ["执行结论"]
    text = "\n".join(str(c.value) for row in wb["执行结论"].iter_rows() for c in row if c.value)
    assert "本期无异常" in text


# ── 6. 回归护栏 ──────────────────────────────────────────────────────────────
def test_persist_tables_registered_in_whitelist():
    """建表 + 调 upsert 但漏加白名单 = 写穿静默失效（M15 的 agents 就是这样）。"""
    for table in ("agents", "agent_files"):
        assert table in pg_store._KEY_COL, f"{table} 未加入 pg_store._KEY_COL，写穿会静默失败"
