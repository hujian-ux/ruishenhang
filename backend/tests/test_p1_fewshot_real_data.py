"""P1 · few-shot 真实数据接入集成测试（不依赖运行中的后端）。

验证「把真实 xlsx 方案库配对 → 挂到对应 agent」的端到端链路：
  - scan_solution_pairs 能识别 corpus_inbox/fewshot_solutions 下的真实验收 xlsx；
  - ops._resolve_solution_dir（生产匹配逻辑）能把 agent 的 solution_dir 向上回退命中语料目录；
  - build_fewshot_block 解析的是**真实表头**（非合成），输入/输出方向正确；
  - compose_with_fewshot 在 FEWSHOT_ENABLED=1 时把样例注入 system_prompt，关闭时零行为变更。

真实语料来源：docs/验收/M17_真模型/ 下两个 M17 财务验收方案（报销单合规预审、费用异常波动分析）
各自的「原始数据 ↔ 交付物」成对 xlsx，已复制到 corpus_inbox/fewshot_solutions/04_角色/财务/<方案>/。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import openpyxl
import pytest

from app.core.config import settings
from app.routers import ops  # 复用生产的 _resolve_solution_dir
from app.services import fewshot

REPO = Path(__file__).resolve().parents[2]
CORPUS = REPO / "corpus_inbox" / "fewshot_solutions"
DEFS = REPO / "backend" / "reports" / "defs.json"

TARGETS = {"报销单合规预审", "费用异常波动分析"}


def _deliverable_first_header(xlsx_path: str) -> str:
    wb = openpyxl.load_workbook(xlsx_path, read_only=True, data_only=True)
    ws = wb.active
    for row in ws.iter_rows(values_only=True):
        if row and any(c is not None for c in row):
            return str(row[0])
    return ""


def test_real_corpus_pairs_and_orientation():
    """scan 能识别真实 xlsx，且输入=原始数据、输出=交付物，方向正确。"""
    assert CORPUS.exists(), f"真实语料目录缺失: {CORPUS}"
    pairs = fewshot.scan_solution_pairs(str(CORPUS))
    by_dir = {p["solution_dir"]: p for p in pairs}
    assert len(pairs) == 2, f"应配对 2 个方案，实际 {len(pairs)}: {list(by_dir)}"
    for sol_dir, p in by_dir.items():
        assert p["input_path"], f"{sol_dir} 缺少输入样例"
        assert "原始数据" in os.path.basename(p["input_path"]), \
            f"{sol_dir} 输入方向错: {p['input_path']}"
        assert "交付物" in os.path.basename(p["output_path"]), \
            f"{sol_dir} 输出方向错: {p['output_path']}"


def test_real_data_attaches_to_defs_agents():
    """生产匹配逻辑把真实样例挂到 defs.json 中的两个财务 agent。"""
    defs = json.loads(DEFS.read_text(encoding="utf-8"))
    agents = [a for a in defs if a.get("name") in TARGETS]
    assert len(agents) == 2, f"defs.json 应含 2 个目标方案，实际 {len(agents)}"

    pairs = fewshot.scan_solution_pairs(str(CORPUS))
    by_dir = {p["solution_dir"]: p for p in pairs}

    attached = 0
    for a in agents:
        key = ops._resolve_solution_dir(a.get("solution_dir"), by_dir)
        p = by_dir.get(key)
        assert p, f"agent {a['name']} 未匹配到语料，key={key!r}"
        block = fewshot.build_fewshot_block(p["input_path"], p["output_path"])
        fewshot.attach_fewshot(a, block)
        # 块里必须含真实交付物表头（证明解析的是真 xlsx，不是占位）
        hdr = _deliverable_first_header(p["output_path"])
        assert hdr and hdr in block, \
            f"{a['name']} 样例未含真实交付物表头 {hdr!r}（块长度 {len(block)}）"
        assert a["fewshot_version"], f"{a['name']} 未写入版本号"
        attached += 1
    assert attached == 2


def test_compose_injects_only_when_enabled():
    """FEWSHOT_ENABLED=1 注入样例；关闭时原样返回（零行为变更）。"""
    block = "【交付物样例】\n表头：A｜B\n样本：1｜2"
    agent = {"fewshot": block, "fewshot_version": "abc123"}
    sp = "你是专家。"

    prev = settings.FEWSHOT_ENABLED
    try:
        settings.FEWSHOT_ENABLED = False
        assert fewshot.compose_with_fewshot(sp, agent) == sp, "关闭时不应注入"
        settings.FEWSHOT_ENABLED = True
        out = fewshot.compose_with_fewshot(sp, agent)
        assert "产出物样例" in out and block in out, "开启时应注入样例块"
    finally:
        settings.FEWSHOT_ENABLED = prev
