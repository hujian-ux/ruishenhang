"""M19 · 职业维度（Role Workspace）。

为什么需要这一层：
    115 个技能包原本按「行业」分（制造业 21 / 零售 15 / 跨境电商 17 / 办公角色 62）。
    但员工是**按岗位找工具**的：一个财务不会先想"我属于制造业还是零售"，他只想知道
    "报销单怎么预审、账龄怎么分析"。行业视角对老板有用，对岗位员工是噪音。

设计取舍：
    1. **职业是横切标签，不替代行业**。制造业的「对账与开票核对」既是制造业包，也是
       财务岗的包。故一个包可挂多个职业，第一个为**主职业**（决定默认归属）。
    2. **显式映射表，不用关键词推断**。关键词会把「经营周报自动拼表」判成"运营"、
       把「异常指标归因分析」判成"数据"，而它们实际都是财务/经营分析岗的活。
       115 条写死虽笨，但可审计、可 review、不会随描述文案改动而漂移。
    3. **每个职业给 4 个 core**。选中职业后先给这 4 个（新人开箱即用），其余折叠在
       「全部 N 个」里——避免一上来就摊 20 个让人挑花眼。
    4. 映射表未命中的新包走**回退推断**（sub_function → 职业），不抛错、不丢失。

新增技能包时：在 PACK_ROLES 里补一行即可；漏了也不会崩，只会落到回退或"其他"。
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------- 职业枚举
# 顺序即前端展示顺序（先高频通用岗，后行业专岗，管理者放最后——它是横向视角）
ROLES: List[Dict[str, str]] = [
    {"id": "finance", "name": "财务", "initial": "财",
     "desc": "报销、对账、税务、报表、资金", "color": "#c2410c"},
    {"id": "hr", "name": "人事", "initial": "人",
     "desc": "招聘、绩效、薪酬、入离职", "color": "#7c3aed"},
    {"id": "sales", "name": "销售", "initial": "销",
     "desc": "报价、投标、客户、商机跟进", "color": "#0369a1"},
    {"id": "admin", "name": "行政", "initial": "行",
     "desc": "制度、资产、采购比价、会务", "color": "#0f766e"},
    {"id": "procure", "name": "采购供应链", "initial": "采",
     "desc": "询价、跟催、齐套、库存", "color": "#b45309"},
    {"id": "plan", "name": "生产计划", "initial": "产",
     "desc": "排产、交期、开工、交付风险", "color": "#4338ca"},
    {"id": "quality", "name": "品质工程", "initial": "质",
     "desc": "异常分析、SOP、ECN、版本一致性", "color": "#be123c"},
    {"id": "marketing", "name": "市场运营", "initial": "市",
     "desc": "会员、内容、活动、私域触达", "color": "#a21caf"},
    {"id": "ecom", "name": "电商运营", "initial": "电",
     "desc": "选品、上架、广告、履约申诉", "color": "#047857"},
    {"id": "service", "name": "客服", "initial": "客",
     "desc": "标准应答、投诉、FAQ、VOC", "color": "#0891b2"},
    {"id": "manager", "name": "管理者", "initial": "管",
     "desc": "经营复盘、异常归因、驾驶舱", "color": "#334155"},
]

ROLE_NAME = {r["id"]: r["name"] for r in ROLES}

# ---------------------------------------------------------------- 包 → 职业
# 键为 skill.md frontmatter 的 name 字段（注意与目录名可能不同）
# 值为职业 id 列表，**第一个是主职业**
PACK_ROLES: Dict[str, List[str]] = {
    # ===== 制造业 21 =====
    "图纸询价智能报价": ["sales"],
    "经营复盘": ["manager", "finance"],
    "BOM导入·工程基线核对": ["quality"],
    "SOP生成与校验": ["quality"],
    "订单接单评审-急单可行性": ["plan", "sales"],
    "齐套与缺料预警": ["procure", "plan"],
    "品质异常分析（8D 根因追溯）": ["quality"],
    "对账与开票核对": ["finance"],
    "客户单据转系统订单": ["sales"],
    "合同条款确认": ["sales"],
    "异常分级与升级归口": ["manager"],
    "开工条件核查": ["plan"],
    "工程变更管理ECN全流程管控": ["quality"],
    "版本一致性巡检": ["quality"],
    "交期平衡与产销协同": ["plan"],
    "排产与插单调整": ["plan"],
    "执行偏差追踪与补产": ["plan"],
    "库存结构与呆滞料复盘": ["procure"],
    "来料跟催": ["procure"],
    "交付风险评估（出货前五档处置决策）": ["plan", "sales"],
    "利润风险提示": ["finance", "manager"],
    # ===== 零售 15 =====
    "社群周内容包与分层话术": ["marketing"],
    "沉睡会员识别与唤醒": ["marketing"],
    "RFM会员分层与差异化触达": ["marketing"],
    "周报月报自动化": ["manager"],
    "异常指标归因分析": ["manager"],
    "年度提案与新客Pitch": ["sales"],
    "一稿多尺寸海报适配": ["marketing"],
    "短视频脚本与分镜": ["marketing"],
    "多版本推文与A/B变体": ["marketing"],
    "活动落地页与H5生成": ["marketing"],
    "内部效率自动化工作流搭建": ["admin"],
    "经营驾驶舱与多视图可视化": ["manager"],
    "FAQ知识库沉淀": ["service"],
    "场景化标准应答": ["service"],
    "投诉与敏感应对": ["service"],
    # ===== 跨境电商 17 =====
    "经营利润异常会诊与行动审批": ["manager", "finance"],
    "重大异常战情室": ["manager", "ecom"],
    "账号申诉证据包": ["ecom"],
    "商品合规材料包": ["ecom"],
    "Amazon及多平台商品批量上架与发布校验": ["ecom"],
    "AI选品机会发现与多维验证": ["ecom", "procure"],
    "新品全口径利润立项": ["finance", "ecom"],
    "FBA赔偿与异常货件损失追回": ["ecom", "finance"],
    "PPC搜索词与真实利润诊断": ["ecom"],
    "广告—库存—利润联动预警": ["ecom"],
    "商品详情图与A+内容批量生成": ["ecom", "marketing"],
    "内容—达人—广告—退款后净ROI": ["marketing", "ecom"],
    "voc-quality-closed-loop": ["service", "quality"],
    "chargeback-evidence-pack": ["service", "ecom"],
    "多平台对账差异证据包": ["finance"],
    "平台费用异常审计": ["finance"],
    "供应商询价与样品对比": ["procure"],
    # ===== 办公角色 62 =====
    "JD 拆解与画像": ["hr"],
    "主管赋能方案": ["hr"],
    "人才九宫格": ["hr"],
    "入离职手续办理": ["hr"],
    "内宣通稿生成": ["hr", "admin"],
    "合规文书草拟": ["hr"],
    "员工证明开具": ["hr"],
    "培训需求分析": ["hr"],
    "批量简历筛选排序": ["hr"],
    "招聘漏斗分析": ["hr"],
    "文化活动策划": ["hr"],
    "月度薪资核算": ["hr", "finance"],
    "用工合规排查": ["hr"],
    "离职面谈分析": ["hr"],
    "绩效评估校准": ["hr"],
    "薪酬竞争力对标": ["hr"],
    "课件生成": ["hr"],
    "调薪测算": ["hr", "finance"],
    "部门人力盘点": ["hr", "manager"],
    "面试评估汇总": ["hr"],
    "制度冲突与失效引用排查": ["admin"],
    "制度问答知识库（带出处）": ["admin"],
    "多供应商报价逐项拆解比价": ["admin", "procure"],
    "年度合规日历与到期预警台账构建": ["admin"],
    "框架合同与实际结算偏离核查": ["admin", "finance"],
    "盘点差异归因与盘点报告生成": ["admin"],
    "离职资产与授权回收核查": ["admin", "hr"],
    "续租与换租决策包": ["admin"],
    "行政月报与量化口径生成": ["admin"],
    "行政费用异常归因（量价分离）": ["admin", "finance"],
    "行政预算编制依据包": ["admin", "finance"],
    "资产台账清洗与幽灵资产识别": ["admin"],
    "跨会议待办闭环追踪": ["admin"],
    "重要会议前的资料预备包": ["admin"],
    "雇主责任险与团意险方案横向比对": ["admin"],
    "凭证全量稽核扫描": ["finance"],
    "应收账款账龄分析与逾期预警": ["finance"],
    "往来对账差异清单": ["finance"],
    "报销单合规预审": ["finance"],
    "税收优惠体检与漏享扫描": ["finance"],
    "纳税日历与逾期预警": ["finance"],
    "纳税申报前风险清单": ["finance"],
    "经营周报自动拼表": ["finance", "manager"],
    "费用异常波动分析": ["finance"],
    "资金日报自动拼表": ["finance"],
    "银行余额调节表自动编制": ["finance"],
    "预算编制数据收集": ["finance"],
    "丢单复盘归档": ["sales"],
    "历史报价归集与口径核对": ["sales"],
    "商机健康度体检": ["sales"],
    "客户交接包": ["sales"],
    "客户信用速查卡": ["sales", "finance"],
    "展会名片分级与跟进清单": ["sales"],
    "投标废标体检": ["sales"],
    "投标资料一键组卷": ["sales"],
    "报价测算表生成": ["sales"],
    "招标文件智能解读": ["sales"],
    "拜访前作战简报": ["sales"],
    "方案初稿组装（带出处）": ["sales"],
    "语音转结构化跟进记录": ["sales"],
    "资质证书台账与到期预警": ["sales", "admin"],
    "需求澄清纪要": ["sales"],
}

# ---------------------------------------------------------------- 核心包（开箱即用）
# 每个职业 4 个：覆盖该岗最高频、最不需要额外数据的场景
ROLE_CORE: Dict[str, List[str]] = {
    "finance": ["费用异常波动分析", "报销单合规预审",
                "应收账款账龄分析与逾期预警", "经营周报自动拼表"],
    "hr": ["JD 拆解与画像", "批量简历筛选排序", "月度薪资核算", "绩效评估校准"],
    "sales": ["客户信用速查卡", "报价测算表生成", "招标文件智能解读", "拜访前作战简报"],
    "admin": ["行政费用异常归因（量价分离）", "制度问答知识库（带出处）",
              "跨会议待办闭环追踪", "资产台账清洗与幽灵资产识别"],
    "procure": ["齐套与缺料预警", "来料跟催", "库存结构与呆滞料复盘", "供应商询价与样品对比"],
    "plan": ["排产与插单调整", "交期平衡与产销协同", "开工条件核查",
             "交付风险评估（出货前五档处置决策）"],
    "quality": ["品质异常分析（8D 根因追溯）", "SOP生成与校验",
                "工程变更管理ECN全流程管控", "版本一致性巡检"],
    "marketing": ["社群周内容包与分层话术", "RFM会员分层与差异化触达",
                  "多版本推文与A/B变体", "一稿多尺寸海报适配"],
    "ecom": ["AI选品机会发现与多维验证", "Amazon及多平台商品批量上架与发布校验",
             "PPC搜索词与真实利润诊断", "FBA赔偿与异常货件损失追回"],
    "service": ["场景化标准应答", "投诉与敏感应对", "FAQ知识库沉淀", "voc-quality-closed-loop"],
    "manager": ["经营复盘", "异常指标归因分析",
                "经营驾驶舱与多视图可视化", "经营利润异常会诊与行动审批"],
}

# 回退推断：办公角色板块的 sub_function 天然就是职业
_SUB_ROLE = {"HR": "hr", "行政": "admin", "财务": "finance", "销售": "sales"}
# 行业板块无 sub_function，整体兜到一个最可能的职业，避免出现"未分类"
_BOARD_ROLE = {"制造业": "quality", "零售": "marketing", "跨境电商": "ecom"}

UNCLASSIFIED = ""


def roles_for(name: str, sub_function: str = "", category_cn: str = "") -> List[str]:
    """返回该包的职业列表（首个为主职业）。新包未登记时走回退，不抛错。"""
    hit = PACK_ROLES.get(name)
    if hit:
        return list(hit)
    if sub_function in _SUB_ROLE:
        return [_SUB_ROLE[sub_function]]
    if category_cn in _BOARD_ROLE:
        return [_BOARD_ROLE[category_cn]]
    return [UNCLASSIFIED]


def primary_role(name: str, sub_function: str = "", category_cn: str = "") -> str:
    return roles_for(name, sub_function, category_cn)[0]


def annotate(agent: Dict) -> Dict:
    """就地补 role_tags / role_primary / role_core（已有值不覆盖）。

    挂在 agent_store.save 上，使 import / register / 脚本直连三种入口都自动带上，
    不需要每个调用方各写一遍。
    """
    # 公共库不挂职业标签：文案/PPT/表格是每个岗位的活，挂给谁都会让计数重复
    if agent.get("public") or agent.get("category") == "public":
        return agent
    if "role_tags" not in agent:
        roles = roles_for(agent.get("name", ""), agent.get("sub_function") or "",
                          agent.get("category_cn") or "")
        agent["role_tags"] = roles
        agent.setdefault("role_primary", roles[0])
        agent.setdefault("role_core", is_core(agent.get("name", "")))
    return agent


def is_core(name: str) -> bool:
    return any(name in v for v in ROLE_CORE.values())


def validate() -> List[str]:
    """自检：核心包必须都在映射表里且归属该职业；返回问题列表（空=健康）。"""
    problems: List[str] = []
    for rid, packs in ROLE_CORE.items():
        for p in packs:
            if p not in PACK_ROLES:
                problems.append(f"{rid} 的核心包未在映射表：{p}")
            elif rid not in PACK_ROLES[p]:
                problems.append(f"{rid} 的核心包归属错误：{p} → {PACK_ROLES[p]}")
    for role_id in {r for v in PACK_ROLES.values() for r in v}:
        if role_id not in ROLE_NAME:
            problems.append(f"未知职业 id：{role_id}")
    return problems


def catalog(agents: List[Dict]) -> List[Dict]:
    """把（已按可见域过滤的）技能包聚成职业目录。

    入参是 agent dict（需含 name / agent_id / status / description），
    返回按 ROLES 顺序排列的职业节点，只保留有包的职业。
    """
    bucket: Dict[str, List[Dict]] = {r["id"]: [] for r in ROLES}
    for a in agents:
        # 内置通用助手与公共库（办公基础技能）都不是业务技能包，不进 115 口径
        if a.get("builtin") or a.get("public") or a.get("category") == "public":
            continue
        rid = a.get("role_primary") or primary_role(
            a.get("name", ""), a.get("sub_function", ""), a.get("category_cn", ""))
        if not rid or rid not in bucket:
            continue
        core_list = ROLE_CORE.get(rid, [])
        bucket[rid].append({
            "agent_id": a["agent_id"],
            "name": a.get("name"),
            "description": (a.get("description") or "")[:120],
            "status": a.get("status"),
            "adapter_type": a.get("adapter_type"),
            "category_cn": a.get("category_cn"),
            "sub_function": a.get("sub_function"),
            "role_tags": a.get("role_tags") or [rid],
            "core": a.get("name") in core_list,
            "tags": a.get("tags") or [],
        })

    out: List[Dict] = []
    for r in ROLES:
        packs = bucket[r["id"]]
        if not packs:
            continue
        # core 按 ROLE_CORE 里定义的优先级排（最常用的排第一），其余按名称稳定排序
        order = {n: i for i, n in enumerate(ROLE_CORE.get(rid, []))}
        packs.sort(key=lambda x: (0, order.get(x["name"], 99)) if x["core"] else (1, x["name"] or ""))
        out.append({
            "id": r["id"], "name": r["name"], "initial": r["initial"],
            "desc": r["desc"], "color": r["color"],
            "count": len(packs), "core_count": sum(1 for p in packs if p["core"]),
            "agents": packs,
        })
    return out


def resolve(role: Optional[str]) -> Optional[Dict[str, str]]:
    """把职业 id 或中文名都收成统一 id；无效返回 None。"""
    if not role:
        return None
    role = role.strip()
    for r in ROLES:
        if role in (r["id"], r["name"]):
            return r
    return None
