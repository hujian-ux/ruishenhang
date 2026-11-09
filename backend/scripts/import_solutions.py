"""M15 · 解决方案技能包导入器。

把 D:/workbuddy_解决方案 下的 skill.md（prompt-as-skill 形态）解析成平台
可注册的智能体定义（AgentDefinition），并做分层与统计。

用法：
    python scripts/import_solutions.py --dry-run          # 只分析，不写库
    python scripts/import_solutions.py --out defs.json    # 导出标准化定义
    python scripts/import_solutions.py --board 04_角色     # 只看某板块

设计要点：
- 章节名有 4 套变体（适用场景/适用时机、输入/输入（inputs）/输入契约 …），
  统一归一化到 scene/inputs/workflow/outputs/rules 五类。
- system_prompt 不是简单拼接原文，而是重排为「角色 → 适用场景 → 输入契约 →
  工作流程 → 产出契约 → 硬规则」的标准段序，保证模型按步执行。
- 分层：正文去空白字数 < SHRINK_THRESHOLD 记为 "skeleton"（模板壳，需人工补规则）；
  >= 记为 "ready"。这是能否直接上生产的关键判据。
"""
from __future__ import annotations

import argparse
import collections
import glob
import json
import os
import re
import statistics
import sys

# 章节识别与包目录定位是「技能包怎么读」的唯一真源，放在 app 层给导入器、运行时
# （GET /agents/{id}/inputs）和回灌脚本共用。M52 之前这段逻辑只活在脚本里，
# 英文 `## Inputs` 之类变体识别不了 → 15 个包的输入契约是空的。
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.services.skill_pack import (  # noqa: E402
    classify_title as _classify_title,
    solutions_root as _solutions_root,
    split_sections as _split_sections,
)

# 默认指向仓库内自带的 115 包源（backend/solutions），使交付物自包含：
# 客户 clone 后无需外部目录即可导入/自动灌库。可用环境变量 SOLUTIONS_ROOT 覆盖。
SOLUTIONS_ROOT = os.environ.get("SOLUTIONS_ROOT") or _solutions_root()
SHRINK_THRESHOLD = 600  # 去空白字数低于此值视为模板壳

# M15/M25 P1：业务包全局补平台硬规则。优先用平台单一来源；若脚本独立运行且
# 后端不可导入，则降级为 no-op（不影响导入本身），由 backfill 脚本事后补齐。
try:
    from app.services.hard_rules import ensure_hard_rules as _ensure_hard_rules
except Exception:  # pragma: no cover - 独立运行兜底
    def _ensure_hard_rules(body: str) -> str:
        return body

# 章节别名表已迁到 app/services/skill_pack.py：导入器、运行时输入清单、回灌脚本
# 必须认同一套标题，否则「导入时有输入契约、试跑时看不到清单」这类漂移会反复出现。
# 这里保留 `split_sections` 名字，让既有调用方（测试/回灌）不用改。
split_sections = _split_sections

BOARD_LABEL = {
    "01_制造业": ("industry", "制造业"),
    "02_零售": ("industry", "零售"),
    "03_跨境电商": ("industry", "跨境电商"),
    "04_角色": ("office", "办公角色"),
}

# 需要「先查企业知识/制度再执行」的信号 → RAG 类适配器；否则纯生成（输入由用户提供）。
# 分强弱两档：弱词（标准/规范/条款…）在业务描述里极易自然出现，单次命中不代表要检索，
# 否则会把「齐套与缺料预警」这种纯计算类误判成 RAG。故用「出现次数门槛」而非 any-match。
STRONG_RETRIEVAL = re.compile(r"知识库|FAQ|问答库|检索|出处|引自|依据库|引自第|条款库")
WEAK_RETRIEVAL = re.compile(r"制度|规范|条款|法规|政策|标准")


def pick_adapter(text: str) -> str:
    """技能包 → adapter_type 映射（第35章选型结论，不许凭空）。

    技能包的输入绝大多数由用户直接提供（xlsx/csv），本质是「LLM 按流程执行」→ llm；
    仅当技能本身明确要求翻企业制度/规范/知识库才需要 RAG → llm_retrieval。
    判据：强信号 >=2 次，或弱信号 >=4 次。
    """
    strong = len(STRONG_RETRIEVAL.findall(text))
    weak = len(WEAK_RETRIEVAL.findall(text))
    return "llm_retrieval" if (strong >= 2 or weak >= 4) else "llm"


