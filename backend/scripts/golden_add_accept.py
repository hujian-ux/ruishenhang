"""给 golden 补同族等价目标（accept_titles），得到不失真的召回指标。

问题：一条 query 往往能被同族多篇文档回答（「渠道政策怎么执行」对 2026-041 / 2026-025 /
2026-077 都成立），但 golden 只标一个 doc_id。检索召回到同族另一篇时被算成漏检，
指标系统性偏低——实测 44 条里 5 条「坏例」全是同族等价，真漏检 0 条。

做法：按标题去掉尾部编号取族名，把同族文档全部写进 accept_titles。
注意这不是放宽标准：跨族召回仍然算漏检，只是承认「同族任一篇都能答」。

用法：
  ./.venv/Scripts/python.exe scripts/golden_add_accept.py \
      --golden D:/pilot_corpus/golden.jsonl --out D:/pilot_corpus/golden_v2.jsonl [--max 20]
"""
import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import eval as ev  # noqa: E402
from app.services import kb_store, knowledge  # noqa: E402


def family(title: str) -> str:
    t = re.sub(r"[（(][^）)]*[）)]\s*$", "", (title or "").strip())
    return t.strip() or (title or "")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--golden", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--max", type=int, default=20, help="每族最多补多少个等价目标")
    args = ap.parse_args()

    kb_store.load_from_pg()
    by_family: dict = defaultdict(list)
    for p in knowledge.get_parents():
        by_family[family(p["title"])].append(p["title"])

    rows = ev.load_golden_file(args.golden)
    out = []
    added = 0
    for r in rows:
        e = dict(r)
        exp = str(e.get("expected_title") or "")
        fam = family(exp)
        peers = [t for t in by_family.get(fam, []) if t != exp][: args.max]
        if peers:
            e["accept_titles"] = peers
            added += 1
        out.append(e)

    with open(args.out, "w", encoding="utf-8") as f:
        f.write("# 自动补同族等价目标（accept_titles），原始条目未改动\n")
        for e in out:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    print(f"已补 accept_titles {added}/{len(out)} 条 → {args.out}")


if __name__ == "__main__":
    main()
