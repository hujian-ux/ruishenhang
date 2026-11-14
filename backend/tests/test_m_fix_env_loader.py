"""M-fix · 配置加载层修复回归。

历史 bug：_load_env_files 只读了 backend/.env 与 backend/.env.local，
仓库根 .env（含 TAVILY/BOCHA/WEB_SEARCH 等密钥）实际不生效（"配置假通"）。

本用例固定验证三层优先级：根 .env（基础）< backend/.env < backend/.env.local（最高），
且空值（KEY=）不注入。
"""
from __future__ import annotations

import os
import pathlib
import tempfile

from app.core.config import _load_env_files


def test_env_loader_reads_root_and_respects_priority(monkeypatch):
    tmp = pathlib.Path(tempfile.mkdtemp())
    (tmp / "backend").mkdir()
    (tmp / ".env").write_text(
        "EVL_ROOT=root\nEVL_CONFLICT=root\nEVL_EMPTY=\n", encoding="utf-8")
    (tmp / "backend" / ".env").write_text(
        "EVL_BE=be\nEVL_CONFLICT=be\n", encoding="utf-8")
    (tmp / "backend" / ".env.local").write_text(
        "EVL_LOCAL=local\nEVL_CONFLICT=local\n", encoding="utf-8")

    saved = dict(os.environ)
    try:
        monkeypatch.delenv("EAP_SKIP_ENV_LOCAL", raising=False)
        _load_env_files(root_dir=tmp)
        assert os.environ.get("EVL_ROOT") == "root"       # 根 .env 作为基础层被读取
        assert os.environ.get("EVL_BE") == "be"            # backend/.env 被读取
        assert os.environ.get("EVL_LOCAL") == "local"      # .env.local 被读取
        assert os.environ.get("EVL_CONFLICT") == "local"   # .env.local 优先级最高
        assert "EVL_EMPTY" not in os.environ               # 空值不注入
    finally:
        os.environ.clear()
        os.environ.update(saved)


def test_env_loader_skips_local_under_skip_flag(monkeypatch):
    """EAP_SKIP_ENV_LOCAL=1 时，backend/.env.local 仍应被跳过（测试隔离）。"""
    tmp = pathlib.Path(tempfile.mkdtemp())
    (tmp / "backend").mkdir()
    (tmp / ".env").write_text("EVL_ROOT2=root\n", encoding="utf-8")
    (tmp / "backend" / ".env.local").write_text("EVL_LOCAL2=local\n", encoding="utf-8")

    saved = dict(os.environ)
    try:
        monkeypatch.setenv("EAP_SKIP_ENV_LOCAL", "1")
        _load_env_files(root_dir=tmp)
        assert os.environ.get("EVL_ROOT2") == "root"       # 根层仍读
        assert "EVL_LOCAL2" not in os.environ              # .env.local 被跳过
    finally:
        os.environ.clear()
        os.environ.update(saved)