def parse_frontmatter(text: str) -> dict:
    m = re.match(r"^---\n(.*?)\n---\n?", text, re.S)
    if not m:
        return {}
    fm = {}
    for line in m.group(1).split("\n"):
        if ":" in line and not line.lstrip().startswith("-"):
            k, v = line.split(":", 1)
            fm[k.strip()] = v.strip()
    return fm


def build_system_prompt(name: str, desc: str, sec: dict) -> str:
    """重排为标准段序，模型按步执行。"""
    parts = [f"你是「{name}」执行专家。{desc}"]
    if sec.get("position"):
        parts.append(f"## 定位\n{sec['position']}")
    if sec.get("scene"):
        parts.append(f"## 适用场景\n{sec['scene']}")
    if sec.get("inputs"):
        # M52：清单来自包作者原文。平台会把用户附上的文件转成上下文，模型不该再
        # 让对方「重述一遍输入清单」——只许就缺的那几项问，且要指名到具体文件。
        parts.append(
            f"## 输入契约\n{sec['inputs']}\n"
            "处理要求：\n"
            "1. 先按上面的清单逐项核对本次材料，缺哪项就在开头「缺口」里点名（缺 X → 影响什么）；\n"
            "2. 不要要求用户复述已经随附的文件内容，也不要整段回抄清单当问题；\n"
            "3. 缺项不影响主体产出时，先按明确假设出草稿并标注假设口径，不要把整单退回去。")
    if sec.get("workflow"):
        parts.append(f"## 工作流程（严格按序执行，不得跳步）\n{sec['workflow']}")
    if sec.get("outputs"):
        parts.append(f"## 产出契约\n{sec['outputs']}")
    if sec.get("rules"):
        parts.append(f"## 硬规则（违反即视为失败）\n{sec['rules']}")
    if sec.get("tools"):
        parts.append(f"## 依赖工具\n{sec['tools']}")
    if sec.get("other"):
        # 未归类的原文段落（含公式/判定漏斗/示例）是实战细节，原样保留供模型参照
        parts.append(f"## 作业手册原文\n{sec['other']}")
    parts.append("## 通用红线\n"
                 "1. 每个结论必须能追溯到输入中的具体行/字段，无法追溯的写「无依据」。\n"
                 "2. 算不出来的指标明确写「本月无记录」，不得估算填充。\n"
                 "3. 问题必须落到唯一责任人 + 可验收动作 + 时限，禁止「加强沟通」式空话。")
    # M15/M25 P1：平台硬规则（8 条跨平台红线）接在最前，与公共库同源、幂等。
    return _ensure_hard_rules("\n\n".join(parts))


def parse_skill(path: str) -> dict:
    text = open(path, encoding="utf-8").read()
    fm = parse_frontmatter(text)
    body = re.sub(r"^---\n.*?\n---\n?", "", text, flags=re.S)
    sec = split_sections(body)
    name = fm.get("name") or os.path.basename(os.path.dirname(path))
    desc = fm.get("description", "")

    rel = os.path.relpath(path, SOLUTIONS_ROOT).replace("\\", "/")
    seg = rel.split("/")
    board = seg[0]
    kind, board_cn = BOARD_LABEL.get(board, ("industry", board))
    # 04_角色/HR/xxx 有二级职能；其余板块二级即方案名
    sub = seg[1] if kind == "office" and len(seg) > 3 else ""

    chars = len(re.sub(r"\s", "", body))
    return {
        "source": rel,
        "solution_dir": os.path.dirname(rel),
        "name": name,
        "description": desc,
        "board": board,
        "board_cn": board_cn,
        "kind": kind,             # industry | office
        "sub_function": sub,      # HR / 行政 / 财务 / 销售（仅 office）
        "adapter_type": pick_adapter(text),
        "tags": [board_cn] + ([sub] if sub else []),
        "inputs_contract": sec.get("inputs", ""),
        "outputs_contract": sec.get("outputs", ""),
        "sections": sorted(sec.keys()),
        "has_workflow": bool(sec.get("workflow")),
        "has_rules": bool(sec.get("rules")),
        "body_chars": chars,
        "grade": "ready" if chars >= SHRINK_THRESHOLD else "skeleton",
        "system_prompt": build_system_prompt(name, desc, sec),
        "meta": {k: v for k, v in fm.items() if k not in ("name", "description")},
    }


def scan(root: str = SOLUTIONS_ROOT) -> list[dict]:
    files = glob.glob(os.path.join(root, "**", "skill.md"), recursive=True)
    return [parse_skill(f) for f in sorted(files)]


