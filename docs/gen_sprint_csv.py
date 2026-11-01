# -*- coding: utf-8 -*-
"""把 P0 三章（35/36/37）Sprint 任务卡导出为可导入 Jira / 飞书多维表的 CSV。
- 数据单一来源，同时生成两份：
  1) Sprint任务卡_Jira导入.csv  —— 采用 Jira CSV 导入识别的英文表头，开箱即导
  2) Sprint任务卡_飞书多维表.csv —— 中文表头，贴合飞书多维表格字段
- 编码：UTF-8 with BOM（utf-8-sig），保证 Excel / 飞书正确识别中文。
"""
import csv, os

BASE = r"C:\Users\hblch\WorkBuddy\2026-10-30-21-20-34"

# ---- 单一数据源 ----------------------------------------------------------
# 每个 Epic（章节）下的任务。36 章按周拆解。
EPICS = [
    dict(
        no="35", title="模型选型决策框架", prio="P0",
        goal="上线前堵住“LLM万能”误区，让每个新需求先过选型评审。",
        dep="依赖第25章模型网关；高风险：团队习惯用LLM做一切，需培训+评审红线。",
        tasks=[
            dict(key="35-1", name="决策树建模",
                 desc="选型决策树（输入→确定性→可解释→成本）文档",
                 est="2d", out="选型决策树（输入→确定性→可解释→成本）文档", acc="评审通过"),
            dict(key="35-2", name="网关扩展",
                 desc="第25章网关新增 adapter_type{llm,ml_serving,rule}",
                 est="3d", out="第25章网关新增 adapter_type{llm,ml_serving,rule}", acc="非LLM可注册"),
            dict(key="35-3", name="向导嵌入",
                 desc="“新建智能体”强制填写选型说明",
                 est="2d", out="“新建智能体”强制填写选型说明", acc="缺选型不可发布"),
            dict(key="35-4", name="对比看板",
                 desc="同任务 LLM vs ML 成本/准确率对比",
                 est="2d", out="同任务 LLM vs ML 成本/准确率对比", acc="看板可用"),
        ],
    ),
    dict(
        no="36", title="RAG 质量工程（重点）", prio="P0",
        goal="把检索质量做成可度量、可门禁的工程能力，杜绝“漏召回→幻觉”。",
        dep="依赖第5章知识库切分/元数据、第18章过滤点；关键指标：Recall@100≥0.95 才放行；质量层失败不进合规层。",
        tasks=[
            dict(key="36-W1", name="混合检索",
                 desc="BM25+向量，RRF融合；父子文档切分；权限元数据落库",
                 est="5d", out="融合检索服务；切分流水线", acc="双路召回可用"),
            dict(key="36-W2", name="重排层",
                 desc="Top-100→cross-encoder→Top-K；接入第25章网关",
                 est="5d", out="重排服务+配置", acc="MRR较纯向量↑"),
            dict(key="36-W3", name="评测集",
                 desc="≥200条 golden queries + 标准 doc_id；Recall@K/MRR/nDCG 脚本",
                 est="5d", out="评测集+自动化脚本", acc="Recall@100≥0.95"),
            dict(key="36-W4", name="质量门禁",
                 desc="质量门禁接入CI；质量层与第18章合规层解耦；上线报告",
                 est="5d", out="CI门禁+发布", acc="门禁阻断生效"),
        ],
    ),
    dict(
        no="37", title="ML 级评测体系", prio="P0",
        goal="用可靠评测替代 LLM-as-judge，给自动演化(第20章)立可信地基。",
        dep="依赖第20章自动演化、第28章测试体系；外部依据：ICLR2025+arXiv 2025/2026 LLM-as-judge 系统性偏差。",
        tasks=[
            dict(key="37-1", name="客观校验",
                 desc="格式/工具参数/检索命中/引用/越权 5 类规则校验",
                 est="3d", out="5类规则校验器", acc="自动化通过"),
            dict(key="37-2", name="黄金集",
                 desc="≥500条多轮+边界对话标注",
                 est="5d", out="≥500条多轮+边界对话标注集", acc="覆盖边界"),
            dict(key="37-3", name="judge约束",
                 desc="LLM-as-judge 多模型交叉+校准阈值，仅辅助",
                 est="2d", out="多模型交叉+校准阈值方案", acc="不单独定生死"),
            dict(key="37-4", name="演化门禁",
                 desc="第20章演化建议接评测门禁",
                 est="2d", out="演化建议→评测门禁链路", acc="不达标不灰度"),
        ],
    ),
]

