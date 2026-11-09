"""M52 技能包目录读取：章节解析 / 输入清单 / 包内样例文件。

为什么要有这一层：115 个技能包不是只有一段 prompt。包目录里同时带着客户询价邮件、
材料价格表、历史报价这类「原始输入」样例，以及已产出的交付物。M15 导入时只吃
skill.md 正文，样例文件从头到尾没进链路，运行时模型手里没数据，只能回头问用户
「请提供输入清单」——而清单纯粹是包作者已经写在 skill.md 里的东西。

本模块把「包里有什么」变成可查询的资产：
- `split_sections` / `classify_title`：章节识别唯一真源（英文 Inputs、执行步骤（内部流程）
  这类变体此前全落进 other，导致 15 个包的输入契约是空的）。
- `pack_dir`：定位包目录。板块 01-03 的 skill.md 与素材同目录；板块 04 的 skill.md
  在 `03-ji-neng-bao/<拼音>/` 下，素材在上两级。
- `samples`：按 readme 的「文件清单」表或文件名约定，把包内文件分成
  原始输入 / 交付物 / 说明文件，只有原始输入会被当作试跑素材。
"""
from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional

# ── 章节识别 ───────────────────────────────────────────────────────────────
# 归一化键：scene 适用场景 / position 定位 / inputs 输入 / workflow 流程 /
#           outputs 产出 / rules 规则 / disclaimer 边界 / prompt_entry 提示词 /
#           tools 依赖工具 / other 未识别
SECTION_ALIAS = {
    # 场景 / 触发
    "适用场景": "scene", "适用时机": "scene", "适用岗位": "scene", "启用时机": "scene",
    "触发条件": "scene", "适用场景清单": "scene", "使用场景": "scene",
    "二、什么时候用（适用时机）": "scene",
    # 定位 / 一句话
    "能力定位": "position", "一句话定位": "position", "一句话结论": "position",
    "核心能力": "position", "这个skill解决什么": "position",
    "这个Skill解决什么": "position",
    # 输入
    "输入": "inputs", "输入（inputs）": "inputs", "输入契约": "inputs",
    "输入要求": "inputs", "需要的输入": "inputs", "inputs": "inputs",
    "输入（原始数据源）": "inputs",
    # 流程
    "工作流程": "workflow", "流程": "workflow", "执行步骤": "workflow",
    "处理流程": "workflow", "作业流程": "workflow", "工作流": "workflow",
    "workflow": "workflow", "执行步骤(内部流程)": "workflow",
    # 产出
    "产出": "outputs", "输出": "outputs", "输出契约": "outputs", "交付物": "outputs",
    "交付契约": "outputs", "输出（outputs）": "outputs", "outputs": "outputs",
    "输出excel": "outputs", "输出格式说明（excel交付）": "outputs",
    # 规则
    "关键规则": "rules", "硬规则": "rules", "红线": "rules", "约束": "rules",
    "核心原则": "rules", "强制规则": "rules", "校验规则": "rules", "禁止": "rules",
    "禁止事项": "rules", "关键规则/提示": "rules", "关键规则/提示词要点": "rules",
    "关键规则/提示词": "rules", "计算规则(写死,不可自行变更)": "rules",
    "计算规则(写死)": "rules", "输出前自检清单": "rules",
    # 边界
    "不承诺": "disclaimer", "边界": "disclaimer", "不适用范围": "disclaimer",
    "不依赖的前提": "disclaimer", "边界（不承诺什么）": "disclaimer",
    # 提示词
    "提示词入口": "prompt_entry", "提示词": "prompt_entry", "主提示词": "prompt_entry",
    "内嵌提示词（主提示词要点）": "prompt_entry",
    # 工具
    "依赖工具（底层 mcp / 能力）": "tools", "依赖工具": "tools",
}

# 剥序号：一、 二、 / 1. 1、 / （一） / (1)
_ORDINAL = re.compile(
    r"^\s*(?:[一二三四五六七八九十]+[、.．]|[（(][一二三四五六七八九十\d]+[）)]|\d+[、.．])\s*")


def norm_title(title: str) -> str:
    """剥掉中文/阿拉伯序号与空白，便于和别名表对齐。"""
    return re.sub(r"\s+", "", _ORDINAL.sub("", title.strip()).strip())


