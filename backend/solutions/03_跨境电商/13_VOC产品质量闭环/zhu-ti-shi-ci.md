# CX-01 · VOC 产品质量闭环 · 主提示词

> 使用前提：已在 WorkBuddy「专家·技能·连接器」中导入并启用 `VOC产品质量闭环` Skill 包，
> 并已上传 4 份素材：`差评明细.csv`、`FBA退货报告.csv`、`客服工单.csv`、`产品与Listing档案.md`。

---

## 一、主提示词（复制即用）

```
请按「VOC产品质量闭环」技能，处理 SKU HT-H03（海拓生活科技 · 家用桌面加湿器）
近 90 天（2025-12-16 ~ 2026-03-15）的 VOC 数据，输出可派工的 CAPA 闭环表。

【输入 1 · 差评明细.csv】214 行
字段：review_id, sku, review_date, marketplace, language, star_rating,
     review_title, review_body, verified_purchase, listing_version, theme_tag

【输入 2 · FBA退货报告.csv】163 行
字段：return_id, order_id, sku, return_date, marketplace, quantity,
     platform_reason_code, customer_comment, warehouse_disposition,
     warehouse_note, unit_loss_usd, theme_tag

【输入 3 · 客服工单.csv】96 行
字段：ticket_id, sku, created_date, marketplace, channel, subject,
     customer_verbatim, first_reply_hours, resolution, linked_order_id, theme_tag

【输入 4 · 产品与Listing档案.md】
含：三维规格基准（外径 φ168mm / 含底座总宽 205mm / 含盖总高 286mm）、
   Listing v2.1/v2.2/v2.3 变更记录与上线日期、Disposition 口径对照、责任岗位对照表

【输出要求 · 按 5 段】
1. 三源对齐：报出 差评/退货/工单 条数与 VOC 合并条数，说明时间窗与「不去重」口径
2. 问题聚类：尺寸误解 / 噪音 / 漏水 / 其他 四类的条数与占比
   —— 占比之和必须 = 100.0%，四类条数之和必须 = VOC 合并条数（算不平请停下来先修口径）
3. 星标素材：列出 warehouse_disposition = Defective 且 customer_comment 含
   "too small" / "not as expected" / "wrong size" 的真实 return_id，
   报出误判单数与占全部 Defective 的比例，并结合 warehouse_note 说明
   「通电正常/无渗漏」为何不构成质量失效证据；
   同时按 Listing v2.3 上线日（2026-01-18）切分尺寸类差评，
   给出上线后 14 天日均 vs 其余时段日均的倍数
4. 双路径归因：每个主题分别写「为什么发生」与「为什么没被拦住」，
   并判定根因类型（产品失效 / 描述性预期差），描述性预期差一律不得向供应商索赔
5. CAPA 闭环表（CSV）：字段依次为
   序号, 问题主题, VOC合并条数, 占比, 退货单数, 单均损失USD, 损失金额USD,
   星标证据编号, 发生原因_为什么发生, 流出原因_为什么没被拦住, 根因判定,
   CAPA编号, CAPA动作, 动作类型, 责任岗位, 验收标准, 目标完成日, 状态
   —— 主题级指标只在该主题首行填写，末行为合计校验行

【硬规则】
- 金额只从退货表算：主题损失 = 该主题退货单数 × 该主题单均损失
- 零成本动作（改五点描述文字）必须排在改图之前，并给 48 小时内截止
- 尺寸图未上线前，不得出现「已解决」结论；结案条件写成上线后 30 天占比目标
- 不承诺效率百分比，只报可复算的条数、占比、金额、日期
- 产品根因认定 / Listing 修改确认 / 供应商索赔决定，标注为「需人工审批」

生成后自检：占比算平了吗？金额乘法对得上吗？每条 CAPA 都有责任岗位与可核对的验收标准吗？
```

---

## 二、追问提示词（按需使用）

**追问 1 · 只要老板看的三句话**
```
把上面的结论压缩成给老板的三句话：一句说钱（月度损失与尺寸类占比），
一句说误判（多少单被错标 Defective、索赔要不要撤），一句说下一步（今天能做的零成本动作）。
```

**追问 2 · 供应商索赔撤销说明**
```
基于星标素材，起草一份给供应商 S-A 的尺寸类索赔撤销说明：
列明撤销单数、判定依据（客户备注 + 仓库功能测试结论）、更正后的质量月报口径，
以及后续保留索赔的项目（噪音类 dB(A)@1m 实测 44.6 超 38dB 上限）。
```

**追问 3 · 30 天复测口径**
```
给出 CAPA 结案复测方案：复测窗口、样本口径（哪张表哪些字段）、
判定阈值（尺寸类退货占比 ≤25%）、以及复测未达标时的升级路径。
```

---

## 三、结果核对清单（学员自查）

| 检查项 | 通过标准 |
| --- | --- |
| 聚类算平 | 288 + 90 + 52 + 43 = 473，占比合计 100.0% |
| 金额可复算 | 尺寸类 99 × 18.4 = 1,821.6 USD；四类合计 3,078.9 USD |
| 星标素材真实 | 引用的 return_id 能在 FBA退货报告.csv 中查到，且 Disposition = Defective |
| 误判识别 | 报出 71 单尺寸类被标 Defective（Defective 总数 122 单） |
| 拐点识别 | v2.3 上线后 14 天尺寸类差评 66 条，日均 4.71 vs 其余 0.84（约 5.6 倍） |
| 双路径完整 | 每个主题都有「为什么发生」+「为什么没被拦住」 |
| CAPA 可执行 | 12 条 CAPA 均有责任岗位、验收标准、目标完成日 |
| 结论克制 | 未出现「已解决」；零成本方案排在改图前 |
