# 平台绩效通知与 Case 往来记录 · HT-H03 德国站

> 原始输入 · 脱敏构造（业务格式真实）
> 主体：深圳海拓生活科技有限公司 / Hightop Living Technology Co., Ltd.
> 站点：Amazon.de（德国站） · 卖家账号 A2HT****9K3 · 责任人：平台运营与合规 · 林蔚

---

## 一、商品与商业影响

| 项目 | 值 |
| --- | --- |
| SKU | HT-H03 |
| 商品名 | Hightop 超声波恒温加湿器 4.5L |
| 销售型号 | HT-H03 |
| ASIN（德国站） | B0DE7H03QM |
| 站点 | Amazon.de |
| Listing 状态 | Inactive（Suspended Listing，2026-08-24 起） |
| 近 30 天销售额 | 13,800 USD |
| 日均销售额 | 460 USD（13,800 ÷ 30） |
| FBA 在仓库存 | 540 件 |
| 库存货值 | 12,960 USD（540 × 24.00 USD 到仓成本） |
| 停售日损失 | 约 460 USD / 日 |

---

## 二、平台绩效通知原文（Performance Notification）

```
From: Amazon Seller Performance <seller-performance@amazon.de>
Date: Mon, 24 Aug 2026 09:12:00 +0200 (CEST)
Subject: Your listing has been deactivated - Product Safety Concern (ASIN B0DE7H03QM)

Hello,

We are writing to inform you that the following listing has been deactivated
because we received complaints indicating a potential product safety concern:

ASIN: B0DE7H03QM   SKU: HT-H03

Why did this happen?
1) We received 3 customer complaints within the last 90 days reporting
   overheating / burning smell during operation.
2) Your product detail page contains claims that may mislead customers
   regarding the safety and certification status of the product.
3) The compliance documentation on file for this ASIN is incomplete.

How do I reactivate my listing?
Submit a Plan of Action (POA) within 72 hours of this notification,
including: (a) the root cause of the issue, (b) the actions you have taken
to resolve the issue, and (c) the steps you have taken to prevent similar
complaints going forward. Supporting documents must be legible, unedited,
and must correspond to the ASIN and model in question.

Deadline for submission: 27 Aug 2026, 09:12 (CEST)
```

**平台三项指控（本场景统一编号）**

| 指控编号 | 平台指控（原文要点） | 我方需要证明的事 |
| --- | --- | --- |
| A1 | 90 天内 3 起过热 / 异味安全隐患投诉 | 产品本体的安全性有第三方证据支撑；投诉可归因并已处置 |
| A2 | 商品详情页存在关于安全与认证状态的误导性表述 | 已识别具体误导语句、已完成修改、修改可验证 |
| A3 | 该 ASIN 在档合规文件不完整 | 补齐档案并说明每份文件与 ASIN / 型号的对应关系 |

---

## 三、事件时间线（含 Case 往来）

| 序号 | 时间（CEST） | 来源 | 事件 | 备注 |
| --- | --- | --- | --- | --- |
| T01 | 2026-08-14 16:40 | 买家投诉 | 订单 DE-30251-8841：运行 2 小时后有焦味 | 已退货，未走质量工单 |
| T02 | 2026-08-15 10:05 | 客服 | 工单 WO-26-1183 建立，客服按"常规退货"结案 | **矛盾点：未升级为安全事件** |
| T03 | 2026-08-18 21:17 | 买家投诉 | 订单 DE-30414-2260：底座发烫、担心起火 | 已退款，未取回样机 |
| T04 | 2026-08-20 14:30 | 内部 | 运营在页面新增"通过安全认证，可整夜使用"文案 | **矛盾点：认证主体为元件非整机** |
| T05 | 2026-08-22 08:55 | 买家投诉 | 订单 DE-30588-7014：加热片异响并断电 | 第 3 起，触发平台阈值 |
| T06 | 2026-08-24 09:12 | 平台 | Performance Notification：Listing 下架，72 小时提交 POA | 截止 2026-08-27 09:12 |
| T07 | 2026-08-24 11:40 | 内部 | 运营向采购索要认证文件 | 采购当天只找到元件级 UL 证书 |
| T08 | 2026-08-25 15:20 | 供应商 | 宁波佳恒回传《电热元件 UL 认证证书 E512883》 | **星标素材：认证对象是元件 HTE-220-A** |
| T09 | 2026-08-26 10:00 | 内部 | 本证据包生成时点 | 距截止约 29 小时 12 分 |

---

## 四、上一轮申诉驳回记录（同账号历史）

| 项目 | 内容 |
| --- | --- |
| 案件 | Case ID 10#####7734（SKU HT-H01 欧盟站） |
| 提交时间 | 2025-11-03 |
| 驳回时间 | 2025-11-08 |
| 驳回理由（平台原文要点） | "The evidence submitted does not correspond to the claims made."（证据与主张不对应） |
| 我方复盘结论 | 并非没有证据：发票 SKU 与 ASIN 对不上、时间线前后矛盾、整改措施"加强管理"无法验证；材料中含"我们是良心卖家""客户恶意投诉"等情绪化表述 |
| 本轮必须规避 | ① 证据编号必须与主张逐条对应 ② 时间线不得自相矛盾 ③ 整改动作必须可验证 ④ 零情绪化表述 |

---

## 五、页面表述现状（A2 取证用）

| 位置 | 现有表述（下架前） | 问题定性 | 建议修改后表述 |
| --- | --- | --- | --- |
| 标题 | "通过安全认证 · 可整夜连续使用" | 认证主体错位（元件≠整机）、使用方式绝对化 | "4.5L 大容量 · 12 小时定时关机" |
| 五点描述第 2 条 | "内置元件已获 UL 安全认证，整机安全无隐患" | 由元件认证推导整机结论 | "内置电热元件采用已获 UL 认证的 HTE-220-A" |
| 五点描述第 4 条 | "可无人值守整夜运行" | 与说明书"请勿在无人看护下长时间运行"冲突 | "支持 2/4/8 小时定时，建议按说明书使用" |
| A+ 页面图 3 | 图内文字"绝对安全" | 绝对化用语 | 删除该图内文字 |