# 别名表统一做同样归一，避免「工作流程（5 步）」这类带括注的标题漏配。
SECTION_ALIAS = {norm_title(k): v for k, v in SECTION_ALIAS.items()}

# 关键词兜底：标题里带这些词就归到对应段。顺序即优先级——必须把 workflow 排在
# outputs 前面：包作者常写「Workflow（6 步，每步都有明确产出）」，先匹配「产出」
# 会把整段流程塞进产出契约，模型就丢了执行步骤。
_KEYWORD_RULES: List[tuple[str, tuple[str, ...]]] = [
    ("disclaimer", ("不承诺", "不适用", "边界")),
    ("rules", ("自检清单", "禁止", "规则", "约束", "红线", "计算规则")),
    ("inputs", ("输入", "inputs", "材料清单", "需要准备")),
    ("workflow", ("工作流", "workflow", "步骤", "流程")),
    ("outputs", ("输出", "产出", "交付", "outputs")),
    ("prompt_entry", ("提示词", "prompt", "主提示")),
    ("tools", ("依赖工具", "mcp")),
    ("scene", ("适用", "触发", "什么时候用", "使用场景", "解决什么", "能力定位", "定位")),
]


def classify_title(title: str) -> str:
    """标题 → 归一化段名。先精确别名，再关键词兜底，都没有则 other。"""
    key = norm_title(title)
    hit = SECTION_ALIAS.get(key)
    if hit:
        return hit
    low = key.lower()
    for name, words in _KEYWORD_RULES:
        if any(w.lower() in low for w in words):
            return name
    return "other"


def split_sections(body: str) -> Dict[str, str]:
    """按 `## ` 切章节并归一化。同名多段合并；未识别段落保留在 other 不丢信息。"""
    out: Dict[str, List[str]] = {}
    cur: Optional[str] = None
    buf: List[str] = []

    def flush() -> None:
        if cur:
            out.setdefault(cur, []).append("\n".join(buf).strip())

    for line in body.split("\n"):
        if line.startswith("## "):
            flush()
            title = line[3:].strip()
            cur = classify_title(title)
            buf = ["## " + title] if cur == "other" else []
        elif cur is not None:
            buf.append(line)
        elif line.strip():
            cur = "other"  # 首个 ## 之前的引言段
            buf = [line]
    flush()
    return {k: "\n".join(v for v in vs if v).strip() for k, vs in out.items()}


# ── 包目录定位 ─────────────────────────────────────────────────────────────
_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_DEFAULT_ROOT = os.path.join(_BACKEND_ROOT, "solutions")

# 说明文件：不是输入素材，不参与试跑
_META_FILES = {"skill.md", "readme.md", "页面内容.md", "index.html"}
# 生成物/打包件：不是原始输入
_OUTPUT_HINTS = ("jiao-fu", "jiaofu", "交付", "产出", "结果")
_PROMPT_HINTS = ("提示词", "zhuti-shici", "主提示词")
_INPUT_EXTS = {".csv", ".xlsx", ".xls", ".md", ".txt", ".json", ".tsv"}


def solutions_root() -> str:
    """技能包根目录：SOLUTIONS_ROOT 环境变量 > settings > 仓库内置 backend/solutions。"""
    env = os.environ.get("SOLUTIONS_ROOT") or ""
    if not env:
        try:
            from app.core.config import settings
            env = settings.SOLUTIONS_ROOT or ""
        except Exception:  # pragma: no cover - 独立运行兜底
            env = ""
    return env or _DEFAULT_ROOT


def _skill_md(source_path: str) -> str:
    """source_path（相对根目录的 skill.md 路径）→ 绝对路径。"""
    if not source_path:
        return ""
    rel = source_path.replace("\\", "/").lstrip("/")
    cand = os.path.join(solutions_root(), *rel.split("/"))
    return cand if os.path.isfile(cand) else ""


