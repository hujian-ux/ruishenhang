"""第18章 · 单一过滤点（core）。

所有读/写/外发均经此一处过滤，下游服务信任 caller 上下文，不再各自判权。
可见性规则（多人知识库 scope/owner/dept）：
  - global    : 登录即可见（含 employee）
  - department: 仅 caller.deptId == doc.dept
  - owner     : 仅 doc.owner == caller.userId（或平台管理员）
  - personal  : 仅 doc.owner == caller.userId
跨租户一律不可见。越权内容在召回阶段即被剔除，不进入大模型上下文。
"""
from app.models.caller import CallerContext


def visible(meta: dict, caller: CallerContext) -> bool:
    """判定单条知识/数据对 caller 是否可见。meta 需含 tenant_id/scope/owner/dept。"""
    if meta.get("tenant_id") and meta["tenant_id"] != caller.tenantId:
        return False
    scope = meta.get("scope")
    if scope == "global":
        return caller.role in ("employee", "dept_admin", "platform_admin")
    if scope == "department":
        return meta.get("dept") == caller.deptId
    if scope == "owner":
        return meta.get("owner") == caller.userId or caller.role == "platform_admin"
    if scope == "personal":
        return meta.get("owner") == caller.userId
    return False


def single_filter(items: list[dict], caller: CallerContext) -> tuple[list[dict], int]:
    """单一过滤点：裁切可见性，返回 (可见项, 被过滤数)。不绕过。

    兼容两种结构：显式 meta 子键（如评测集）或顶层携带 scope/dept 的召回结果。
    """
    visible_items = []
    for it in items:
        meta = it.get("meta") or it
        if visible(meta, caller):
            visible_items.append(it)
    filtered_out = len(items) - len(visible_items)
    return visible_items, filtered_out
