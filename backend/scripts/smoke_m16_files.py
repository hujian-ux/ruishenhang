"""M16 手工冒烟：上传 xlsx → 按技能包执行 → 下载产出物。

用法：cd backend && PG_PORT=1 .venv/Scripts/python.exe scripts/smoke_m16_files.py
"""
import io
import sys

sys.path.insert(0, ".")

from fastapi.testclient import TestClient
from openpyxl import Workbook, load_workbook

from app.core.security import create_dev_token
from app.main import app
from app.services import agent_store

# 1. 造一份技能包（经营复盘），已发布
agent = {
    "agent_id": "agt_smoke_review",
    "name": "经营复盘",
    "adapter_type": "llm",
    "description": "按月做经营复盘：量价分离、异常归因、行动项",
    "knowledge_scope": "department",
    "status": "published",
    "category": "industry", "category_cn": "制造业", "sub_function": "",
    "system_prompt": (
        "你是经营复盘专家。严格按以下流程执行：\n"
        "1) 量价分离：把收入/费用变化拆成「量」与「价」两个因子；\n"
        "2) 异常归因：同比绝对值 >30% 的科目逐条归因；\n"
        "3) 产出：必须输出一张明细表（列名与输入一致）+\n"
        "   一张汇总表（科目/影响金额/责任部门/行动项）。\n"
        "缺失数据列清单，不得臆造。"
    ),
}
agent_store.save(agent)

# 2. 造输入台账
wb = Workbook()
ws = wb.active
ws.title = "6月费用"
ws.append(["2026年6月费用台账"])
ws.append(["部门", "科目", "本月金额", "上月金额", "同比"])
for r in [["销售部", "差旅", 12000, 10400, 0.154],
          ["销售部", "招待", 8000, 11500, -0.304],
          ["生产部", "耗材", 23000, 16200, 0.420],
          ["生产部", "维修", 5000, 4500, 0.111]]:
    ws.append(r)
buf = io.BytesIO()
wb.save(buf)
raw = buf.getvalue()
print(f"[0] 输入台账 {len(raw)} bytes")

c = TestClient(app)
EMP = {"Authorization": create_dev_token("u_zhang", "d_sales", "employee")}
ADM = {"Authorization": create_dev_token("p_admin", "d_platform", "platform_admin")}

# 3. 上传
r = c.post("/api/v1/agent/files", headers=EMP,
           files={"file": ("费用台账.xlsx", raw,
                           "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
print("[1] upload ->", r.status_code)
d = r.json()["data"]
fid = d["file_id"]
print("    file_id:", fid, "| size:", d["size"], "| store:", d["meta"].get("store"))
pv = d["preview"]
print("    preview ok:", pv["ok"], "| sheets:", pv["sheet_count"],
      "| headers:", pv["sheets"][0]["headers"], "| rows:", pv["sheets"][0]["rows"])

# 4. 执行（带附件）
r = c.post("/api/v1/agent/invoke", headers=EMP,
           json={"agent_id": "agt_smoke_review", "input": "请做 6 月费用复盘",
                 "file_ids": [fid]})
print("[2] invoke ->", r.status_code)
d = r.json()["data"]
print("    skill_applied:", d["agent"]["skill_applied"], "| run_id:", d["run_id"])
print("    files_used:", d["files_used"])
print("    tool_calls:", [t["tool"] for t in d["tool_calls"]])
print("    answer[:100]:", d["answer"][:100].replace("\n", " "))
arts = d["artifacts"]
print("    artifacts:", [(a["filename"], a["size"]) for a in arts])

# 5. 下载产出物
if arts:
    aid = arts[0]["file_id"]
    r = c.get(f"/api/v1/agent/files/{aid}", headers=EMP)
    print("[3] download ->", r.status_code, "| bytes:", len(r.content),
          "| ctype:", r.headers.get("content-type"))
    wb2 = load_workbook(io.BytesIO(r.content))
    print("    sheets:", wb2.sheetnames)
    for n in wb2.sheetnames[:2]:
        print(f"    「{n}」{wb2[n].max_row} 行 × {wb2[n].max_column} 列")

# 6. 越权：别的部门员工拿不到
OTHER = {"Authorization": create_dev_token("u_li", "d_other", "employee")}
print("[4] 越权下载 ->", c.get(f"/api/v1/agent/files/{fid}", headers=OTHER).status_code)
print("[5] 列表 ->", c.get("/api/v1/agent/files?kind=artifact", headers=EMP).json()["data"]["count"],
      "个产出物")
print("[6] stats ->", c.get("/api/v1/agent/files/stats", headers=ADM).json()["data"])
print("RESULT:", "PASS" if arts and r is not None else "FAIL")
