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


def test_resolve_solution_dir_walks_up_to_xlsx_ancestor():
    """166 交付物匹配修复：agent 的 source_path 可能比 xlsx 深多级（板块04 嵌套两层），
    必须向上回退到最近的含 xlsx 祖先，否则 115 个业务包绝大多数匹配不到（原精确相等只命中 9 个）。"""
    from app.routers import ops
    by_dir = {"01_制造业/方案A": {"p": 1}, "00_办公/模板": {"p": 2}}
    # 板块04：skill.md 在 xlsx 上级再深两级
    assert ops._resolve_solution_dir("01_制造业/方案A/子/孙/skill.md", by_dir) == "01_制造业/方案A"
    # 直接命中
    assert ops._resolve_solution_dir("00_办公/模板", by_dir) == "00_办公/模板"
    # 反斜杠也能归一
    assert ops._resolve_solution_dir("01_制造业\\方案A\\skill.md", by_dir) == "01_制造业/方案A"
    # 找不到返回空（后续跳过，不挂）
    assert ops._resolve_solution_dir("99_不存在/xx", by_dir) == ""


def test_scan_pinyin_pairing(tmp_path):
    """M17 P2 · D 盘样例文件名是拼音（如 ying-shou-zhang-kuan-zhang-ling-fen-xi-biao），
    中文关键词命中不到，必须用拼音标记层识别输入/交付物。"""
    d = tmp_path / "04_角色" / "财务" / "应收账款账龄分析"
    d.mkdir(parents=True)
    _write_xlsx(d / "ying-shou-zhang-kuan-ming-xi-zhang.xlsx", ["客户", "余额"], [["A", 50]])
    _write_xlsx(d / "ying-shou-zhang-kuan-zhang-ling-fen-xi-biao.xlsx", ["账龄", "金额"], [["90+", 80]])
    pairs = fewshot.scan_solution_pairs(str(tmp_path))
    assert len(pairs) == 1
    p = pairs[0]
    assert "ming-xi" in p["input_name"] and "fen-xi-biao" in p["output_name"]


def test_scan_multi_raw_one_deliverable(tmp_path):
    """多份原始数据 + 一份交付物（财务包常见）：识别到唯一交付物即配对，取首个非交付物作输入样例。"""
    d = tmp_path / "04_角色" / "财务" / "报销单合规预审"
    d.mkdir(parents=True)
    _write_xlsx(d / "fei-yong-bao-xiao-dan.xlsx", ["a"], [[1]])            # 原始
    _write_xlsx(d / "fa-piao-yang-ben.xlsx", ["b"], [[2]])                 # 原始
    _write_xlsx(d / "bao-xiao-he-gui-yu-shen-qing-dan.xlsx", ["c"], [[3]])  # 交付物（yu-shen）
    pairs = fewshot.scan_solution_pairs(str(tmp_path))
    assert len(pairs) == 1
    p = pairs[0]
    assert p["output_name"] == "bao-xiao-he-gui-yu-shen-qing-dan.xlsx"
    assert p["input_name"] in ("fei-yong-bao-xiao-dan.xlsx", "fa-piao-yang-ben.xlsx")


def test_fewshot_root_precedence(monkeypatch):
    """build-fewshot 扫描根的优先级：环境变量 > D 盘默认 > 仓库回退。"""
    from app.routers import ops
    monkeypatch.setattr(ops.settings, "SOLUTIONS_ROOT", "")
    monkeypatch.delenv("SOLUTIONS_ROOT", raising=False)
    # D 盘不存在 → 回退仓库 solutions
    monkeypatch.setattr(os.path, "isdir", lambda p: False)
    assert ops._fewshot_root().endswith("solutions")
    # D 盘存在 → 用 D 盘
    monkeypatch.setattr(os.path, "isdir", lambda p: p == "D:/workbuddy_解决方案")
    assert ops._fewshot_root() == "D:/workbuddy_解决方案"
    # 环境变量最高优先
    monkeypatch.setenv("SOLUTIONS_ROOT", "/custom/root")
    assert ops._fewshot_root() == "/custom/root"
    monkeypatch.delenv("SOLUTIONS_ROOT", raising=False)