def report(defs: list[dict]) -> None:
    print(f"技能包总数：{len(defs)}")
    by = collections.defaultdict(list)
    for d in defs:
        key = f"{d['board_cn']}" + (f"/{d['sub_function']}" if d["sub_function"] else "")
        by[key].append(d)
    print(f"\n{'板块':22s} {'数量':>4s} {'中位字数':>8s} {'ready':>6s} {'skeleton':>9s}")
    print("-" * 58)
    for k in sorted(by):
        v = by[k]
        med = int(statistics.median([x["body_chars"] for x in v]))
        r = sum(1 for x in v if x["grade"] == "ready")
        print(f"{k:22s} {len(v):4d} {med:8d} {r:6d} {len(v) - r:9d}")
    print("-" * 58)

    ready = [d for d in defs if d["grade"] == "ready"]
    skel = [d for d in defs if d["grade"] == "skeleton"]
    print(f"ready（可直接上生产）：{len(ready)}")
    print(f"skeleton（模板壳，需补规则）：{len(skel)}")
    print(f"含工作流程章节：{sum(1 for d in defs if d['has_workflow'])}/"
          f"{len(defs)}   含硬规则章节：{sum(1 for d in defs if d['has_rules'])}/{len(defs)}")

    print("\n最实 5 个：")
    for d in sorted(defs, key=lambda x: -x["body_chars"])[:5]:
        print(f"  {d['body_chars']:5d}  {d['board_cn']}/{d['name']}")
    print("最薄 5 个（需补规则）：")
    for d in sorted(defs, key=lambda x: x["body_chars"])[:5]:
        print(f"  {d['body_chars']:5d}  {d['board_cn']}/{d['name']}")


def apply_defs(defs: list[dict], scope: str = "department",
               batch_name: str = "", requester: str = "p_admin",
               dept: str = "d_platform") -> dict:
    """把解析结果真正注册进平台（本地直连 service，离线可跑）。

    与 `POST /agents/import` 同语义：全部进 pending_review，并合并成一张批次审批单
    （115 个包逐个开单在审批台上不可治理）。审批通过后才可 invoke（第35章红线）。
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if root not in sys.path:
        sys.path.insert(0, root)
    from app.routers.agents import _new_agent_id
    from app.services import agent_store, approval_store

    created, skipped = [], []
    for d in defs:
        agent_id = _new_agent_id(d["name"])
        exist = agent_store.get(agent_id)
        if exist and exist.get("source_path") == d["source"]:
            skipped.append({"name": d["name"], "reason": "已存在（同来源）", "agent_id": agent_id})
            continue
        agent_store.save({
            "agent_id": agent_id,
            "name": d["name"],
            "adapter_type": d["adapter_type"],
            "knowledge_scope": scope,
            "model_id": None,
            "registered_by": requester,
            "dept_id": dept,
            "status": "pending_review",
            "description": d["description"],
            "system_prompt": d["system_prompt"],
            "category": d["kind"],
            "category_cn": d["board_cn"],
            "sub_function": d["sub_function"],
            "tags": d["tags"],
            "source_path": d["source"],
            "inputs_contract": d["inputs_contract"],
            "outputs_contract": d["outputs_contract"],
        })
        created.append({"agent_id": agent_id, "name": d["name"], "adapter_type": d["adapter_type"]})

    ticket = None
    if created:
        ticket = approval_store.create_batch_ticket(
            [c["agent_id"] for c in created], requester, dept,
            batch_name or f"技能包批量导入 {len(created)} 个",
        )
    return {"created": len(created), "skipped": skipped, "agents": created,
            "approval_id": ticket["approval_id"] if ticket else None}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=SOLUTIONS_ROOT)
    ap.add_argument("--out", default="")
    ap.add_argument("--board", default="")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--apply", action="store_true", help="真正注册进平台（本地直连）")
    ap.add_argument("--scope", default="department",
                    choices=["personal", "owner", "department", "global"])
    ap.add_argument("--batch-name", default="")
    args = ap.parse_args()

    defs = scan(args.root)
    if args.board:
        defs = [d for d in defs if d["board"] == args.board]
    if not defs:
        print("未找到 skill.md，请检查 --root", file=sys.stderr)
        return 1
    report(defs)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(defs, f, ensure_ascii=False, indent=2)
        print(f"\n已导出：{args.out}（{len(defs)} 条）")
    if args.apply:
        res = apply_defs(defs, scope=args.scope, batch_name=args.batch_name)
        print(f"\n已注册 {res['created']} 个（跳过 {len(res['skipped'])}），"
              f"批次审批单：{res['approval_id']}")
        print("状态均为 pending_review —— 审批通过后才可调用（第35章红线）")
        by_adapter = collections.Counter(a["adapter_type"] for a in res["agents"])
        print(f"adapter 分布：{dict(by_adapter)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
