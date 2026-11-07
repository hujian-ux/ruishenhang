"""M15/M17 P1 · 166 xlsx 交付物转 few-shot（机制 + 幂等 + opt-in）。"""
import os

import pytest

from app.core.config import settings
from app.services import fewshot


def _write_xlsx(path, headers, rows):
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.append(headers)
    for r in rows:
        ws.append(r)
    wb.save(path)


def test_scan_pairs_and_build_block(tmp_path):
    d = tmp_path / "01_制造业" / "方案A"
    d.mkdir(parents=True)
    _write_xlsx(d / "原始数据.xlsx", ["月份", "销售额"], [["1月", 100], ["2月", 200]])
    _write_xlsx(d / "交付物_分析.xlsx", ["月份", "环比"], [["1月", "-"], ["2月", "100%"]])

    pairs = fewshot.scan_solution_pairs(str(tmp_path))
    assert len(pairs) == 1
    p = pairs[0]
    assert p["solution_dir"] == "01_制造业/方案A"
    assert "原始数据" in p["input_name"] and "交付物" in p["output_name"]

    block = fewshot.build_fewshot_block(p["input_path"], p["output_path"])
    assert "【输入样例】" in block and "【交付物样例】" in block
    assert "表头：" in block and "样本：" in block
    assert "##" not in block  # 强制剔除，避免渲染误切页


def test_scan_skips_ambiguous_three_files(tmp_path):
    d = tmp_path / "dir"
    d.mkdir()
    for n in ("a.xlsx", "b.xlsx", "c.xlsx"):
        _write_xlsx(d / n, ["x"], [[1]])
    assert fewshot.scan_solution_pairs(str(tmp_path)) == []


def test_scan_single_file_as_output_only(tmp_path):
    d = tmp_path / "方案B"
    d.mkdir()
    _write_xlsx(d / "交付物.xlsx", ["列1", "列2"], [["a", 1]])
    pairs = fewshot.scan_solution_pairs(str(tmp_path))
    assert len(pairs) == 1
    p = pairs[0]
    assert p["input_path"] is None and p["output_name"] == "交付物.xlsx"
    block = fewshot.build_fewshot_block(p["input_path"], p["output_path"])
    assert "【交付物样例】" in block and "无原始数据样例" in block


def test_attach_and_compose_opt_in():
    block = "【输入样例】\n表头：月份｜销售额\n样本：1月｜100"
    agent = {"agent_id": "ag1", "system_prompt": "你是专家。"}
    fewshot.attach_fewshot(agent, block)
    assert agent["fewshot"] == block
    assert agent["fewshot_version"]
    v1 = agent["fewshot_version"]
    fewshot.attach_fewshot(agent, block)  # 同内容再挂，版本不变
    assert agent["fewshot_version"] == v1

    # opt-in：默认关 → 不变
    settings.FEWSHOT_ENABLED = False
    assert fewshot.compose_with_fewshot("你是专家。", agent) == "你是专家。"
    # 开启 → 注入样例块
    settings.FEWSHOT_ENABLED = True
    out = fewshot.compose_with_fewshot("你是专家。", agent)
    assert "产出物样例" in out and block in out
    settings.FEWSHOT_ENABLED = False  # 复原
