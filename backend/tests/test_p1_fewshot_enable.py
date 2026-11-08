"""P1 · 166 个 xlsx 交付物 few-shot 启用验证（不依赖真实 166 文件）。

用合成的方案库（含输入/输出 xlsx 成对）证明：SOLUTIONS_ROOT 指向方案库后，
POST /ops/build-fewshot 能扫描到配对并把样例块挂到对应业务包（agent.fewshot）。
真实启用只需把 SOLUTIONS_ROOT 指向 D:/workbuddy_解决方案（166 个目录）。
"""
import os

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from app.core import security
from app.main import app
from app.services import agent_store


def _write_xlsx(path, headers, rows):
    wb = Workbook()
    ws = wb.active
    ws.append(headers)
    for r in rows:
        ws.append(r)
    wb.save(path)


def _admin_token():
    return security.create_dev_token("u_admin", "d_it", "platform_admin", "t_demo")


def test_build_fewshot_attaches(tmp_path, monkeypatch):
    sol = tmp_path / "solA"
    sol.mkdir()
    _write_xlsx(str(sol / "原始数据.xlsx"), ["日期", "金额"],
                [["2026-01-01", 100], ["2026-01-02", 200]])
    _write_xlsx(str(sol / "交付物.xlsx"), ["月份", "合计"],
                [["1月", 300], ["2月", 400]])

    monkeypatch.setenv("SOLUTIONS_ROOT", str(tmp_path))

    key = "ag_fewshot_demo"
    agent_store._AGENTS[key] = {
        "agent_id": key, "name": "演示办公技能", "category": "office", "category_cn": "办公",
        "builtin": False, "public": False, "system_prompt": "你是办公专家。",
        "source_path": "solA",   # 与 scan_solution_pairs 的相对键一致
    }
    try:
        c = TestClient(app)
        r = c.post("/api/v1/ops/build-fewshot", headers={"Authorization": _admin_token()})
        assert r.status_code == 200
        d = r.json()["data"]
        assert d["pairs_found"] >= 1
        assert d["attached"] >= 1
        a = agent_store._AGENTS[key]
        assert a.get("fewshot") and "交付物样例" in a["fewshot"]
        assert a.get("fewshot_version")
    finally:
        agent_store._AGENTS.pop(key, None)
        monkeypatch.delenv("SOLUTIONS_ROOT", raising=False)
