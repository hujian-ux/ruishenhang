"""本地文件网关 · 守护进程核心。

功能：监听若干本地目录，把新增/变更文件上传到企业 AI 中台（默认 ``/agent/files``）。
特性：
  - 去重：按内容 sha256 + 大小 + mtime 判定是否真的变了，状态文件防重传。
  - include/exclude：glob 模式过滤（exclude 优先）。
  - 轮询即可（无需 watchdog；若装了 watchdog 可由调用方包装事件，本模块保持纯轮询）。
  - 平台客户端可注入，便于离线单测。

用法：
  python gateway.py --config config.yaml            # 常驻轮询
  python gateway.py --config config.yaml --once     # 扫一次就退（适合 cron）
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from platform_client import PlatformClient


@dataclass
class Config:
    backend_url: str = "http://localhost:8080"
    token: Optional[str] = None
    tenant: Optional[str] = None
    watch_dirs: List[str] = field(default_factory=list)
    include: List[str] = field(default_factory=list)   # 空=全部
    exclude: List[str] = field(default_factory=list)
    target: str = "agent_files"                          # 预留：未来支持 kb 等
    endpoint: str = "/agent/files"
    poll_interval: float = 30.0
    state_file: str = ".gw_state.json"

    @classmethod
    def from_yaml(cls, path: str) -> "Config":
        import yaml
        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        return cls(
            backend_url=raw.get("backend_url", cls.backend_url),
            token=raw.get("token"), tenant=raw.get("tenant"),
            watch_dirs=raw.get("watch_dirs", []) or [],
            include=raw.get("include", []) or [],
            exclude=raw.get("exclude", []) or [],
            target=raw.get("target", cls.target),
            endpoint=raw.get("endpoint", cls.endpoint),
            poll_interval=float(raw.get("poll_interval", cls.poll_interval)),
            state_file=raw.get("state_file", cls.state_file),
        )


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _rel(root: str, path: str) -> str:
    return os.path.relpath(path, root).replace(os.sep, "/")


class Gateway:
    def __init__(self, cfg: Config, client: Optional[PlatformClient] = None):
        self.cfg = cfg
        self.client = client or PlatformClient(cfg.backend_url, cfg.token, cfg.tenant)
        self.state: Dict[str, dict] = self._load_state()

    # ── 状态持久化 ──
    def _load_state(self) -> Dict[str, dict]:
        p = Path(self.cfg.state_file)
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                return {}
        return {}

    def _save_state(self) -> None:
        Path(self.cfg.state_file).write_text(
            json.dumps(self.state, ensure_ascii=False, indent=2), encoding="utf-8")

    # ── 过滤 ──
    def _matched(self, rel: str) -> bool:
        rel = rel.replace("\\", "/")
        parts = rel.split("/")
        # 同时匹配「完整相对路径」与「各级后缀」，让 .git/* 在顶层或嵌套都生效
        suffixes = ["/".join(parts[i:]) for i in range(len(parts))]
        def hit(pat: str) -> bool:
            return any(fnmatch.fnmatch(s, pat) for s in suffixes)
        if any(hit(p) for p in self.cfg.exclude):
            return False
        if self.cfg.include:
            return any(hit(p) for p in self.cfg.include)
        return True

    def _walk(self) -> List[str]:
        out: List[str] = []
        for d in self.cfg.watch_dirs:
            root = Path(d)
            if not root.exists():
                continue
            for cur, _dirs, files in os.walk(root):
                for fn in files:
                    full = os.path.join(cur, fn)
                    if os.path.isfile(full) and self._matched(_rel(d, full)):
                        out.append(full)
        return out

    # ── 单次扫描 + 上传 ──
    def scan_once(self) -> Dict[str, int]:
        uploaded = 0
        skipped = 0
        for full in self._walk():
            rel = None
            # 找所属 watch 根，算相对路径
            for d in self.cfg.watch_dirs:
                if full.startswith(os.path.join(d, "")) or full == d:
                    rel = _rel(d, full)
                    break
            if rel is None:
                rel = os.path.basename(full)
            try:
                st = os.stat(full)
                digest = _sha256(full)
            except OSError:
                continue
            prev = self.state.get(rel)
            if prev and prev.get("hash") == digest:
                skipped += 1
                continue
            resp = self.client.upload_file(full, title=rel, endpoint=self.cfg.endpoint)
            status = resp.get("status", 0)
            if status in (200, 201):
                self.state[rel] = {"hash": digest, "size": st.st_size,
                                   "mtime": st.st_mtime}
                uploaded += 1
            else:
                # 上传失败：不改状态，下次重试（含 4xx/5xx）
                skipped += 1
        self._save_state()
        return {"uploaded": uploaded, "skipped": skipped, "total": len(self.state)}

    # ── 常驻轮询 ──
    def run_forever(self) -> None:
        if not self.client.token and self.cfg.token is None:
            self.client.login()
        while True:
            try:
                self.scan_once()
            except Exception as e:  # 单轮异常不应打死守护进程
                print(f"[gateway] scan error: {e}")
            time.sleep(max(1.0, self.cfg.poll_interval))


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="本地文件网关：监听目录 → 上传到中台")
    ap.add_argument("--config", required=True, help="配置文件路径 (yaml)")
    ap.add_argument("--once", action="store_true", help="只扫一次就退出（适合 cron）")
    args = ap.parse_args(argv)

    cfg = Config.from_yaml(args.config)
    gw = Gateway(cfg)
    if not args.once and cfg.token is None:
        gw.client.login()
    result = gw.scan_once()
    print(f"[gateway] uploaded={result['uploaded']} skipped={result['skipped']} "
          f"tracked={result['total']}")
    if not args.once:
        gw.run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
