"""M28 · 语料投递包：原始 Word/Excel/PDF → 纯文本 的转换契约。

覆盖小白最容易踩的几种情况：老格式给出可操作提示、占位文件不进语料、
干跑不写盘、部门目录结构保留、单篇失败不拖垮整批。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import convert_corpus as cc  # noqa: E402


@pytest.fixture()
def dirs(tmp_path):
    src, out = tmp_path / "raw", tmp_path / "converted"
    (src / "dept_finance").mkdir(parents=True)
    (src / "global").mkdir(parents=True)
    (src / "dept_finance" / cc.PLACEHOLDER).write_text("占位", encoding="utf-8")
    cc.REPORT = tmp_path / "reports" / "corpus_convert.json"
    return src, out


def test_docx_paragraphs_and_tables(dirs):
    from docx import Document

    src, out = dirs
    p = src / "dept_finance" / "报销制度.docx"
    doc = Document()
    doc.add_paragraph("第一章 差旅住宿标准")
    doc.add_paragraph("部门经理每晚上限 500 元。")
    t = doc.add_table(rows=2, cols=2)
    t.cell(0, 0).text = "岗位"
    t.cell(0, 1).text = "限额"
    t.cell(1, 0).text = "部门经理"
    t.cell(1, 1).text = "500"
    doc.save(str(p))

    rep = cc.run(src, out)
    assert len(rep["converted"]) == 1 and not rep["failed"]
    text = (out / "dept_finance" / "报销制度.txt").read_text(encoding="utf-8")
    assert "差旅住宿标准" in text and "500" in text and "[表格 1]" in text


def test_xlsx_multiple_sheets(dirs):
    from openpyxl import Workbook

    src, out = dirs
    p = src / "global" / "科目表.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "2026"
    ws.append(["科目", "口径"])
    ws.append(["差旅费", "按实际发生"])
    wb.create_sheet("2025").append(["旧科目", "已废止"])
    wb.save(str(p))

    rep = cc.run(src, out)
    assert len(rep["converted"]) == 1 and not rep["failed"]
    text = (out / "global" / "科目表.txt").read_text(encoding="utf-8")
    assert "[工作表 2026]" in text and "[工作表 2025]" in text
    assert "按实际发生" in text


def test_placeholder_is_skipped(dirs):
    src, out = dirs
    rep = cc.run(src, out)
    assert rep["total"] == 0
    assert not rep["converted"]


def test_legacy_format_gives_actionable_hint(dirs):
    src, out = dirs
    (src / "global" / "旧制度.doc").write_bytes(b"fake")
    rep = cc.run(src, out)
    assert len(rep["failed"]) == 1
    assert "另存为 .docx" in rep["failed"][0]["reason"]


def test_gbk_text_is_decoded_and_structure_kept(dirs):
    src, out = dirs
    (src / "global" / "考勤.txt").write_bytes("迟到扣款规定".encode("gbk"))
    rep = cc.run(src, out)
    assert len(rep["converted"]) == 1
    assert "迟到扣款规定" in (out / "global" / "考勤.txt").read_text(encoding="utf-8")


def test_dry_run_writes_nothing(dirs):
    src, out = dirs
    (src / "global" / "手册.md").write_text("# 员工手册\n内容", encoding="utf-8")
    rep = cc.run(src, out, dry_run=True)
    assert len(rep["converted"]) == 1 and rep["converted"][0]["dry"] is True
    assert not out.exists() or not list(out.rglob("*.txt"))


def test_existing_txt_skipped_unless_force(dirs):
    src, out = dirs
    (src / "global" / "制度.md").write_text("新版内容", encoding="utf-8")
    (out / "global").mkdir(parents=True)
    (out / "global" / "制度.txt").write_text("旧版内容", encoding="utf-8")

    rep = cc.run(src, out)
    assert len(rep["skipped"]) == 1 and not rep["converted"]
    assert (out / "global" / "制度.txt").read_text(encoding="utf-8") == "旧版内容"

    rep = cc.run(src, out, force=True)
    assert len(rep["converted"]) == 1
    assert (out / "global" / "制度.txt").read_text(encoding="utf-8") == "新版内容"


def test_one_bad_file_does_not_break_the_batch(dirs):
    src, out = dirs
    (src / "global" / "好的.md").write_text("# 正常文档\n可用内容", encoding="utf-8")
    (src / "global" / "坏的.doc").write_bytes(b"fake")
    rep = cc.run(src, out)
    assert len(rep["converted"]) == 1 and len(rep["failed"]) == 1
    assert (out / "global" / "好的.txt").exists()


def test_init_creates_standard_directories(tmp_path):
    src, out = tmp_path / "raw", tmp_path / "converted"
    made = cc.init_dirs(src, out)
    assert len(made) == len(cc.DEPT_DIRS)
    for name in cc.DEPT_DIRS:
        assert (src / name).is_dir()
        assert (src / name / cc.PLACEHOLDER).exists()
    assert out.is_dir()
