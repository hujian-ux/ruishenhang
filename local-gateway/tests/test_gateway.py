"""本地文件网关 · 离线单测（注入假平台客户端，零网络）。

覆盖：扫描上传、去重防重传、exclude 过滤、include 过滤、变更重传、状态持久化。
"""
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gateway import Config, Gateway  # noqa: E402


class FakeClient:
    """记录上传调用，模拟 200 成功。"""
    def __init__(self):
        self.calls = []          # [(name, size)]
        self.token = "fake-token"

    def upload_file(self, path, title=None, endpoint="/agent/files"):
        name = title or os.path.basename(path)
        size = os.path.getsize(path)
        self.calls.append((name, size))
        return {"status": 200, "json": {"code": 0, "data": {"file_id": "f1"}}}


def _write(p, content="hello"):
    Path(p).write_text(content, encoding="utf-8")


def _cfg(tmp_path, **kw):
    base = dict(backend_url="http://x", watch_dirs=[str(tmp_path / "wd")],
                state_file=str(tmp_path / ".gw_state.json"))
    base.update(kw)
    return Config(**base)


def test_scan_uploads_new_files(tmp_path):
    wd = tmp_path / "wd"
    wd.mkdir()
    _write(wd / "a.xlsx")
    _write(wd / "b.csv")
    fc = FakeClient()
    gw = Gateway(_cfg(tmp_path), client=fc)
    res = gw.scan_once()
    assert res["uploaded"] == 2
    assert {n for n, _ in fc.calls} == {"a.xlsx", "b.csv"}


def test_dedup_no_reupload(tmp_path):
    wd = tmp_path / "wd"
    wd.mkdir()
    _write(wd / "a.xlsx")
    fc = FakeClient()
    gw = Gateway(_cfg(tmp_path), client=fc)
    gw.scan_once()
    # 第二次扫描，无变化 → 不应再上传
    gw2 = Gateway(_cfg(tmp_path), client=fc)
    res = gw2.scan_once()
    assert res["uploaded"] == 0
    assert res["skipped"] == 1
    assert len(fc.calls) == 1


def test_modified_file_reuploaded(tmp_path):
    wd = tmp_path / "wd"
    wd.mkdir()
    _write(wd / "a.xlsx", "v1")
    fc = FakeClient()
    gw = Gateway(_cfg(tmp_path), client=fc)
    gw.scan_once()
    # 改内容
    _write(wd / "a.xlsx", "v2-longer-content")
    gw2 = Gateway(_cfg(tmp_path), client=fc)
    res = gw2.scan_once()
    assert res["uploaded"] == 1
    assert len(fc.calls) == 2


def test_exclude_patterns(tmp_path):
    wd = tmp_path / "wd"
    wd.mkdir()
    _write(wd / "a.xlsx")
    _write(wd / "b.tmp")
    (wd / ".git").mkdir()
    _write(wd / ".git" / "c.xlsx")
    fc = FakeClient()
    gw = Gateway(_cfg(tmp_path, exclude=["*.tmp", ".git/*"]), client=fc)
    res = gw.scan_once()
    assert res["uploaded"] == 1
    assert fc.calls[0][0] == "a.xlsx"


def test_include_filter(tmp_path):
    wd = tmp_path / "wd"
    wd.mkdir()
    _write(wd / "a.xlsx")
    _write(wd / "b.csv")
    _write(wd / "c.txt")
    fc = FakeClient()
    gw = Gateway(_cfg(tmp_path, include=["*.xlsx", "*.csv"]), client=fc)
    res = gw.scan_once()
    assert res["uploaded"] == 2
    assert {n for n, _ in fc.calls} == {"a.xlsx", "b.csv"}


def test_state_persisted(tmp_path):
    wd = tmp_path / "wd"
    wd.mkdir()
    _write(wd / "a.xlsx")
    fc = FakeClient()
    gw = Gateway(_cfg(tmp_path), client=fc)
    gw.scan_once()
    assert Path(gw.cfg.state_file).exists()
    state = json.loads(Path(gw.cfg.state_file).read_text(encoding="utf-8"))
    assert "a.xlsx" in state


def test_missing_watch_dir_skipped(tmp_path):
    fc = FakeClient()
    gw = Gateway(_cfg(tmp_path, watch_dirs=[str(tmp_path / "nope")]), client=fc)
    res = gw.scan_once()
    assert res["uploaded"] == 0