def pack_dir(source_path: str) -> str:
    """定位包目录（素材所在处）。

    板块 01-03：skill.md 与素材同目录。
    板块 04：skill.md 在 `03-ji-neng-bao/<拼音>/`，素材在上两级，靠 readme/页面内容锚定。
    找不到锚点时退回 skill.md 所在目录。
    """
    md = _skill_md(source_path)
    if not md:
        return ""
    root = os.path.abspath(solutions_root())
    cur = os.path.abspath(os.path.dirname(md))
    for _ in range(3):
        try:
            names = {n.lower() for n in os.listdir(cur)
                     if os.path.isfile(os.path.join(cur, n))}
        except OSError:
            break
        if names & {"readme.md", "页面内容.md", "index.html"}:
            return cur if os.path.commonpath([cur, root]) == root else ""
        parent = os.path.dirname(cur)
        if parent == cur or os.path.abspath(parent) == root:
            break
        cur = parent
    return os.path.abspath(os.path.dirname(md))


def _readme_kinds(dir_path: str) -> Dict[str, str]:
    """解析 readme 的「文件清单」表：文件名 → input/output/meta。

    表格三列（文件 | 类型 | 说明）。类型列含「原始输入/输入」→ input，
    「交付物/产出」→ output，「Skill 定义」→ meta。解析不到就返回空，由文件名兜底。
    """
    kinds: Dict[str, str] = {}
    for name in ("readme.md", "README.md", "页面内容.md"):
        p = os.path.join(dir_path, name)
        if not os.path.isfile(p):
            continue
        try:
            text = open(p, encoding="utf-8", errors="ignore").read()
        except OSError:
            continue
        for line in text.split("\n"):
            if not line.strip().startswith("|"):
                continue
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) < 2:
                continue
            fname = re.sub(r"[`*]", "", cells[0]).strip()
            fname = fname.replace("\\", "/").rsplit("/", 1)[-1]
            label = re.sub(r"[`*]", "", cells[1])
            if not fname or "." not in fname:
                continue
            if "原始输入" in label or label == "输入":
                kinds[fname.lower()] = "input"
            elif "交付" in label or "产出" in label:
                kinds[fname.lower()] = "output"
            elif "skill" in label.lower() or "定义" in label:
                kinds[fname.lower()] = "meta"
    return kinds


def _key_of(name: str) -> str:
    """文件名 → 归一化拼音键（中文转拼音、去分隔符、小写）。

    包目录里的文件名有两种形态：开发机上是中文原名，仓库分发版是拼音（客户询价邮件.md
    → ke-hu-xun-jia-you-jian.md）。readme 的文件清单始终写中文，两种形态都要能对上，
    否则「交付物」会被当成输入喂回模型——那等于把答案先塞给模型。
    """
    stem = os.path.splitext(os.path.basename(name or ""))[0]
    if _CJK.search(stem):
        try:
            from pypinyin import lazy_pinyin
            stem = "".join(lazy_pinyin(stem))
        except Exception:      # 没有 pypinyin 就退回分隔符比对（中文名仍能精确匹配）
            pass
    return re.sub(r"[^a-z0-9]", "", stem.lower())


def _match_kinds(files: List[str], readme: Dict[str, str]) -> Dict[str, tuple]:
    """把 readme 里的中文文件名对到磁盘上的实际文件（精确优先，其次拼音模糊）。

    返回 {磁盘文件名: (kind, 中文原名)}。中文原名要带出去——模型看到的附件名如果是
    `li-shi-bao-jia.xlsx`，跟技能输入清单里的「历史报价 Excel」对不上，它就会判成
    「未提供」并回头问用户要，白跑一趟。
    """
    import difflib

    out: Dict[str, tuple] = {}
    pool = {f: _key_of(f) for f in files}
    lower = {f.lower(): f for f in files}
    for want, kind in readme.items():
        if want in lower:
            out[lower[want]] = (kind, want)
            continue
        target = _key_of(want)
        if not target:
            continue
        best, score = "", 0.0
        for f, key in pool.items():
            if f in out:
                continue
            r = difflib.SequenceMatcher(None, target, key).ratio()
            if r > score:
                best, score = f, r
        if best and score >= 0.72:     # 率→lv 这类转写差异留下余量，又不至于认错
            out[best] = (kind, want)
    return out



