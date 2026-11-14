"""M27 备份 / 恢复演练 / 恢复脚本测试骨架。

覆盖：
  - 纯函数：_checksum（确定性 + 篡改检测）、_validate（缺字段/校验和/ schema）、
    _config_summary（敏感键脱敏）。
  - 三档命令语义：do_backup 产出可校验文件、do_verify 恢复演练（不写生产）、
    do_restore 缺 --confirm 拒绝、带 --confirm 写回、校验失败拒绝。
  - CLI 入口：main() 经 argparse 派发 do_backup/do_verify。

不依赖任何外部服务：_load_services 在测试中桩掉，避免耦合 app.services 的实时内存态。
真正对运行中的服务做跨进程恢复（PG 持久层）不靠单测顺带做，走线下演练。
"""
import json
import sys
from pathlib import Path

import pytest

import backup_restore as br


class _FakeStore:
    """模拟一个可快照/写回的 M27 store，用于验证 backup/restore 迭代逻辑。"""

    def __init__(self):
        self.last_restore = None
        self.calls = {"snapshot": 0, "restore": 0}

    def snapshot(self):
        self.calls["snapshot"] += 1
        return {"samples": [1, 2, 3], "feedback": [], "by_path": {}, "counters": {}}

    def restore(self, snap):
        self.calls["restore"] += 1
        self.last_restore = snap


@pytest.fixture
def fake_stores(monkeypatch):
    store = _FakeStore()
    monkeypatch.setattr(br, "_load_services", lambda: {"observability": store})
    return store


def _valid_payload():
    stores = {
        "observability": {"samples": [], "feedback": [], "by_path": {}, "counters": {}},
        "events": {"subscriptions": [], "events": []},
        "usage_store": {"records": []},
    }
    payload = {
        "schema": br.SCHEMA,
        "created_at": "2026-01-01T00:00:00+00:00",
        "config_summary": {"OIDC_ENABLED": "<default>"},
        "stores": stores,
        "counts": br._counts(stores),
        "note": "test",
    }
    payload["checksum"] = br._checksum(payload)
    return payload


# ── 纯函数 ────────────────────────────────────────────────────────────────
def test_checksum_deterministic():
    p = _valid_payload()
    assert br._checksum(p) == br._checksum(p)


def test_checksum_detects_tamper():
    p = _valid_payload()
    good = br._checksum(p)
    p["note"] = "tampered"
    assert br._checksum(p) != good


def test_config_summary_masks_sensitive(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "sk-secret")
    monkeypatch.setenv("OIDC_ENABLED", "1")
    summary = br._config_summary()
    assert summary["LLM_API_KEY"] == "<set>"
    assert summary["OIDC_ENABLED"] == "1"
    # 未设置的非敏感键回落 <default>
    assert summary.get("WEB_SEARCH_ENABLED") in ("<default>", None, "")


def test_validate_ok():
    assert br._validate(_valid_payload()) == []


def test_validate_missing_field():
    p = _valid_payload()
    del p["schema"]
    errs = br._validate(p)
    assert any("schema" in e for e in errs)


def test_validate_checksum_mismatch():
    p = _valid_payload()
    p["created_at"] = "2099-01-01T00:00:00+00:00"  # 改字段但不重算 checksum
    errs = br._validate(p)
    assert any("sha256" in e for e in errs)


def test_validate_schema_mismatch():
    p = _valid_payload()
    p["schema"] = "wrong"
    p["checksum"] = br._checksum(p)
    errs = br._validate(p)
    assert any("schema" in e for e in errs)


# ── 三档命令 ──────────────────────────────────────────────────────────────
def test_backup_then_verify(tmp_path, fake_stores):
    out = tmp_path / "bk.json"
    assert br.do_backup(str(out)) == 0
    assert out.is_file()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["schema"] == br.SCHEMA
    assert "checksum" in data
    # verify 恢复演练：通过且未写生产
    assert br.do_verify(str(out)) == 0


def test_verify_missing_file(tmp_path):
    assert br.do_verify(str(tmp_path / "nope.json")) == 1


def test_restore_refuses_without_confirm(tmp_path, fake_stores):
    out = tmp_path / "bk.json"
    br.do_backup(str(out))
    fake_stores.calls["restore"] = 0
    rc = br.do_restore(str(out), confirm=False)
    assert rc == 2
    assert fake_stores.calls["restore"] == 0


def test_restore_writes_with_confirm(tmp_path, fake_stores):
    out = tmp_path / "bk.json"
    br.do_backup(str(out))
    rc = br.do_restore(str(out), confirm=True)
    assert rc == 0
    assert fake_stores.calls["restore"] == 1


def test_restore_refuses_on_invalid(tmp_path, fake_stores):
    out = tmp_path / "bk.json"
    br.do_backup(str(out))
    data = json.loads(out.read_text(encoding="utf-8"))
    data["created_at"] = "2099-01-01T00:00:00+00:00"  # 破坏校验和
    out.write_text(json.dumps(data), encoding="utf-8")
    assert br.do_restore(str(out), confirm=True) == 1


def test_cli_backup_and_verify(tmp_path, fake_stores, monkeypatch):
    out = tmp_path / "cli.json"
    monkeypatch.setattr(sys, "argv", ["backup_restore.py", "--backup", str(out)])
    assert br.main() == 0
    assert out.is_file()
    monkeypatch.setattr(sys, "argv", ["backup_restore.py", "--verify", str(out)])
    assert br.main() == 0
