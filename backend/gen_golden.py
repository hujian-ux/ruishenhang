"""M2 W3：构建 golden 评测集（>=200 条）并落盘 services/golden.jsonl。

与 app.services.eval.build_golden_entries 同源；运行：python gen_golden.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.services.eval import build_golden_entries, GOLDEN_PATH  # noqa: E402


def main():
    entries = build_golden_entries()
    GOLDEN_PATH.write_text(
        "\n".join(json.dumps(e, ensure_ascii=False) for e in entries) + "\n",
        encoding="utf-8",
    )
    print(f"golden 已生成：{GOLDEN_PATH}  ({len(entries)} 条)")


if __name__ == "__main__":
    main()