def _guess_kind(fname: str) -> str:
    """readme 没覆盖时的兜底判据：文件名约定（中文原名与拼音版都要认）。"""
    low = fname.lower()
    if low in _META_FILES or low.endswith(".zip"):
        return "meta"
    key = _key_of(fname)          # 中文→拼音的归一键，兼容两种命名形态
    if any(h in low for h in _PROMPT_HINTS) or "zhutishici" in key or "tishici" in key:
        return "meta"
    if (any(h in key for h in ("jiaofu", "chanchu", "jieguo"))
            or any(h in fname for h in _OUTPUT_HINTS)):
        return "output"
    if os.path.splitext(low)[1] in _INPUT_EXTS:
        return "input"
    return "meta"


def samples(source_path: str) -> List[Dict[str, Any]]:
    """包内文件清单（含分类）。只列包目录直属文件，不递归。"""
    d = pack_dir(source_path)
    if not d or not os.path.isdir(d):
        return []
    names = [n for n in sorted(os.listdir(d)) if os.path.isfile(os.path.join(d, n))]
    matched = _match_kinds(names, _readme_kinds(d))
    out: List[Dict[str, Any]] = []
    for name in names:
        path = os.path.join(d, name)
        kind, display = matched.get(name, ("", ""))
        kind = kind or _guess_kind(name)
        try:
            size = os.path.getsize(path)
        except OSError:
            size = 0
        out.append({
            "filename": name,
            # 中文原名（readme 有就还原）：给用户看、也喂给模型，拼音名它认不出来
            "display_name": display or name,
            "kind": kind,                      # input | output | meta
            "size": size,
            "ext": os.path.splitext(name)[1].lower().lstrip("."),
            "from_readme": name in matched,
            "rel_path": os.path.relpath(path, os.path.abspath(solutions_root())).replace("\\", "/"),
        })
    return out


def input_samples(source_path: str, limit: int = 6) -> List[Dict[str, Any]]:
    """可作试跑素材的原始输入文件（按体积升序，便宜的先上）。"""
    items = [s for s in samples(source_path) if s["kind"] == "input" and s["size"] > 0]
    return sorted(items, key=lambda x: x["size"])[:limit]


def sample_bytes(rel_or_name: str, source_path: str) -> Optional[bytes]:
    """读样例文件内容。只允许读包目录内的直属文件，防目录穿越。"""
    d = pack_dir(source_path)
    if not d:
        return None
    name = rel_or_name.replace("\\", "/").rsplit("/", 1)[-1]
    if name in ("", ".", ".."):
        return None
    path = os.path.join(d, name)
    if os.path.dirname(os.path.abspath(path)) != os.path.abspath(d):
        return None
    if not os.path.isfile(path):
        return None
    try:
        return open(path, "rb").read()
    except OSError:
        return None


_BULLET = re.compile(r"^\s*(?:[-*+·]|\d+[.、)）]|[（(]\d+[）)])\s*")
_CJK = re.compile(r"[\u4e00-\u9fff]")


def checklist(agent: Dict[str, Any], limit: int = 12) -> List[str]:
    """技能的输入清单（要用户给什么）。

    优先从 skill.md 的输入章节取条目（包作者写的最新），退化到库里存的
    inputs_contract 文本切条。两条都空返回空列表，前端据此不显示该区块。
    """
    src = agent.get("source_path") or ""
    text = ""
    md = _skill_md(src)
    if md:
        try:
            body = re.sub(r"^---\n.*?\n---\n?", "",
                          open(md, encoding="utf-8", errors="ignore").read(), flags=re.S)
            text = split_sections(body).get("inputs", "")
        except OSError:
            text = ""
    if not text.strip():
        text = agent.get("inputs_contract") or ""

    items: List[str] = []
    for raw in text.split("\n"):
        line = raw.strip()
        if not line or line.startswith("#") or set(line) <= set("-|: "):
            continue
        if line.startswith("|"):        # 表格行：取首列当条目
            cells = [c.strip() for c in line.strip("|").split("|")]
            line = cells[0] if cells else ""
            if not line or set(line) <= set("-: "):
                continue
        line = _BULLET.sub("", line).strip(" 　")
        line = re.sub(r"\*\*|`", "", line).strip()
        if line and line not in items:
            items.append(line[:80])
    return items[:limit]
