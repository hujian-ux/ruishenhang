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


def visible_fields(scope: str | None, owner: str | None, dept_id: str | None,
                   tenant_id: str | None, caller: CallerContext) -> bool:
    """判定内核（**第18章的单一定义**）：按字段判一条内容对 caller 是否可见。

    为什么单拆这一层（M10）：规模化后「聚合完再过滤」会把候选窗口吃光——
    三个部门各有同题文档时，top-200 子块可能全属于别的部门，过滤后一条不剩，
    用户看到「知识库没有这条制度」。修法是在**候选阶段**就按可见性预筛，
    但判权逻辑必须还是这一份，所以内核抽出来给两处调用（预筛 + 兜底），
    不是新增第二个过滤点。
    """
    if tenant_id and tenant_id != caller.tenantId:
        return False
    if scope == "global":
        return caller.role in ("employee", "dept_admin", "platform_admin")
    if scope == "department":
        return dept_id == caller.deptId
    if scope == "owner":
        return owner == caller.userId or caller.role == "platform_admin"
    if scope == "personal":
        return owner == caller.userId
    return False


def visible(meta: dict, caller: CallerContext) -> bool:
    """判定单条知识/数据对 caller 是否可见。meta 需含 tenant_id/scope/owner/dept。"""
    return visible_fields(meta.get("scope"), meta.get("owner"), meta.get("dept"),
                          meta.get("tenant_id"), caller)


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
