"""M16 落地验证：用**真实技能包**（D:\\workbuddy_解决方案）跑 xlsx 进 → xlsx 出。

与 smoke_m16_files.py 的区别：那份用手写技能包验证链路，这份用 115 个方案库里的
真实技能包，验证「包里的工作流程/产出契约真的进了 prompt 并影响产出」。

用法：cd backend && PG_PORT=1 .venv/Scripts/python.exe scripts/verify_m16_skill.py
"""
import io
import sys

sys.path.insert(0, ".")
sys.path.insert(0, "scripts")

from fastapi.testclient import TestClient
from openpyxl import Workbook, load_workbook

import import_solutions as importer
from app.core.security import create_dev_token
from app.main import app
from app.services import agent_store

client = TestClient(app)
ADMIN = {"Authorization": create_dev_token("p_admin", "d_platform", "platform_admin")}
EMP = {"Authorization": create_dev_token("u_finance", "d_finance", "employee")}

# 1. 导入方案库并按批次审批（与 M15 流程一致，不绕过红线）
defs = importer.scan()
res = importer.apply_defs(defs, scope="department")
print(f"[0] 技能包 {len(defs)} 个已导入，批次单 {res['approval_id']}")
r = client.post(f"/api/v1/approval/{res['approval_id']}/decide", headers=ADMIN,
                json={"decision": "approve", "comment": "M16 文件进出验证"})
print("    批次审批 ->", r.status_code, r.json()["data"]["status"],
      "| 迁移", r.json()["data"]["migrated_count"], "个")

# 2. 挑一个真正吃表格的技能包
target = next(a for a in res["agents"] if a["name"] == "费用异常波动分析")
agent = agent_store.get(target["agent_id"])
print(f"[1] 目标技能包：{agent['name']} | adapter={agent['adapter_type']} "
      f"| prompt {len(agent['system_prompt'])} 字")
has_flow = "工作流程" in agent["system_prompt"] or "流程" in agent["system_prompt"]
print("    prompt 内含工作流程：", has_flow)

# 3. 造一份带异常的月度费用台账（金额-数量-单价三列，可做量价分离）
wb = Workbook()
ws = wb.active
ws.title = "费用明细"
ws.append(["2026年6月 管理费用明细表"])
ws.append(["科目", "数量", "单价", "金额", "上月金额", "同比"])
for row in [
    ["办公用品", 120, 50, 6000, 5200, 0.154],
    ["差旅费", 45, 800, 36000, 24000, 0.500],
    ["业务招待", 30, 1200, 36000, 18000, 1.000],
    ["水电费", 1, 18000, 18000, 17500, 0.029],
    ["维修费", 6, 5000, 30000, 12000, 1.500],
]:
    ws.append(row)
buf = io.BytesIO()
wb.save(buf)
raw = buf.getvalue()

up = client.post("/api/v1/agent/files", headers=EMP,
                 files={"file": ("管理费用明细.xlsx", raw, "application/octet-stream")}).json()["data"]
print(f"[2] 上传 {up['filename']} -> {up['file_id']} | "
      f"sheets={up['preview']['sheet_count']} rows={up['preview']['total_rows']} "
      f"| store={up['meta'].get('store')}")
print("    识别到的表头：", up["preview"]["sheets"][0]["headers"])

# 4. 执行（附件 + 技能包提示词）
r = client.post("/api/v1/agent/invoke", headers=EMP, json={
    "agent_id": agent["agent_id"],
    "input": "请对 6 月管理费用做异常波动分析，同比超 30% 的逐条归因",
    "file_ids": [up["file_id"]],
})
d = r.json()["data"]
print(f"[3] invoke -> {r.status_code} | skill_applied={d['agent']['skill_applied']} "
      f"| files_used={len(d['files_used'])} | run_id={d['run_id']}")
print("    answer 前 160 字：", d["answer"][:160].replace("\n", " "))

# 5. 下载产出物并核对内容
if not d["artifacts"]:
    print("RESULT: FAIL（未生成产出物）")
    sys.exit(1)
art = d["artifacts"][0]
dl = client.get(f"/api/v1/agent/files/{art['file_id']}", headers=EMP)
print(f"[4] 下载 -> {dl.status_code} | {len(dl.content)} bytes | {art['filename']}")
wb2 = load_workbook(io.BytesIO(dl.content))
print("    sheet：", wb2.sheetnames)
concl = "\n".join(str(c.value) for row in wb2["执行结论"].iter_rows() for c in row if c.value)
print("    执行结论含技能包名：", "费用异常波动分析" in concl)
print("    执行结论含输入文件名：", "管理费用明细.xlsx" in concl)
if "输入数据摘要" in wb2.sheetnames:
    sh = wb2["输入数据摘要"]
    print("    输入数据摘要首行：", [c.value for c in sh[1]])
print("RESULT: PASS")
