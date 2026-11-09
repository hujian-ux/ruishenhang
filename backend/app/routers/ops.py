"""M27 · 运维路由（prefix=/ops）：指标 / 健康 / 告警 / 就绪度 / 审计导出 / 自助删数据。

全部端点需登录（Depends(get_caller)）；成功响应统一 {"code":0,"data":{...}}。
/ops/metrics 例外：text/plain 的 Prometheus 文本。
/ops/audit-export 与 /ops/me/data 的结果按 caller 收敛（复用既有 RBAC / userId）。
"""
from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse
import os

from app.core.config import settings
from app.deps import get_caller
from app.models.caller import CallerContext
from app.services import agent_store, fewshot, observability, office_hard_rules, runtime_config

router = APIRouter(prefix="/ops", tags=["ops"])


@router.get("/metrics", response_class=PlainTextResponse)
def metrics(caller: CallerContext = Depends(get_caller)):
    """Prometheus exposition（text/plain）；仍需登录。"""
    return PlainTextResponse(content=observability.prometheus_text(), media_type="text/plain")


@router.get("/health")
def health(caller: CallerContext = Depends(get_caller)):
    return {"code": 0, "data": observability.health(caller)}


@router.get("/alerts")
def get_alerts(caller: CallerContext = Depends(get_caller)):
    items = observability.alerts()
    h = observability.health(caller)
    return {
        "code": 0,
        "data": {
            "alerts": items,
            "count": len(items),
            # 样本不足时 alerts 为空，说明落在这里（reason），不误报也不失语
            "insufficient_data": h["insufficient_data"],
        },
    }


@router.get("/readiness")
def readiness(caller: CallerContext = Depends(get_caller)):
    return {"code": 0, "data": observability.readiness()}


def _apply_env(updates: dict):
    """把配置写入 os.environ（即时影响 readiness），并同步到 settings 单例已存在的属性。

    M49 起实现抽到 services.runtime_config，模型配置端点复用同一套写入逻辑。
    """
    return runtime_config.apply(updates)


def _persist_env(updates: dict):
    """把配置落盘到 backend/.env.local（gitignore），重启后由 config._load_env_files 读回。"""
    return runtime_config.persist(updates)


@router.post("/configure")
def configure_external_input(payload: dict, caller: CallerContext = Depends(get_caller)):
    """M27 · 平台运营台「外部输入就绪度」在线配置入口（仅 platform_admin，dev/demo 用）。

    接收 {key, fields}，按就绪项类型把外部输入写入 os.environ（即时翻绿）并落盘
    backend/.env.local（重启仍生效）。生产环境应使用密钥管理服务，不应经此端点落盘明文密钥。
    支持：llm_key（LLM_API_KEY + 可选 base_url/model）、search_key（官方搜索 key + provider）、
    oidc_idp（OIDC_ENABLED=1 + issuer/audience）、real_corpus（SEED_DEMO_CORPUS 模式切换）。
    """
    if caller.role != "platform_admin":
        return {"code": 3, "msg": "需要平台管理员权限"}
    key = (payload or {}).get("key")
    fields = (payload or {}).get("fields") or {}
    if key not in ("llm_key", "search_key", "oidc_idp", "real_corpus"):
        return {"code": 2, "msg": "未知配置项"}
    updates: dict = {}
    if key == "llm_key":
        ak = (fields.get("api_key") or "").strip()
        if not ak:
            return {"code": 2, "msg": "api_key 必填"}
        updates["LLM_API_KEY"] = ak
        if (fields.get("provider") or "").strip() in ("direct", "litellm"):
            updates["LLM_PROVIDER"] = fields["provider"].strip()
        if (fields.get("vendor") or "").strip():
            updates["LLM_VENDOR"] = fields["vendor"].strip()
        if (fields.get("base_url") or "").strip():
            updates["LLM_BASE_URL"] = fields["base_url"].strip()
        if (fields.get("model") or "").strip():
            # 键名必须是 LLM_DEFAULT_MODEL：网关读的是它，写 LLM_MODEL 等于没配
            # （M27 遗留 bug，M49 修）。同时把成本路由两档对齐，否则路由会把请求
            # 改写回旧的 glm-4.6/glm-4-flash，用别家 key 直接 401。
            updates["LLM_DEFAULT_MODEL"] = fields["model"].strip()
            updates["ROUTING_SMALL_MODEL"] = fields["model"].strip()
            updates["ROUTING_LARGE_MODEL"] = fields["model"].strip()
    elif key == "search_key":
        prov = (fields.get("provider") or "tavily").strip().lower()
        ak = (fields.get("api_key") or "").strip()
        if not ak:
            return {"code": 2, "msg": "api_key 必填"}
        prov_map = {"tavily": "TAVILY_API_KEY", "bocha": "BOCHA_API_KEY",
                    "serper": "SERPER_API_KEY", "bing": "BING_SEARCH_KEY"}
        if prov not in prov_map:
            return {"code": 2, "msg": "provider 须为 tavily/bocha/serper/bing"}
        updates[prov_map[prov]] = ak
        updates["WEB_SEARCH_PROVIDER"] = prov
        updates["WEB_SEARCH_ENABLED"] = "1"
    elif key == "oidc_idp":
        iss = (fields.get("issuer") or "").strip()
        aud = (fields.get("audience") or "").strip()
        if not iss or not aud:
            return {"code": 2, "msg": "issuer 与 audience 必填"}
        updates["OIDC_ENABLED"] = "1"
        updates["OIDC_ISSUER"] = iss
        updates["OIDC_AUDIENCE"] = aud
    elif key == "real_corpus":
        enabled = bool(fields.get("enabled"))
        # 启用真实语料模式 = 关闭演示语料（SEED_DEMO_CORPUS=0）
        updates["SEED_DEMO_CORPUS"] = "0" if enabled else "1"
    _apply_env(updates)
    _persist_env(updates)
    return {"code": 0, "data": observability.readiness()}


