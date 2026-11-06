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
import re
import threading
from typing import Any
from urllib.parse import urlparse

from app.core.config import settings
from app.core.filter import visible_fields
from app.services import _m27_store

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

_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
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
_ALLOWED_TOOLS = {"rag_retrieve", "web_search", "file_read", "file_write", "kb_write"}
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
def scan_input(text: str) -> dict:
    """提示注入 / 越狱扫描。

    blocked：命中规则数 >= GUARDRAILS_MAX_INJECTION_HITS 或开启 STRICT。
    clean_text：清控制字符（保留 \\t\\n\\r）+ 截断 4000。
    """
    raw = text if isinstance(text, str) else str(text or "")
    cleaned = _CONTROL_RE.sub("", raw)
    hits = _iter_injection_hits(cleaned)
    max_hits = _max_hits()
    blocked = len(hits) >= max_hits or _strict()
    risk = round(min(1.0, len(hits) / max_hits), 3)
    if blocked and risk < 0.5:
        risk = 0.5
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
    _bump("tool_check")
    return {"allowed": bool(allowed), "reason": reason,
            "args": masked_args, "scopes": scopes}


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
        "stats": stats,
    }


def reset_state() -> None:
    """清空运行时计数器（规则与配置是静态常量，不在此列）。"""
    with _STATS_LOCK:
        for k in _STATS:
            _STATS[k] = 0
