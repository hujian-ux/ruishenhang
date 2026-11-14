"""M27 · AI 行为安全与工具层安全闭环（蓝图第 27/39/44 章）。

定位：一个「纯函数 + 可重放状态」的安全内核，不碰任何业务主链路。
  - 输入侧：提示注入 / 越狱检测（scan_input）
  - 来源侧：可信域名白名单（check_source）
  - 输出侧：脱敏 / 跨租户跨部门泄密 / 引用覆盖 / 置信度（scan_output）
  - 事实侧：关键数字的字面可验证比对（grounding_check）
  - 工具侧：白名单 + 单一过滤点 + 出网脱敏 + 私网拦截（guard_tool_call）

设计取舍：
  * 规则是模块级常量，policy() 只回显开关与「规则数量」，绝不回显任何密钥。
  * 所有判权都走 core.filter.visible_fields（第18章单一过滤点），不另起一套。
  * 进程内只留一组计数器，reset_state() 清零；无持久化状态。
  * 配置一律用 getattr(settings, ...) 读，config.py 由别组维护，缺项不炸。
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
import threading
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from app.core.config import settings
from app.core.filter import visible_fields
from app.services import _m27_store, observability

# ==================================================================== 配置读取
def _enabled() -> bool:
    return bool(getattr(settings, "GUARDRAILS_ENABLED", True))


def _strict() -> bool:
    return bool(getattr(settings, "GUARDRAILS_STRICT", False))


def _tool_auth() -> bool:
    return bool(getattr(settings, "GUARDRAILS_TOOL_AUTH", True))


def _output_mask() -> bool:
    return bool(getattr(settings, "GUARDRAILS_OUTPUT_MASK", True))


def _max_hits() -> int:
    try:
        return max(1, int(getattr(settings, "GUARDRAILS_MAX_INJECTION_HITS", 3)))
    except (TypeError, ValueError):
        return 3


def _host_of(source: str) -> str:
    """把 URL / 域名归一成小写 host（去 scheme、端口、路径、尾点）。"""
    s = (source or "").strip().lower()
    if not s:
        return ""
    if "://" in s:
        try:
            h = urlparse(s).hostname or ""
        except ValueError:
            h = ""
    else:
        h = s.split("/")[0].split("?")[0].split("#")[0]
    return h.split(":")[0].strip().strip(".").rstrip(".")


def _allowlist() -> list[str]:
    raw = str(getattr(settings, "GUARDRAILS_SOURCE_ALLOWLIST", "") or "")
    out = []
    for part in raw.split(","):
        h = _host_of(part)
        if h:
            out.append(h)
    return out


# ==================================================================== 注入规则
# 每条规则命中一次即记一条 hit；hits 数 = 命中的「规则条数」，避免一句话
# 重复命中同一规则把阈值刷爆。pattern 回显正则串，kind 供下游分流。
_INJECTION_RULES: list[dict[str, Any]] = [
    {
        "kind": "ignore_instructions",
        "pattern": (r"忽略(以上|上面|之前|前述|先前|所有|前面|这些|这段)?(的)?"
                    r"(指令|指示|提示|规则|约束|要求)"
                    r"|ignore\s+(all\s+|any\s+)?(previous|prior|above|earlier)\s+"
                    r"(instructions?|prompts?|rules?)"),
    },
    {
        "kind": "role_escalation",
        "pattern": (r"(扮演|假装|冒充|充当|切换(成|为)?|现在你是|从现在起你是|你就是)"
                    r".{0,10}(管理员|超管|平台管理员|系统管理员|root|开发者模式|运维|"
                    r"老板|CEO|越权|不受限制|无限制)"
                    r"|developer\s+mode|jailbreak"),
    },
    {
        "kind": "cross_dept_data",
        "pattern": (r"套取|越权查看|跨部门|他人部门|其他部门|其它部门|别的部门|"
                    r"所有部门|全部部门|(other|another|all)\s+(departments?|depts?)"),
    },
    {
        "kind": "secret_request",
        "pattern": (r"(密码|口令|密钥|私钥|token|api[_ -]?key|apikey|secret|凭证|凭据|"
                    r"access[_ -]?key)[\s\S]{0,24}?"
                    r"(告诉|给我|发我|贴出|打印|输出|显示|交出|是多少|是什么|发出来|"
                    r"列出来|show|give|print|reveal|dump)"
                    r"|(告诉|给我|发我|贴出|打印|输出|显示|交出|提供|索要|获取|套取|"
                    r"reveal|show|print|give|dump)[\s\S]{0,24}?"
                    r"(密码|口令|密钥|私钥|token|api[_ -]?key|apikey|secret|凭证|凭据)"),
    },
    {
        "kind": "export_all_bypass",
        "pattern": (r"(导出|下载|读取|拉取|获取|dump|export).{0,8}"
                    r"(全量|全部|所有数据|整库|整个库|全表)"
                    r"|绕过(权限|审批|鉴权|限制|校验|审计)|无视权限|跳过(权限|审批|鉴权)"
                    r"|越权(访问|查看|操作|导出)|bypass\s+(auth|permission|approval|security)"
                    r"|不受(任何)?(限制|约束)"),
    },
    {
        "kind": "prompt_leak",
        "pattern": (r"(系统提示词|system[\s_-]?prompt|内部提示词|你的(指令|提示词|设定)|"
                    r"初始(指令|提示词))[^。；\n]{0,12}"
                    r"(打印|输出|显示|复述|贴出来|告诉我|发给我|念出来|泄露|"
                    r"repeat|print|reveal|show|dump)"
                    r"|reveal\s+(your\s+)?(system\s+)?prompt"
                    r"|repeat\s+(the\s+)?(words\s+)?above|输出(你的)?(系统)?提示词"),
    },
]
for _r in _INJECTION_RULES:
    _r["_re"] = re.compile(_r["pattern"], re.IGNORECASE)

# 输出侧只把「明显恶意」的种类算作注入命中；cross_dept_data 里「跨部门」
# 在正规汇报里也可能出现，不计入，避免把正常答案的 confidence 打低。
_OUTPUT_INJECTION_KINDS = {
    "ignore_instructions", "role_escalation", "secret_request",
    "export_all_bypass", "prompt_leak",
}

# 入库防投毒：文档内嵌的指令型注入（区别于聊天注入）。
# 只在「强信号」上命中，避免把正常制度文本误判为投毒（误杀比漏杀更伤业务）。
_INGEST_POISON_RULES: list[dict[str, Any]] = [
    {"kind": "ignore_instructions_doc",
     "pattern": (r"(忽略|无视|disregar[dz]|ignore)\s*(之前|以上|前面|先前|所有|上述|"
                 r"prior|previous|above)?\s*(的)?\s*(指令|指示|规则|约束|instructions?|"
                 r"rules?|prompts?)")},
    {"kind": "role_pretend_doc",
     "pattern": (r"(you\s*are\s*now|pretend\s*(to\s*be|you\s*are)|developer\s*mode|"
                 r"jailbreak|你\s*现在\s*是\s*一个?\s*(ai|模型|助手|系统)|从此\s*你\s*是)")},
    {"kind": "delimiter_injection",
     "pattern": (r"(<\|im_start\|>|<\|system\|>|\[SYSTEM\]|###\s*system\s*:|"
                 r"---?\s*system\s*---?|<<SYS>>)")},
    {"kind": "leak_instructions_doc",
     "pattern": (r"(reveal\s*(your\s+)?(system\s+)?prompt|output\s*(your|系统)?"
                 r"(prompt|提示词|指令)|泄露\s*(你的|系统)?(提示词|指令))")},
    {"kind": "encoded_directive",
     "pattern": (r"(?i)(base64|base-64)\s*[:=]?\s*[A-Za-z0-9+/]{40,}={0,2}")},
]
for _r in _INGEST_POISON_RULES:
    _r["_re"] = re.compile(_r["pattern"], re.IGNORECASE)

_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
# 不可见字符（零宽连字、BOM、特殊空格、方向符、阿拉伯字母方向标记等）：
# 提示注入常靠它们绕过字面检测（肉眼看不到，正则却可能放过）。
_ZERO_WIDTH_RE = re.compile(
    r"[\u200b\u200c\u200d\u2060\ufeff\u2028\u2029\u202a-\u202e\u061c\u180e]"
)
# 常见拉丁同形字符（Cyrillic/Greek 视觉同形）→ ASCII，防止视觉欺骗型注入。
_HOMOGLYPH_MAP = {
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x",
    "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M", "Н": "H", "О": "O",
    "Р": "P", "С": "C", "Т": "T", "Х": "X", "і": "i", "ј": "j", "ѕ": "s",
    "ҙ": "g", "ԛ": "q", "ԝ": "w", "ӏ": "l",
}
_CITE_RE = re.compile(r"\[(?:[Ww])?\d+\]")
_CITE_ID_RE = re.compile(r"\[(?:[Ww])?(\d+)\]")


def _iter_injection_hits(text: str) -> list[dict[str, str]]:
    hits = []
    for rule in _INJECTION_RULES:
        m = rule["_re"].search(text or "")
        if m:
            excerpt = (m.group(0) or "").strip()
            hits.append({
                "pattern": rule["pattern"],
                "kind": rule["kind"],
                "excerpt": excerpt[:80],
            })
    return hits


# ==================================================================== 脱敏规则
# 顺序有讲究：先整段私钥、再赋值、再独立密钥、再身份证、最后手机号，
# 避免长串数字被短规则先切碎。
def _id_repl(m: re.Match) -> str:
    s = m.group(0)
    return s[:6] + "********" + s[-4:]


_MASK_RULES: list[tuple[re.Pattern, Any]] = [
    (re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]*?"
                r"-----END [A-Z0-9 ]*PRIVATE KEY-----", re.IGNORECASE),
     lambda m: "-----PRIVATE KEY ****-----"),
    (re.compile(r"(?<![A-Za-z0-9_])(password|passwd|pwd|secret|api[_-]?key|apikey|"
                r"access[_-]?key|token|密钥|密码|口令)\s*[:=]\s*"
                r"([A-Za-z0-9_\-./+:=@~]+)", re.IGNORECASE),
     lambda m: m.group(1) + "=****"),
    (re.compile(r"(?<![A-Za-z0-9])(sk-[A-Za-z0-9_\-]{16,}|AKIA[0-9A-Z]{12,}|"
                r"ghp_[A-Za-z0-9]{20,})"),
     lambda m: "****"),
    (re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"), _id_repl),
    (re.compile(r"(?<!\d)(1[3-9]\d)(\d{4})(\d{4})(?!\d)"),
     lambda m: m.group(1) + "****" + m.group(3)),
]


def _mask(text: str) -> tuple[str, bool]:
    out = text or ""
    changed = False
    for rx, repl in _MASK_RULES:
        new = rx.sub(repl, out)
        if new != out:
            changed = True
            out = new
    return out, changed


def _norm(s: str) -> str:
    """字面比对前去空白，兼容「600 元」与「600元」。"""
    return re.sub(r"\s+", "", s or "")


# ==================================================================== 工具常量
# P1-9：delegate_expert 是平台一等委派能力（只读推理、不落库、自带台账与审计），
# 纳入白名单走正常放行；否则 loop 虽会以 custom_tool_ok 特判放行，但每次调用都会先写
# 一条 allowed=False 的 tool_check 审计噪声，污染审计链。
_ALLOWED_TOOLS = {"rag_retrieve", "web_search", "file_read", "file_write", "kb_write",
                  "delegate_expert"}
_MASK_ARG_KEYS = ("url", "text", "query", "content", "snippet", "payload")
_VIS_KEYS = ("scope", "owner", "dept_id", "dept", "tenant_id")
_URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)

# 答案里的「关键事实」token：日期 / 文号 / 条款号 / W编号 / 数字+单位 / 纯数字。
# 纯语义判断一律不做，只抽可字面核验的片段。
_KEY_TOKEN_RE = re.compile(
    r"\d{4}[-/.年]\d{1,2}[-/.月]\d{1,2}日?"
    r"|[A-Za-z]{1,8}-\d{2,4}-\d{2,6}"
    r"|第\s*\d+\s*[条款项]"
    r"|\b[Ww]\d+\b"
    r"|\d+(?:[,.]\d+)*\s*(?:亿元|万元|个百分点|元|%|％|折|人次|人|个|条|款|项|次|"
    r"天|小时|年|月|日|号)"
    r"|(?<!\w)\d+(?:[,.]\d+)*(?!\w)"
)

# 轻量级运行时计数（只用于观测，不影响任何判定）
_STATS: dict[str, int] = {
    "scan_input": 0, "scan_output": 0, "grounding": 0, "tool_check": 0,
}
_STATS_LOCK = threading.Lock()


def _bump(key: str) -> None:
    with _STATS_LOCK:
        _STATS[key] = _STATS.get(key, 0) + 1


def _pick(d: dict, *keys: str) -> Any:
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return None


# ==================================================================== 1. 输入侧
def normalize_text(text: str, max_len: int = 4000) -> str:
    """数据归一化安全（DNS）：清不可见字符 / 同形字符 / 控制符，再截断。

    返回用于「真正喂给模型或入库」的干净文本。普通业务文本不含这些字符，
    所以归一化后与原文本一致，不会动到正常答案/语料的字面内容。
    """
    if not isinstance(text, str):
        text = str(text or "")
    text = _ZERO_WIDTH_RE.sub("", text)
    if _HOMOGLYPH_MAP:
        text = "".join(_HOMOGLYPH_MAP.get(ch, ch) for ch in text)
    text = _CONTROL_RE.sub("", text)
    return text[:max_len]


def scan_input(text: str) -> dict:
    """提示注入 / 越狱扫描。

    blocked：命中规则数 >= GUARDRAILS_MAX_INJECTION_HITS 或开启 STRICT。
    clean_text：经 normalize_text 归一化（清零宽/同形/控制符）+ 截断 4000，
                作为实际喂给运行时的文本（防零宽字符注入绕过字面检测）。
    """
    raw = text if isinstance(text, str) else str(text or "")
    cleaned = normalize_text(raw)
    hits = _iter_injection_hits(cleaned)
    max_hits = _max_hits()
    blocked = len(hits) >= max_hits or _strict()
    risk = round(min(1.0, len(hits) / max_hits), 3)
    if blocked and risk < 0.5:
        risk = 0.5
    if blocked:
        observability.observe_guardrail("input")  # 输入拦截计数
    _bump("scan_input")
    return {
        "blocked": bool(blocked),
        "hits": hits,
        "risk": risk,
        "clean_text": cleaned[:4000],
    }


# ==================================================================== 2. 来源侧
def check_source(source: str, scope: str = "global") -> dict:
    """可信来源判定：空来源明确 unavailable，不默认可信；白名单外不静默放行。"""
    src = (source or "").strip() if isinstance(source, str) else ""
    if not src:
        return {"trusted": False, "reason": "unavailable", "source": ""}
    host = _host_of(src)
    trusted = bool(host and any(
        host == d or host.endswith("." + d) for d in _allowlist()
    ))
    if trusted:
        reason = "allowlist"
    elif (scope or "global") == "global":
        reason = "untrusted_review"
    else:
        reason = "not_in_allowlist"
    return {"trusted": trusted, "reason": reason, "source": src}


# ==================================================================== 2.5 入库侧
def scan_ingest(text: str) -> dict:
    """入库防投毒：扫描文档正文是否内嵌提示注入 / 越狱指令。

    与聊天注入扫描（scan_input）分开：文档场景容错更低，只在强信号上命中，
    避免把「见下方说明」「系统会自动…」这类正常制度表述误判为投毒。
    返回 {poisoned, hits, risk}。poisoned=True 的文档应被拒入库（见 INGEST_REJECT_POISONED）。
    """
    body = text if isinstance(text, str) else str(text or "")
    hits = []
    for rule in _INGEST_POISON_RULES:
        m = rule["_re"].search(body)
        if m:
            hits.append({
                "pattern": rule["pattern"],
                "kind": rule["kind"],
                "excerpt": (m.group(0) or "").strip()[:80],
            })
    poisoned = bool(hits)
    risk = round(min(1.0, len(hits) / 2.0), 3) if hits else 0.0
    _bump("scan_ingest")
    return {"poisoned": poisoned, "hits": hits, "risk": risk}


# ==================================================================== 3. 输出侧
def _detect_leaks(hits: list, caller: Any) -> list[dict]:
    leaks = []
    if caller is None:
        return leaks
    c_tenant = getattr(caller, "tenantId", None)
    c_dept = getattr(caller, "deptId", None)
    for i, h in enumerate(hits):
        if not isinstance(h, dict):
            continue
        meta = h.get("meta") if isinstance(h.get("meta"), dict) else {}
        tenant = _pick(h, "tenant_id") or _pick(meta, "tenant_id")
        dept = _pick(h, "dept_id", "dept") or _pick(meta, "dept_id", "dept")
        reasons = []
        if tenant and c_tenant and str(tenant) != str(c_tenant):
            reasons.append("cross_tenant")
        if dept and c_dept and str(dept) != str(c_dept):
            reasons.append("cross_dept")
        if reasons:
            excerpt = str(_pick(h, "snippet", "text", "content", "title") or "")[:80]
            leaks.append({"index": i, "reasons": reasons, "excerpt": excerpt})
    return leaks


def _citation_coverage(answer: str, hits: list) -> float:
    ids = {int(n) for n in _CITE_ID_RE.findall(answer)}
    n = len(hits)
    if n <= 0:
        return 1.0 if ids else 0.0
    return round(min(1.0, len(ids) / n), 3)


def scan_output(answer: str, caller=None, hits=None, evidence: str = "") -> dict:
    """输出脱敏 + 泄密检测 + 引用覆盖 + 置信度评估。"""
    ans = answer if isinstance(answer, str) else str(answer or "")
    hit_list = list(hits or [])
    ev = evidence or ""
    flags: list[str] = []

    masked, changed = ans, False
    if _output_mask():
        masked, changed = _mask(ans)
    if changed:
        flags.append("masked")

    leaks = _detect_leaks(hit_list, caller)
    if leaks:
        flags.append("leak_detected")

    coverage = _citation_coverage(ans, hit_list)
    has_cite = bool(_CITE_RE.search(ans))
    grounded_ok = (not ev.strip()) or has_cite
    if ev.strip() and not has_cite:
        flags.append("ungrounded")
    if hit_list and coverage < 0.5:
        flags.append("low_citation_coverage")

    inj = [h for h in _iter_injection_hits(ans)
           if h["kind"] in _OUTPUT_INJECTION_KINDS]
    if inj:
        flags.append("injection_in_output")

    confidence = (0.40
                  + 0.40 * coverage
                  + (0.20 if grounded_ok else 0.0)
                  - (0.30 if leaks else 0.0)
                  - (0.30 if inj else 0.0))
    confidence = round(max(0.0, min(1.0, confidence)), 3)

    _bump("scan_output")
    return {
        "masked_answer": masked,
        "leaks": leaks,
        "citation_coverage": coverage,
        "grounded_ok": bool(grounded_ok),
        "confidence": confidence,
        "flags": flags,
    }


def apply_output_guard(answer: str, caller=None, hits=None, evidence: str = "") -> dict:
    """输出层统一出口：默认只观测（返回原答案 + 护栏结论，不改写）。

    GUARDRAILS_OUTPUT_ENFORCE=1 时返回脱敏后的答案，真正拦截泄密 —— 严格改写是
    opt-in，避免把依赖答案原文的验收断言（M17/M25）悄悄改掉；生产环境开启即生效。
    """
    g = scan_output(answer, caller=caller, hits=hits, evidence=evidence)
    enforce = bool(getattr(settings, "GUARDRAILS_OUTPUT_ENFORCE", False))
    rewritten = bool(enforce and g.get("masked_answer")) and g.get("masked_answer") != answer
    out_answer = g["masked_answer"] if (enforce and g.get("masked_answer")) else answer
    if rewritten:
        observability.observe_guardrail("output")  # 输出泄密改写计数（仅真正改写时）
    return {"answer": out_answer, **g}


# ==================================================================== 4. 事实侧
def grounding_check(answer: str, evidence: str) -> dict:
    """抽答案里的关键数字/金额/日期/条款号，逐个在 evidence 里做字面比对。

    不做任何语义判断：只回答「这个字面片段在不在证据里」。
    """
    ans = answer if isinstance(answer, str) else str(answer or "")
    ev = evidence if isinstance(evidence, str) else str(evidence or "")
    body = _CITE_RE.sub(" ", ans)  # 引用编号不是待核验事实
    tokens: list[str] = []
    seen = set()
    for m in _KEY_TOKEN_RE.finditer(body):
        t = m.group(0).strip()
        if t and t not in seen:
            seen.add(t)
            tokens.append(t)
    norm_ev = _norm(ev)
    unsupported = [t for t in tokens if _norm(t) not in norm_ev]
    checked = len(tokens)
    score = round((checked - len(unsupported)) / checked, 3) if checked else 1.0
    _bump("grounding")
    return {"checked": checked, "unsupported": unsupported, "score": score}


# ==================================================================== 5. 工具侧
def _mcp_allow_set() -> set[str]:
    # 动态取 settings：config 模块可能被 importlib.reload 重绑 settings 对象，
    # 模块顶层 import 会拿到旧对象；此处实时读取，避免重绑后 staleness（见 test_p0_casdoor_consolidation）。
    from app.core.config import settings as _live_settings
    raw = (getattr(_live_settings, "MCP_TOOL_ALLOW", "") or "").strip()
    if not raw:
        return set()
    return {a.strip() for a in raw.split(",") if a.strip()}


def _mcp_tool_allowed(qualified: str, allow: set[str]) -> bool:
    if not allow:
        return False
    if qualified in allow:
        return True
    server = qualified.split("__", 2)[1] if qualified.startswith("mcp__") else ""
    if server and f"mcp__{server}__*" in allow:
        return True
    return "*" in allow


def _guard_mcp_tool(tool: str, args: dict, caller) -> dict:
    """外部 MCP 工具守门：显式授权 + 经 mcp_client 调用，结果原样回传。"""
    if not _tool_auth():
        return {"allowed": False, "reason": "GUARDRAILS_TOOL_AUTH=0"}
    if not mcp_tool_declared(tool):
        return {"allowed": False,
                "reason": f"mcp_tool_not_allowed: {tool}（请在 MCP_TOOL_ALLOW 显式放行）"}
    try:
        from app.services import mcp_client
        res = mcp_client.call_mcp_tool(tool, args)
    except Exception as exc:
        return {"allowed": False, "reason": f"mcp_call_failed: {str(exc)[:160]}"}
    if res.get("is_error"):
        return {"allowed": False,
                "reason": f"mcp_tool_error: {str(res.get('content', ''))[:200]}"}
    return {"allowed": True, "result": {"content": res.get("content", "")}}


def mcp_tool_declared(tool: str) -> bool:
    """P1-3 单一真源：外部 MCP 端点是否已在 MCP_TOOL_ALLOW 显式声明放行。

    沙箱硬闸（sandbox.check）与执行授权（_guard_mcp_tool）共用此判定，
    避免两处各自实现 allow 解析导致行为漂移。支持 mcp__server__tool /
    mcp__server__* / * 三种写法；空白名单一律拒（默认安全）。
    """
    if not isinstance(tool, str) or not tool.startswith("mcp__"):
        return False
    return _mcp_tool_allowed(tool, _mcp_allow_set())


def _is_private_url(url: str) -> bool:
    """字面 IP 用 ipaddress 判私网；域名只拦 localhost / 内网后缀，不做 DNS。"""
    try:
        host = (urlparse(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return False
    if not host:
        return False
    if host == "localhost" or host.endswith(
            (".localhost", ".local", ".internal", ".localdomain")):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False  # 公网域名：不解析 DNS（见模块文档 / 遗留限制）
    return not ip.is_global


def _find_private_url(args: dict) -> str:
    vals = [args.get(k) for k in ("url", "query", "text", "content", "target", "link")]
    for v in vals:
        if isinstance(v, str):
            for u in _URL_RE.findall(v):
                if _is_private_url(u):
                    return u
    u0 = args.get("url")
    if isinstance(u0, str) and u0 and "://" not in u0 and _is_private_url("http://" + u0):
        return u0
    return ""


def _visibility_scope(args: dict) -> tuple[str, list[str]]:
    """确定有效 scope 与涉及的可见性字段；scope 缺省时按部门/属主推断。"""
    keys = [k for k in _VIS_KEYS if k in args and args[k] not in (None, "")]
    if args.get("scope"):
        eff = str(args["scope"])
    elif args.get("dept_id") or args.get("dept"):
        eff = "department"
    elif args.get("owner"):
        eff = "owner"
    else:
        eff = "global"
    return eff, keys


def guard_tool_call(tool: str, args: dict, caller) -> dict:
    """工具调用前置校验：白名单 / 单一过滤点 / 出网脱敏 / 私网拦截 / 审计。"""
    t = tool if isinstance(tool, str) else str(tool or "")
    a = dict(args) if isinstance(args, dict) else {}
    # 外部 MCP 工具（mcp__server__tool）走独立守门分支，不进内部白名单/脱敏逻辑。
    if t.startswith("mcp__"):
        return _guard_mcp_tool(t, a, caller)
    scopes: list[str] = []
    allowed, reason = True, "ok"

    # 出网脱敏：即便最终被拒，也不把原文带出去
    masked_args = dict(a)
    if _output_mask():
        for k in _MASK_ARG_KEYS:
            if isinstance(masked_args.get(k), str):
                masked_args[k], _ = _mask(masked_args[k])

    if t not in _ALLOWED_TOOLS:
        allowed, reason = False, "tool_not_allowed"
    else:
        private = _find_private_url(a) if t == "web_search" else ""
        if private:
            allowed, reason = False, "private_url_forbidden: " + private

        eff_scope, vis_keys = _visibility_scope(a)
        scopes = vis_keys
        if allowed and vis_keys and _tool_auth():
            if caller is None:
                allowed, reason = False, "caller_required"
            else:
                try:
                    ok = visible_fields(
                        eff_scope,
                        _pick(a, "owner"),
                        _pick(a, "dept_id", "dept"),
                        _pick(a, "tenant_id"),
                        caller,
                    )
                except Exception:
                    ok = False
                if not ok:
                    allowed, reason = False, "scope_forbidden"

    # 每次调用留痕（审计后端不可用时由 _m27_store 静默降级）
    try:
        _m27_store.audit_event(
            "tool_check", caller, t,
            {"allowed": allowed, "reason": reason, "scopes": scopes},
        )
    except Exception:
        pass
    if not allowed:
        observability.observe_guardrail("tool")  # 工具调用拦截计数
    _bump("tool_check")
    return {"allowed": bool(allowed), "reason": reason,
            "args": masked_args, "scopes": scopes}


# ==================================================================== 办公62 硬规则（可加载）
# 办公域（Word/Excel/PPT 等办公技能 agent）基线硬规则：forbid / must_include /
# format 三类。规则来自 backend/office62_rules.json（路径可被 EAP_OFFICE62_RULES
# 覆盖）。纯函数 + 模块级缓存；policy() 只回显规则数量与加载状态，绝不回显
# 规则内容或任何密钥（与全局安全内核的回显纪律一致）。
#
# 语义：
#   forbid / format  —— pattern 命中即违规（文本里出现/出现了不合规范的格式）。
#   must_include     —— pattern 未命中即违规（文本必须包含该内容却缺失）。
#   scope 为空 = 全局适用；非空 = 仅当请求 scope 与规则 scope 有交集成效。
def _office62_default_path() -> Path:
    env = (getattr(settings, "EAP_OFFICE62_RULES", "")
           or os.environ.get("EAP_OFFICE62_RULES", ""))
    if env:
        return Path(env)
    # 本文件位于 backend/app/services/guardrails.py → parents[2] 即 backend/
    return Path(__file__).resolve().parents[2] / "office62_rules.json"


_OFFICE62_CACHE: dict[str, Any] = {
    "path": None, "rules": [], "loaded": False, "error": "",
}

_FLAG_MAP = {"i": re.IGNORECASE, "m": re.MULTILINE, "s": re.DOTALL}


def load_office62_rules(path=None, force: bool = False) -> dict:
    """读取并预编译办公62规则；缺项/异常绝不抛错（配置容错约定）。

    缓存按 path 命中，重复调用不重复 IO；force=True 强制重载（测试/热改用）。
    """
    p = Path(path) if path else _office62_default_path()
    cache = _OFFICE62_CACHE
    if not force and cache["path"] == str(p) and cache["loaded"]:
        return {"rules": cache["rules"], "loaded": True,
                "error": cache["error"], "path": str(p)}
    rules: list[dict[str, Any]] = []
    err = ""
    try:
        data = json.loads(Path(p).read_text(encoding="utf-8"))
        raw = data.get("rules", []) if isinstance(data, dict) else []
        for r in raw:
            if not isinstance(r, dict):
                continue
            kind = r.get("kind")
            pat = r.get("pattern")
            if kind not in ("forbid", "must_include", "format") or not pat:
                continue
            flags = 0
            for c in str(r.get("flags", "")):
                flags |= _FLAG_MAP.get(c, 0)
            try:
                rx = re.compile(pat, flags)
            except re.error as e:
                err = f"regex_compile_error:{r.get('id')}:{e}"
                continue
            # applies_to：子模板标签（由 office62_classify_doc 分类得出）；空=不限子模板
            applies_to = list(r.get("applies_to", []) or [])
            # trigger：可选触发式正则，命中才要求必备项（用于「含数据才要求来源 /
            # 含风险才要求免责」等语义门槛）。编译失败则降级为「无触发门槛」而非丢弃规则。
            trigger = r.get("trigger") or ""
            trig_re = None
            if trigger:
                try:
                    trig_re = re.compile(trigger, re.IGNORECASE)
                except re.error as e:
                    err = f"trigger_compile_error:{r.get('id')}:{e}"
                    trig_re = None
            rules.append({
                "id": r.get("id", ""),
                "kind": kind,
                "pattern": pat,
                "severity": r.get("severity", "medium"),
                "scope": list(r.get("scope", []) or []),
                "applies_to": applies_to,
                "trigger": trigger,
                "message": r.get("message", ""),
                "_re": rx,
                "_trigger_re": trig_re,
            })
    except Exception as e:  # 文件缺失/损坏 → 空规则集，不阻断主链路
        err = f"load_error:{e}"
    cache.update(path=str(p), rules=rules, loaded=True, error=err)
    return {"rules": rules, "loaded": True, "error": err, "path": str(p)}


def scan_office62(text: str, scope=None, path=None, doc_types=None) -> dict:
    """办公域硬规则扫描。

    返回 {violations, blocked, severity_counts, count, rules_loaded, load_error}。
    blocked = 存在 high 级违规（业务方可据此拦下办公技能产出物）。
    scope：None/"" 表示全局（仅命中 GLOBAL 规则）；字符串或列表表示办公技能场景，
           会同时匹配 GLOBAL 规则与 scope 有交集的规则。
    path：可选，指定规则文件（覆盖默认/环境变量路径），用于测试或按租户加载。
    doc_types：可选，显式传入文档子模板标签集合（跳过自动分类）；未传则按文本自动
           分类（office62_classify_doc）。must_include 规则仅在「applies_to 为空 或 与
           doc_types 有交集」且「trigger 为空 或 命中文本」时才被要求，缺项才算违规。
    """
    body = text if isinstance(text, str) else str(text or "")
    if scope is None or scope == "":
        scope_set: set[str] = set()
    elif isinstance(scope, str):
        scope_set = {scope}
    else:
        scope_set = set(scope)
    if doc_types is None:
        doc_types = office62_classify_doc(body)
    doc_types_set = set(doc_types) if doc_types else set()
    info = load_office62_rules(path=path)
    rules = info["rules"]
    violations: list[dict[str, str]] = []
    for r in rules:
        r_scope = set(r["scope"])
        if r_scope and not (r_scope & scope_set):
            continue
        if r["kind"] == "must_include":
            # 子模板门槛：applies_to 非空且不与本文档类型相交 → 该必备项不适用
            applies = set(r.get("applies_to") or [])
            if applies and not (applies & doc_types_set):
                continue
            # 触发式门槛：trigger 非空且未命中文本 → 该必备项不适用
            trig = r.get("_trigger_re")
            if trig is not None and not trig.search(body):
                continue
            m = r["_re"].search(body)
            hit = not m
        else:
            m = r["_re"].search(body)
            hit = bool(m)
        if hit:
            excerpt = (m.group(0) if m else "")
            violations.append({
                "id": r["id"], "kind": r["kind"], "severity": r["severity"],
                "message": r["message"], "excerpt": excerpt[:80],
            })
    sev = {"high": 0, "medium": 0, "low": 0}
    for v in violations:
        sev[v["severity"]] = sev.get(v["severity"], 0) + 1
    blocked = sev["high"] > 0
    return {"violations": violations, "blocked": bool(blocked),
            "severity_counts": sev, "count": len(violations),
            "rules_loaded": len(rules), "load_error": info["error"]}


_SEV_RANK = {"low": 1, "medium": 2, "high": 3}


def scan_office62_blob(texts, scope=None, path=None) -> dict:
    """对多个文本片段（答案 + 文件正文等）合并扫描，去重并按最高严重度合并。

    返回结构与 scan_office62 一致；便于把「答案文本」与「生成的 docx/pptx/xlsx
    正文」一起过同一套硬规则，避免只看 answer 漏掉文件里的红线。
    """
    merged: dict[str, dict[str, str]] = {}
    for t in texts:
        if not t:
            continue
        res = scan_office62(t, scope=scope, path=path)
        for v in res["violations"]:
            cur = merged.get(v["id"])
            if cur is None or _SEV_RANK.get(v["severity"], 0) > _SEV_RANK.get(cur["severity"], 0):
                merged[v["id"]] = v
    violations = list(merged.values())
    sev = {"high": 0, "medium": 0, "low": 0}
    for v in violations:
        sev[v["severity"]] = sev.get(v["severity"], 0) + 1
    blocked = sev["high"] > 0
    info = load_office62_rules(path=path)
    return {"violations": violations, "blocked": bool(blocked),
            "severity_counts": sev, "count": len(violations),
            "rules_loaded": len(info["rules"]), "load_error": info["error"]}


def extract_office_text(data: bytes, ext: str) -> str:
    """从办公文件字节抽取纯文本，供办公62文件正文扫描复用。

    懒加载 python-docx / python-pptx / openpyxl；任一库缺失或解析失败均返回空串
    （调用方据此跳过文件正文扫描，不影响主链路）。
    """
    ext = (ext or "").lower().lstrip(".")
    try:
        if ext == "docx":
            from docx import Document
            import io
            doc = Document(io.BytesIO(data))
            return "\n".join(p.text for p in doc.paragraphs)
        if ext == "pptx":
            from pptx import Presentation
            import io
            prs = Presentation(io.BytesIO(data))
            out: list[str] = []
            for slide in prs.slides:
                for shape in slide.shapes:
                    if shape.has_text_frame:
                        out.append(shape.text_frame.text)
            return "\n".join(out)
        if ext in ("xlsx", "xlsm"):
            from openpyxl import load_workbook
            import io
            wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
            out = []
            for ws in wb.worksheets:
                for row in ws.iter_rows(values_only=True):
                    out.append(" ".join("" if c is None else str(c) for c in row))
            return "\n".join(out)
    except Exception:
        return ""
    return ""


# 由技能默认产出格式推断办公62 scope；非办公产出格式返回 None（仅命中 GLOBAL 规则）。
_OFFICE62_FORMAT_SCOPE = {"docx": "office_doc", "xlsx": "office_sheet",
                          "pptx": "office_ppt"}


# 文档子模板标签 → 触发正则。office62_classify_doc 据此把文本归类到一组子模板标签，
# 供 must_include 的 applies_to 门槛使用。标签分两类：
#   结构型（合同/纪要/报告/财务/计划/PPT）—— 由文档形态关键词判定；
#   语义型（含数据/风险/对比/外部引用/个人信息/长篇/下载）—— 由内容是否触发某类必备项判定。
# 这是启发式分类（非硬过滤）：标签过宽最多让某必备项「被要求」，若该必备项本身已满足则无违规。
_OFFICE62_DOC_TAGS = {
    "contract": r"(?i)(合同|协议书?|本协议|缔约|双方约定|签署方|合同条款)",
    "meeting": r"(?i)(会议纪要|会议记录|出席|列席|主持人|会议议程|与会人员|会议时间|会议地点)",
    "report": r"(?i)(报告|调研|评估|汇报材料|情况说明|分析总结|总结如下|工作报告|分析报告)",
    "financial": r"(?i)(财务|测算|预算|营收|利润|成本|现金流|资产负债表|损益|收益率|毛利率)",
    "plan": r"(?i)(项目计划|实施方案|排期|里程碑|进度表|工作计划|行动方案|时间表|工作排期)",
    "ppt": r"(?i)(幻灯片|演示文稿|演讲稿|ppt|slide)",
    "data_rich": r"(?i)(数据|统计|金额|营收|利润|同比|环比|图表|占比|增长率|万元|亿元|百分比|%)",
    "comparison": r"(?i)(对比|比较| versus | vs\.? |相较|差距|优劣势|对标)",
    "external_ref": r"(?i)(参考|参见|引用|据.{0,6}(报道|研究|调查|显示|统计)|文献|出处说明)",
    "personal_info": r"(?i)(处理|收集|使用|共享).{0,12}(个人信息|隐私数据|用户数据|敏感信息)",
    "long_doc": r"(?i)(目录|章节|第一章|一、|第.{1,3}章|全文|前言|引言)",
    "download": r"(?i)(下载|附件|详见|见附件|点击下载|附表下载)",
}


def office62_classify_doc(text: str) -> set[str]:
    """把办公文本归类到一组子模板标签（见 _OFFICE62_DOC_TAGS）。

    空文本返回空集合。任何标签正则异常均跳过该标签（不阻断主链路）。该结果是
    must_include applies_to 门槛的输入；分类越准，条件化必备项越贴合实际子模板。
    """
    body = text if isinstance(text, str) else str(text or "")
    tags: set[str] = set()
    if not body:
        return tags
    for tag, pat in _OFFICE62_DOC_TAGS.items():
        try:
            if re.search(pat, body):
                tags.add(tag)
        except Exception:
            continue
    return tags


def office62_scope_for_agent(agent: dict) -> str | None:
    """由技能默认产出格式推断办公62 scope；非办公产出格式返回 None。

    覆盖全部办公类技能（公共库 + 办公角色包），无需硬编码白名单。
    返回 None 时 scan_office62 仍会应用 GLOBAL（scope 空）规则。
    """
    if not isinstance(agent, dict):
        return None
    fmt = str(agent.get("default_format") or "").lower()
    return _OFFICE62_FORMAT_SCOPE.get(fmt)


# ==================================================================== 6/7. 回显 / 复位
def policy() -> dict:
    """回显开关与规则数量，绝不回显任何密钥。"""
    allow = _allowlist()
    with _STATS_LOCK:
        stats = dict(_STATS)
    return {
        "enabled": _enabled(),
        "strict": _strict(),
        "tool_auth": _tool_auth(),
        "output_mask": _output_mask(),
        "max_injection_hits": _max_hits(),
        "source_allowlist": allow,
        "source_allowlist_count": len(allow),
        "injection_rules": len(_INJECTION_RULES),
        "masking_rules": len(_MASK_RULES),
        "allowed_tools": sorted(_ALLOWED_TOOLS),
        "allowed_tools_count": len(_ALLOWED_TOOLS),
        "office62_rules": len(load_office62_rules()["rules"]),
        "office62_loaded": _OFFICE62_CACHE["loaded"],
        "office62_error": (_OFFICE62_CACHE["error"][:120]
                           if _OFFICE62_CACHE["error"] else ""),
        "stats": stats,
    }


def reset_state() -> None:
    """清空运行时计数器（规则与配置是静态常量，不在此列）。"""
    with _STATS_LOCK:
        for k in _STATS:
            _STATS[k] = 0