@router.get("/audit-export")
def audit_export(caller: CallerContext = Depends(get_caller)):
    # 只读取，范围由 audit.get_logs(caller) 的既有 RBAC 收敛
    return {"code": 0, "data": observability.audit_export(caller)}


@router.delete("/me/data")
def delete_my_data(caller: CallerContext = Depends(get_caller)):
    # 只删 caller.userId 名下数据；其它 store 的数据列 manual_review，不谎称全删
    return {"code": 0, "data": observability.delete_my_data(caller)}


@router.post("/backfill-hard-rules")
def backfill_hard_rules(caller: CallerContext = Depends(get_caller)):
    """M15/M25 P1 · 一键补齐存量业务包的平台硬规则（仅平台管理员）。

    对未含平台硬规则头的业务包追加上下文一致的红线块、并刷新 hard_rules_version；
    已含的不动（幂等）。新导入的业务包由 import_solutions 在解析时自动带上，
    本端点只负责「历史存量」一次性自愈。
    """
    if caller.role != "platform_admin":
        return {"code": 3, "msg": "需要平台管理员权限"}
    return {"code": 0, "data": agent_store.backfill_hard_rules()}


@router.post("/backfill-office-rules")
def backfill_office_rules(caller: CallerContext = Depends(get_caller)):
    """M15/M25 P1 · 一键补齐办公类业务包的「办公域」硬规则（仅平台管理员）。

    在平台红线之外，给办公类（category_cn 含「办公」）业务包追加办公域特有红线
    （财务不编造、合同带版本、报表带单位、PII 脱敏、排期标待确认、合规建议咨询）；
    幂等，已含不动。
    """
    if caller.role != "platform_admin":
        return {"code": 3, "msg": "需要平台管理员权限"}
    return {"code": 0, "data": office_hard_rules.backfill_office_hard_rules()}


@router.post("/build-fewshot")
def build_fewshot(caller: CallerContext = Depends(get_caller)):
    """M15/M17 P1 · 把方案库 166 个 xlsx 交付物样例灌成 few-shot（仅平台管理员）。

    遍历 SOLUTIONS_ROOT，按 solution_dir 匹配运行中的业务包，把（原始数据 ↔ 交付物）
    成对 xlsx 压成紧凑样例块挂到 agent（幂等、可重跑）。FEWSHOT_ENABLED=1 时运行时
    把样例拼进对应技能的 system_prompt，提升产出物格式一致性。
    """
    if caller.role != "platform_admin":
        return {"code": 3, "msg": "需要平台管理员权限"}
    # 默认指向仓库内置 115 包源（backend/solutions）；可用 SOLUTIONS_ROOT 环境变量覆盖。
    _repo_root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "solutions")
    root = settings.SOLUTIONS_ROOT or os.environ.get("SOLUTIONS_ROOT") or _repo_root
    pairs = fewshot.scan_solution_pairs(root)
    by_dir = {p["solution_dir"]: p for p in pairs}
    attached = 0
    skipped_no_pair = 0
    for a in list(agent_store._AGENTS.values()):
        if a.get("builtin") or a.get("category") == "public":
            continue
        src = a.get("source_path") or a.get("solution_dir") or ""
        # 向上回退到最近的含 xlsx 的祖先目录（板块04 skill.md 比 xlsx 深两级）
        key = _resolve_solution_dir(src, by_dir)
        p = by_dir.get(key)
        if not p:
            skipped_no_pair += 1
            continue
        block = fewshot.build_fewshot_block(p["input_path"], p["output_path"])
        fewshot.attach_fewshot(a, block)
        agent_store.save(a)
        attached += 1
    return {"code": 0, "data": {"pairs_found": len(pairs), "attached": attached,
                                "skipped_no_pair": skipped_no_pair}}


def _resolve_solution_dir(src: str, by_dir: dict) -> str:
    """把 agent 的 source_path / solution_dir 解析成 by_dir 里存在的键。

    板块 01-03 的 skill.md 与 xlsx 同目录；板块 04 的 skill.md 嵌套更深，
    xlsx 在其上两级。逐层向上回退到「by_dir 中已知含 xlsx 的目录」，
    使 115 个业务包都能匹配到交付物（原精确相等只命中 9 个）。
    路径统一为正斜杠（scan_solution_pairs 与 import_solutions 均如此规范化）。
    """
    key = (src or "").replace("\\", "/")
    while key:
        if key in by_dir:
            return key
        parent = key.rsplit("/", 1)[0] if "/" in key else ""
        if parent == key:
            break
        key = parent
    return ""