SPRINT = "Sprint 1"
STATUS_JIRA = "To Do"
STATUS_FEISHU = "待办"

# ---- 1) Jira 导入 CSV（英文表头，Jira External System Import 可识别）----
jira_headers = ["Issue Type", "Summary", "Description", "Priority",
                "Components", "Original Estimate", "Labels", "Status",
                "Reporter", "Assignee"]
jira_rows = []
for ep in EPICS:
    # Epic 行
    jira_rows.append({
        "Issue Type": "Epic",
        "Summary": "P0·第%s章 %s" % (ep["no"], ep["title"]),
        "Description": "%s\n%s" % (ep["goal"], ep["dep"]),
        "Priority": "Highest",
        "Components": ep["title"],
        "Original Estimate": "",
        "Labels": "P0;%s" % SPRINT,
        "Status": STATUS_JIRA, "Reporter": "", "Assignee": "",
    })
    for t in ep["tasks"]:
        desc = "%s\n产出：%s\n验收：%s\n%s" % (t["desc"], t["out"], t["acc"], ep["dep"])
        jira_rows.append({
            "Issue Type": "Task",
            "Summary": "%s %s | 第%s章 %s" % (t["key"], t["name"], ep["no"], ep["title"]),
            "Description": desc,
            "Priority": "Highest",
            "Components": ep["title"],
            "Original Estimate": t["est"],
            "Labels": "P0;%s;%s" % (SPRINT, t["key"].split("-")[0]),
            "Status": STATUS_JIRA, "Reporter": "", "Assignee": "",
        })

with open(os.path.join(BASE, "Sprint任务卡_Jira导入.csv"), "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=jira_headers)
    w.writeheader()
    w.writerows(jira_rows)

# ---- 2) 飞书多维表 CSV（中文表头）----
feishu_headers = ["任务类型", "任务名称", "任务描述", "优先级", "所属章节",
                  "预估工期", "产出物", "验收标准", "标签", "状态", "负责人"]
feishu_rows = []
for ep in EPICS:
    feishu_rows.append({
        "任务类型": "史诗", "任务名称": "P0·第%s章 %s" % (ep["no"], ep["title"]),
        "任务描述": "%s\n%s" % (ep["goal"], ep["dep"]),
        "优先级": "P0", "所属章节": "第%s章 %s" % (ep["no"], ep["title"]),
        "预估工期": "", "产出物": "", "验收标准": "",
        "标签": "P0;%s" % SPRINT, "状态": STATUS_FEISHU, "负责人": "",
    })
    for t in ep["tasks"]:
        desc = "%s\n%s" % (t["desc"], ep["dep"])
        feishu_rows.append({
            "任务类型": "任务", "任务名称": "%s %s" % (t["key"], t["name"]),
            "任务描述": desc, "优先级": "P0",
            "所属章节": "第%s章 %s" % (ep["no"], ep["title"]),
            "预估工期": t["est"], "产出物": t["out"], "验收标准": t["acc"],
            "标签": "P0;%s;%s" % (SPRINT, t["key"].split("-")[0]),
            "状态": STATUS_FEISHU, "负责人": "",
        })

with open(os.path.join(BASE, "Sprint任务卡_飞书多维表.csv"), "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=feishu_headers)
    w.writeheader()
    w.writerows(feishu_rows)

print("Jira  CSV 行数（含表头）:", len(jira_rows) + 1)
print("飞书  CSV 行数（含表头）:", len(feishu_rows) + 1)
print("Epic:", len(EPICS), " Task:", sum(len(e["tasks"]) for e in EPICS))
print("SAVED:", os.path.join(BASE, "Sprint任务卡_Jira导入.csv"))
print("SAVED:", os.path.join(BASE, "Sprint任务卡_飞书多维表.csv"))
