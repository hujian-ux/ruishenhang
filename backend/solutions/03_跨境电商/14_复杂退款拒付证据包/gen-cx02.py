# -*- coding: utf-8 -*-
"""CX-02 复杂退款/拒付证据包 · 原始数据源与交付物真实生成脚本
案例：海拓生活科技 Shopify 独立站，拒付 386.00 USD，原因码 13.1「商品未收到」
证据截止 5 天（2026-03-11 受理 → 2026-03-16 23:59 UTC）
本月拒付率 0.87%（11 / 1,264 笔），1% 触发支付通道保证金上调
"""
import csv
import os

BASE = os.path.dirname(os.path.abspath(__file__))
os.makedirs(BASE, exist_ok=True)

# ------------------------------------------------------------------ 案件常量
AMOUNT = 386.00
DISPUTE_FEE = 15.00
SETTLE_OFFER = 120.00
TXN_MONTH = 1264
CB_COUNT = 11
CB_RATE = CB_COUNT / TXN_MONTH * 100                       # 0.8703%
CB_RATE_IF_WITHDRAWN = (CB_COUNT - 1) / TXN_MONTH * 100     # 0.7911%
CB_RATE_LINE = 1.00
CB_ROOM = 12 - CB_COUNT                                     # 12/1264 = 0.95% 仍在线内

# ------------------------------------------------------ 1. 订单支付物流时间线
TL_HEAD = ["序号", "时间_UTC", "来源系统", "事件", "关键字段与取值", "证据编号", "证明力"]
TL = [
    ("2026-02-27 09:14:22", "Shopify", "客户下单", "order=HT-SP-10428；金额=386.00 USD；收货地址=Unit 3B, 218 Maple Ave, Dover, DE 19901；邮箱=k.mercer***@mail.com；下单 IP=71.204.88.__（Dover, DE）", "EV-01", "高"),
    ("2026-02-27 09:15:07", "Stripe", "付款成功", "charge=ch_3QkR7uHT0428；卡=Visa ****4821；AVS=Y（地址+邮编匹配）；CVC=M（匹配）；3DS=未触发（免验证阈值内）", "EV-02", "高"),
    ("2026-02-27 09:15:11", "Shopify", "订单确认邮件已投递", "邮件 message-id=<ord-10428@ht-shop>；投递状态=delivered；打开时间=2026-02-27 09:41", "EV-03", "中"),
    ("2026-02-27 18:40:35", "海外仓 WMS", "生成拣货单", "pick=PK-88231；SKU=HT-H03×1；仓库=US-East 3PL（Newark, NJ）", "EV-04", "中"),
    ("2026-02-28 11:05:48", "海外仓 WMS", "出库交接承运商", "运单=ML7742891055；承运商=MetroLast Mile；交接扫描=Newark, NJ；重量=1.62kg", "EV-05", "高"),
    ("2026-02-28 20:12:03", "承运商 API", "轨迹：已收件", "status=Picked up；node=Newark NJ Hub", "EV-06", "中"),
    ("2026-03-01 06:44:19", "承运商 API", "轨迹：干线运输", "status=In transit；node=Philadelphia PA Hub", "EV-06", "中"),
    ("2026-03-03 15:27:52", "承运商 API", "轨迹：到达派送站", "status=Arrived at facility；node=Dover DE Station", "EV-06", "中"),
    ("2026-03-05 08:31:04", "承运商 API", "轨迹：派送中", "status=Out for delivery；driver=D-2291", "EV-06", "中"),
    ("2026-03-05 14:22:36", "承运商 POD 系统", "签收：POD 照片 + GPS 坐标", "photo=POD_ML7742891055_1.jpg；GPS=39.158341, -75.524372；签收人=R. Alvarez；设备=DRV-Scan-2291；门牌可读性=部分遮挡（快递袋遮住第 3 位）", "EV-07", "高（本案王牌）"),
    ("2026-03-05 14:22:41", "承运商 POD 系统", "签收：第二张照片（门口全景）", "photo=POD_ML7742891055_2.jpg；含门色/邮箱/台阶特征，可与街景比对", "EV-08", "高"),
    ("2026-03-05 14:31:10", "Shopify", "发货完成通知邮件", "message-id=<shp-10428@ht-shop>；投递状态=delivered", "EV-03", "中"),
    ("2026-03-06 10:31:27", "客服后台", "客户来信：订单还没到", "ticket=CS-2026-0663；客户原话=\"My order still has not arrived.\"", "EV-09", "中"),
    ("2026-03-06 12:08:55", "客服后台", "客服回复：轨迹显示已签收", "回复内容仅引用轨迹一句话，未附 POD 照片（本案流程缺陷）", "EV-09", "中"),
    ("2026-03-07 08:12:40", "客服后台", "客户来信：可能被放门口拿走了", "客户原话=\"Nobody at home signed for it, maybe it was left at the door and taken.\" —— 客户自述投递已到达门口", "EV-10", "高"),
    ("2026-03-07 09:20:12", "客服后台", "客服提出补发或退款方案", "提出：补发同款或全额退款需先向承运商报失，客户未回复", "EV-09", "中"),
    ("2026-03-09 16:02:09", "承运商 客服", "承运商回函：投递地址与订单地址一致", "case=ML-CLM-55831；结论=GPS 与订单地址偏差 8 米，投递有效", "EV-11", "高"),
    ("2026-03-11 03:02:44", "Stripe", "收到拒付通知", "dispute=dp_1QmT9xHT0428；amount=386.00 USD；reason=product_not_received；network_reason_code=13.1；evidence_due_by=2026-03-16 23:59 UTC；争议手续费=15.00 USD", "EV-12", "案件依据"),
    ("2026-03-11 09:15:00", "内部", "拒付率背景核查", "本月成交 1,264 笔，拒付 11 笔（含本案）=0.87%；1.00% 触发支付通道保证金上调", "EV-13", "决策依据"),
    ("2026-03-11 10:40:00", "内部", "调取历史败诉复盘", "case=DP-2025-1187（386 USD，同原因码）；驳回原因=漏附客户沟通记录，非证据不足", "EV-14", "参考"),
    ("2026-03-12 09:00:00", "内部", "启动证据包编制", "剩余时限 4 天；负责人=苏婷（客服售后与用户体验）", "-", "-"),
]
with open(os.path.join(BASE, "订单支付物流时间线.csv"), "w", newline="", encoding="utf-8-sig") as f:
    w = csv.writer(f)
    w.writerow(TL_HEAD)
    for i, r in enumerate(TL, 1):
        w.writerow([i] + list(r))

# ------------------------------------------------------ 2. 物流与POD记录
POD_HEAD = ["记录编号", "运单号", "节点时间_本地", "节点类型", "节点位置", "字段名", "字段取值", "可否作为拒付证据"]
POD = [
    ("PD-01", "ML7742891055", "2026-02-28 15:12", "Picked up", "Newark, NJ Hub", "scan_type", "Origin scan", "是（发货事实）"),
    ("PD-02", "ML7742891055", "2026-03-01 01:44", "In transit", "Philadelphia, PA Hub", "scan_type", "Linehaul scan", "辅助"),
    ("PD-03", "ML7742891055", "2026-03-03 10:27", "Arrived at facility", "Dover, DE Station", "scan_type", "Destination arrival", "辅助"),
    ("PD-04", "ML7742891055", "2026-03-05 08:31", "Out for delivery", "Dover, DE Station", "driver_id", "D-2291", "辅助"),
    ("PD-05", "ML7742891055", "2026-03-05 14:22", "Delivered", "Dover, DE 19901", "pod_photo_1", "POD_ML7742891055_1.jpg（门口 + 包裹 + 门牌）", "是（核心）"),
    ("PD-06", "ML7742891055", "2026-03-05 14:22", "Delivered", "Dover, DE 19901", "pod_photo_2", "POD_ML7742891055_2.jpg（门口全景：门色/邮箱/台阶）", "是（核心）"),
    ("PD-07", "ML7742891055", "2026-03-05 14:22", "Delivered", "Dover, DE 19901", "pod_gps", "39.158341, -75.524372", "是（核心）"),
    ("PD-08", "ML7742891055", "2026-03-05 14:22", "Delivered", "Dover, DE 19901", "pod_signature_name", "R. Alvarez", "是（需说明与收件人关系）"),
    ("PD-09", "ML7742891055", "2026-03-05 14:22", "Delivered", "Dover, DE 19901", "pod_device", "DRV-Scan-2291（含设备时间戳）", "辅助"),
    ("PD-10", "ML7742891055", "2026-03-05 14:22", "Delivered", "Dover, DE 19901", "house_number_legibility", "部分遮挡：门牌第 3 位被快递袋遮住，读作 21_", "缺口（需 GPS 补强）"),
    ("PD-11", "ML7742891055", "2026-03-09 16:02", "Carrier claim reply", "MetroLast Mile 客服", "geo_deviation", "POD GPS 与订单地址地理编码偏差 8 米", "是（排除地址错误）"),
    ("PD-12", "ML7742891055", "2026-03-09 16:02", "Carrier claim reply", "MetroLast Mile 客服", "claim_conclusion", "投递有效，无重投/退回记录", "是"),
    ("PD-13", "ML7742891055", "2026-03-09 16:05", "Carrier claim reply", "MetroLast Mile 客服", "left_at_door_policy", "该地址历史投递均为 signature-not-required，允许门口投放", "辅助（呼应客户自述）"),
]
with open(os.path.join(BASE, "物流与POD记录.csv"), "w", newline="", encoding="utf-8-sig") as f:
    w = csv.writer(f)
    w.writerow(POD_HEAD)
    w.writerows(POD)

# ------------------------------------------------------ 3. 客服沟通记录
CS_HEAD = ["记录编号", "时间_UTC", "方向", "渠道", "主题", "原文摘录", "抗辩用途"]
CS = [
    ("CS-01", "2026-02-27 09:15", "系统→客户", "邮件", "Order confirmation #HT-SP-10428", "Your order has been confirmed. Ship to: Unit 3B, 218 Maple Ave, Dover, DE 19901.", "证明下单地址由客户提供且被确认"),
    ("CS-02", "2026-03-05 14:31", "系统→客户", "邮件", "Your order was delivered", "Tracking ML7742891055 was delivered on Mar 5 at 2:22 PM.", "证明已主动告知投递完成"),
    ("CS-03", "2026-03-06 10:31", "客户→客服", "邮件", "Order still not arrived", "My order still has not arrived. Can you check?", "对方主张原文（争议点 C1）"),
    ("CS-04", "2026-03-06 12:08", "客服→客户", "邮件", "Re: Order still not arrived", "The carrier tracking shows the parcel was delivered on Mar 5 at 2:22 PM.", "本次仅引用轨迹，未附 POD —— 流程缺陷记录"),
    ("CS-05", "2026-03-07 08:12", "客户→客服", "邮件", "Re: Order still not arrived", "Nobody at home signed for it, maybe it was left at the door and taken.", "客户自述包裹已到门口（争议点 C1 关键反驳）"),
    ("CS-06", "2026-03-07 09:20", "客服→客户", "邮件", "Re: Order still not arrived", "We can reship the same item or issue a refund once the carrier confirms a loss report. Please confirm which you prefer.", "证明我方在拒付前已提供救济方案"),
    ("CS-07", "2026-03-08 00:00", "系统", "备注", "No customer reply within 24h", "Follow-up sent, no response received.", "证明客户未走商户退款流程即发起拒付"),
    ("CS-08", "2026-03-09 16:02", "承运商→客服", "邮件", "Claim ML-CLM-55831 result", "Delivery confirmed at the shipping address; GPS deviation 8 m; no re-delivery or return record.", "排除地址错误（争议点 C3）"),
    ("CS-09", "2026-03-10 11:30", "客服→客户", "邮件", "Carrier confirmation attached", "The carrier confirmed delivery at your address with GPS and photo evidence. Please advise if you would like us to file a porch-theft report.", "证明我方在拒付前已披露 POD 证据"),
    ("CS-10", "2026-03-11 03:02", "支付通道→商户", "系统通知", "Dispute opened dp_1QmT9xHT0428", "Reason: product_not_received (13.1). Evidence due by 2026-03-16 23:59 UTC.", "案件受理与时限依据"),
    ("CS-11", "2026-03-12 09:00", "内部", "备注", "Evidence pack kickoff", "Owner: 苏婷；remaining 4 days；prior loss case DP-2025-1187 reviewed.", "编制记录"),
]
with open(os.path.join(BASE, "客服沟通记录.csv"), "w", newline="", encoding="utf-8-sig") as f:
    w = csv.writer(f)
    w.writerow(CS_HEAD)
    w.writerows(CS)

# ------------------------------------------------------ 4. 交付物：拒付证据包.md
MD = f"""# 拒付证据包 · DP-2026-0311 · 386.00 USD

> 生成方式：WorkBuddy「复杂退款、拒付证据包」技能，基于 `订单支付物流时间线.csv`、`物流与POD记录.csv`、
> `客服沟通记录.csv`、`拒付规则与时效.md` 四份素材自动编制。
> 提交前需人工确认：对外陈述最终措辞、退款/协商决定、提交执行。

---

## 一、案件摘要

| 项目 | 取值 |
| --- | --- |
| 争议编号 | dp_1QmT9xHT0428 |
| 订单号 / 收款流水 | HT-SP-10428 / ch_3QkR7uHT0428 |
| 平台 | Shopify 独立站（收款通道 Stripe） |
| 争议金额 | **{AMOUNT:.2f} USD** |
| 争议手续费 | {DISPUTE_FEE:.2f} USD（败诉不退） |
| 原因码 | 13.1 · Merchandise/Services Not Received（商品未收到） |
| 客户主张 | 已付款但从未收到商品 |
| 受理时间 | 2026-03-11 03:02 UTC |
| **证据截止** | **2026-03-16 23:59 UTC（自受理起 5 天）** |
| 编制启动 | 2026-03-12 09:00 UTC（剩余 4 天） |
| 负责人 | 苏婷 · 客服售后与用户体验 |
| 我方结论 | 投递已完成，POD 照片 + GPS 坐标可证；地址错误可排除 |

**本月拒付率背景**：成交 {TXN_MONTH:,} 笔 / 拒付 {CB_COUNT} 笔（含本案）= **{CB_RATE:.2f}%**，
支付通道保证金上调线为 {CB_RATE_LINE:.2f}%。当前仅余 {CB_ROOM} 笔缓冲（12/{TXN_MONTH:,} = {12 / TXN_MONTH * 100:.2f}%，
第 13 笔即 {13 / TXN_MONTH * 100:.2f}% 越线）。**386 美元事小，拒付率越线事大。**

---

## 二、时效 SLA（倒排到小时）

| 节点 | 交付物 | 责任人 | 截止时间 (UTC) | 距截止 | 状态 |
| --- | --- | --- | --- | --- | --- |
| T0 受理 | 拒付通知归档、原因码确认 | 客服-苏婷 | 2026-03-11 12:00 | — | 已完成 |
| T1 案件拆解 | 所需证据类型清单、缺口清单 | 客服-苏婷 | 2026-03-12 18:00 | 提前 4.2 天 | 已完成 |
| T2 证据调取 | 向承运商调 POD 照片/GPS/签收人 | 客服-苏婷 | 2026-03-13 18:00 | 提前 3.2 天 | 已完成 |
| T3 时间线与映射 | 主张-证据逐条映射表 | 客服-苏婷 | 2026-03-14 12:00 | 提前 2.5 天 | 已完成 |
| T4 陈述定稿 | 抗辩陈述英文正文（人工确认措辞） | 客服主管 | 2026-03-15 12:00 | 提前 1.5 天 | **待人工审批** |
| T5 策略决定 | 提交申诉 or 协商撤诉（金额已量化） | 财务 + 客服主管 | 2026-03-15 18:00 | 提前 1.2 天 | **待人工审批** |
| T6 提交执行 | Stripe 后台上传证据包并留存回执 | 客服-苏婷 | **2026-03-16 12:00** | 提前 0.5 天 | 待执行 |
| T7 结果跟踪 | 裁决结果归档、拒付率更新 | 客服-苏婷 | 裁决日 +1 工作日 | — | 待执行 |

> 硬规则：T6 必须留 12 小时缓冲，不得压到 23:59；上传后必须截图留存提交回执。

---

## 三、争议点 ↔ 证据链逐条映射

> 审核方按「主张—证据」逐条核对，缺一条就输。上一起同额案件（DP-2025-1187）被驳回的原因不是证据不足，
> 而是漏附客户沟通记录——本表把沟通记录列为必附项。

| 编号 | 对方主张 | 我方反驳要点 | 支撑证据 | 证据类型 | 证明力 | 缺口/补强 |
| --- | --- | --- | --- | --- | --- | --- |
| C1 | 从未收到商品 | 2026-03-05 14:22 已投递并留存 POD 照片、GPS 坐标与签收人；客户本人于 03-07 邮件称「可能被放在门口拿走了」，等于承认包裹已到达门口 | EV-07 / EV-08 / EV-10 / PD-05 / PD-06 / PD-07 / CS-05 | POD 影像 + GPS + 客户自述 | 高 | POD 门牌第 3 位被快递袋遮挡，需用 GPS 偏差 8 米 + 门口全景特征补强 |
| C2 | 订单非本人授权 / 付款有疑 | AVS=Y（地址与邮编匹配）、CVC=M（匹配）、下单 IP 归属地 Dover DE 与收货地址同城；订单确认邮件已投递并被打开 | EV-01 / EV-02 / EV-03 / CS-01 | 支付风控字段 + 邮件投递记录 | 高 | 3DS 未触发（在免验证阈值内），需在陈述中说明属通道规则而非风控缺失 |
| C3 | 商家发错地址 | 承运商回函确认投递地址与订单地址一致，POD GPS 与订单地址地理编码偏差仅 8 米，无重投或退回记录 | EV-11 / PD-11 / PD-12 / CS-08 | 承运商书面回函 | 高 | 无（**排除性结论成立**） |
| C4 | 商家未提供救济，只能拒付 | 03-06 首次回复、03-07 主动提出补发或退款、03-10 主动披露 POD 证据；客户 24 小时内未回复即发起拒付 | EV-09 / CS-04 / CS-06 / CS-07 / CS-09 | 客服沟通全量记录 | 中高 | 03-06 首次回复仅引用轨迹一句话、未附 POD —— 已列为流程整改项 |

**映射完整性自检**：4 条主张全部有证据对应，0 条空缺；每条至少 1 项「高」证明力证据；沟通记录已附（上次败诉原因已闭环）。

---

## 四、证据清单（提交件）

| 证据编号 | 名称 | 来源系统 | 获取方式 | 对应主张 |
| --- | --- | --- | --- | --- |
| EV-01 | 订单详情页（含收货地址、金额） | Shopify | 后台订单导出 PDF | C1 / C2 / C3 |
| EV-02 | 付款风控字段截图（AVS=Y / CVC=M） | Stripe | Payment 详情页截图 | C2 |
| EV-03 | 订单确认与发货通知邮件投递记录 | Shopify 邮件日志 | 导出 message-id + 投递状态 | C2 / C4 |
| EV-04 | 拣货单 PK-88231 | 海外仓 WMS | 仓库系统导出 | C1 |
| EV-05 | 出库交接单（运单 ML7742891055） | 海外仓 WMS | 交接扫描回执 | C1 |
| EV-06 | 完整物流轨迹（5 个节点） | 承运商 API | 轨迹导出 PDF | C1 |
| **EV-07** | **POD 签收照片 1（门口 + 包裹 + 门牌）** | **承运商 POD 系统** | **向承运商客服提交 case 调取** | **C1** |
| **EV-08** | **POD 签收照片 2（门口全景）** | **承运商 POD 系统** | **同上** | **C1** |
| EV-09 | 客服沟通记录全量（11 条） | 客服后台 | 会话导出 | C4 |
| EV-10 | 客户 03-07 邮件原文（「可能被放门口拿走」） | 客服后台 | 单封邮件原文导出 | C1 |
| EV-11 | 承运商理赔回函 ML-CLM-55831（GPS 偏差 8 米） | 承运商客服 | 书面回函 PDF | C3 |
| EV-12 | 拒付通知原文（原因码 13.1、截止时间） | Stripe | 争议详情页导出 | 案件依据 |
| EV-13 | 本月拒付率核算表（11/1,264） | 内部 | 通道报表 + 台账 | 策略依据 |
| EV-14 | 历史败诉复盘 DP-2025-1187 | 内部 | 复盘归档 | 结构参考 |

> **星标提醒**：EV-07 / EV-08 / PD-07（GPS）存在于**承运商后台**，不在 Shopify 也不在 Stripe，
> 平时处理拒付的客服不会去翻。这三项是本案与「只写一句轨迹显示签收」的全部差距。

---

## 五、排除性结论

| 待排除假设 | 排除依据 | 结论 |
| --- | --- | --- |
| 地址错误（发错地方） | 订单地址 Unit 3B, 218 Maple Ave, Dover, DE 19901 与 POD GPS 39.158341, -75.524372 地理编码偏差 **8 米**；承运商回函确认无重投/退回 | **可排除** |
| 包裹未出库 | 拣货单 PK-88231 + 出库交接扫描 + 原发地扫描三重记录 | 可排除 |
| 我方未提供救济 | 03-06 回复、03-07 提出补发/退款、03-10 披露 POD | 可排除 |
| 门口失窃（porch theft） | 客户自述「可能被放门口拿走」；该地址历史投递均为 signature-not-required | **不可排除**——但属投递完成后的第三方风险，非「未收到货」 |

---

## 六、抗辩陈述（提交正文 · 英文）

> 措辞已按「事实—证据编号—结论」三段式收敛，不含情绪化表达、不做胜负预判。**最终措辞需人工确认后提交。**

```
Dispute ID: dp_1QmT9xHT0428 | Order: HT-SP-10428 | Amount: USD 386.00
Reason code: 13.1 Merchandise/Services Not Received

1. Delivery was completed and documented.
   The parcel (tracking ML7742891055) was delivered on 2026-03-05 at 14:22 local time.
   The carrier captured two proof-of-delivery photographs (EV-07, EV-08), GPS coordinates
   39.158341, -75.524372 (EV-07) and the signature name R. Alvarez. The GPS position deviates
   only 8 metres from the geocoded shipping address supplied by the cardholder (EV-11).

2. The cardholder's own correspondence confirms arrival at the delivery address.
   On 2026-03-07 the cardholder wrote: "Nobody at home signed for it, maybe it was left at the
   door and taken." (EV-10). This statement concerns a post-delivery event at the correct
   address, not a failure to deliver.

3. The transaction was properly authorised.
   AVS returned Y (street and postal code match) and CVC returned M (match) (EV-02).
   The order IP geolocation and the shipping address are in the same city (EV-01).
   Order confirmation and shipment notification e-mails were delivered and opened (EV-03).

4. An incorrect-address scenario is excluded.
   The carrier's written claim response ML-CLM-55831 confirms delivery at the shipping address
   with no re-delivery or return record (EV-11).

5. Merchant remedies were offered before the dispute was raised.
   We replied on 2026-03-06, offered a reshipment or a refund subject to a carrier loss report
   on 2026-03-07, and disclosed the POD evidence on 2026-03-10 (EV-09, CS-06, CS-09).
   No response was received before the dispute was filed (CS-07).

Conclusion: the goods were delivered to the address provided by the cardholder and the
transaction was authorised. We respectfully request that the dispute be resolved in favour of
the merchant. All exhibits EV-01 to EV-14 are attached and indexed.
```

**中文对照要点**（内部审阅用）：① 投递已完成且有影像与坐标；② 客户自述已到门口；③ 支付风控字段正常；
④ 地址错误可排除；⑤ 拒付前已提供救济。五点与 C1–C4 主张一一对应。

---

## 七、双策略与金额（决策交人做）

| | 策略 A · 直接提交申诉 | 策略 B · 协商部分退款撤诉 |
| --- | --- | --- |
| 动作 | 按上述证据包在截止前提交 Stripe | 与客户协商部分退款 {SETTLE_OFFER:.2f} USD，请客户向发卡行撤销拒付 |
| 直接成本 | 0（人工约 1.5 小时） | {SETTLE_OFFER:.2f} USD |
| 胜诉情形 | 收回 {AMOUNT:.2f} USD，争议手续费 {DISPUTE_FEE:.2f} USD 退回 | — |
| 败诉情形 | 净损失 {AMOUNT + DISPUTE_FEE:.2f} USD（{AMOUNT:.2f} + {DISPUTE_FEE:.2f} 手续费） | — |
| 确定性结果 | 不确定（取决于裁决） | 确定：保留 {AMOUNT - SETTLE_OFFER:.2f} USD，净损失 {SETTLE_OFFER:.2f} USD |
| 对拒付率影响 | 本案已计入，本月维持 {CB_RATE:.2f}%（{CB_COUNT}/{TXN_MONTH:,}） | 客户撤销后本案移出计数，回落至 {CB_RATE_IF_WITHDRAWN:.2f}%（{CB_COUNT - 1}/{TXN_MONTH:,}） |
| 证据强度说明 | C1–C4 全覆盖，POD + GPS + 客户自述三项互证；唯一弱项是门牌第 3 位遮挡，已用 GPS 偏差 8 米补强 | 不涉及 |
| 主要风险 | 若审核方要求门牌清晰可读，弱项可能被抓 | 客户拿到部分退款后仍不撤销拒付（需先取得书面撤销承诺再付款） |
| 适用判断 | 单笔金额优先、证据强度足够时 | 拒付率逼近阈值、保收款通道优先时 |

**金额对比（可复算）**：策略 A 败诉最坏值 {AMOUNT + DISPUTE_FEE:.2f} USD vs 策略 B 确定损失 {SETTLE_OFFER:.2f} USD，
差额 {AMOUNT + DISPUTE_FEE - SETTLE_OFFER:.2f} USD。策略 B 另需先取得客户书面撤销承诺，否则退款与拒付双重损失。

> **本节不做决定。** 提交申诉 or 协商撤诉，由客服主管 + 财务在 T5（2026-03-15 18:00 UTC）前签字确认。

---

## 八、人工审批点

| 环节 | 审批人 | 不可由 AI 代替的原因 |
| --- | --- | --- |
| 抗辩陈述最终措辞 | 客服主管 | 对外正式陈述，措辞即承诺，需人对内容负责 |
| 退款 / 协商金额决定 | 财务 + 客服主管 | 涉及资金支出与拒付率权衡 |
| 提交执行与回执留存 | 客服-苏婷 | 需在通道后台操作并确认提交成功 |

---

## 九、待补缺口与流程整改

| 编号 | 缺口 | 补强动作 | 责任岗位 | 截止 |
| --- | --- | --- | --- | --- |
| G1 | POD 门牌第 3 位被遮挡 | 附 GPS 偏差 8 米说明 + 门口全景照特征比对（门色/邮箱/台阶） | 客服-苏婷 | 2026-03-14 |
| G2 | 签收人 R. Alvarez 与收件人关系不明 | 向承运商追问签收人身份记录；若无记录，在陈述中改用 GPS + 影像为主证 | 客服-苏婷 | 2026-03-14 |
| G3 | 03-06 首次回复未附 POD | 客服话术库新增「拒付/未收到货」标准回复模板，强制附 POD 影像 | 客服-售后组长 | 2026-03-20 |
| G4 | POD 需人工向承运商逐案调取 | 与承运商开通 POD 影像 API 或每日批量回传，收货后自动归档 | 供应链-物流对接 | 2026-04-10 |
| G5 | 拒付率无预警 | 建立拒付率日更看板，达 0.85% 触发提醒、0.95% 触发管控 | 财务 + 客服主管 | 2026-03-31 |

---

## 十、复算索引

| 数字 | 计算式 | 结果 |
| --- | --- | --- |
| 争议金额 | 订单 HT-SP-10428 实付 | {AMOUNT:.2f} USD |
| 败诉最坏净损失 | {AMOUNT:.2f} + {DISPUTE_FEE:.2f} | {AMOUNT + DISPUTE_FEE:.2f} USD |
| 协商方案确定损失 | 部分退款 | {SETTLE_OFFER:.2f} USD |
| 协商方案保留金额 | {AMOUNT:.2f} − {SETTLE_OFFER:.2f} | {AMOUNT - SETTLE_OFFER:.2f} USD |
| 两策略差额 | {AMOUNT + DISPUTE_FEE:.2f} − {SETTLE_OFFER:.2f} | {AMOUNT + DISPUTE_FEE - SETTLE_OFFER:.2f} USD |
| 本月拒付率 | {CB_COUNT} ÷ {TXN_MONTH:,} × 100% | {CB_RATE:.2f}% |
| 撤诉后拒付率 | {CB_COUNT - 1} ÷ {TXN_MONTH:,} × 100% | {CB_RATE_IF_WITHDRAWN:.2f}% |
| 越线笔数 | 13 ÷ {TXN_MONTH:,} × 100% | {13 / TXN_MONTH * 100:.2f}%（> {CB_RATE_LINE:.2f}%） |
| 证据时限 | 2026-03-11 03:02 → 2026-03-16 23:59 UTC | 5 天（编制启动时余 4 天） |
| GPS 偏差 | POD 坐标 vs 订单地址地理编码 | 8 米 |
"""
OUT = os.path.join(BASE, "拒付证据包.md")
with open(OUT, "w", encoding="utf-8") as f:
    f.write(MD)


# ------------------------------------------------------------------ 验证
def vcsv(p, minr):
    assert os.path.exists(p) and os.path.getsize(p) > 0, p
    with open(p, encoding="utf-8-sig") as fh:
        n = sum(1 for _ in csv.reader(fh)) - 1
    assert n >= minr, (p, n)
    print(f"OK  {os.path.basename(p):26s} size={os.path.getsize(p):>7d}B rows={n}")


def vmd(p):
    assert os.path.exists(p) and os.path.getsize(p) > 0, p
    lines = open(p, encoding="utf-8").read().splitlines()
    assert len(lines) > 0
    print(f"OK  {os.path.basename(p):26s} size={os.path.getsize(p):>7d}B lines={len(lines)}")


vcsv(os.path.join(BASE, "订单支付物流时间线.csv"), 21)
vcsv(os.path.join(BASE, "物流与POD记录.csv"), 13)
vcsv(os.path.join(BASE, "客服沟通记录.csv"), 11)
vmd(OUT)
print("---- 复算校验 ----")
print(f"拒付率 = {CB_COUNT}/{TXN_MONTH} = {CB_RATE:.4f}%  -> 报 {CB_RATE:.2f}%")
print(f"撤诉后 = {CB_COUNT - 1}/{TXN_MONTH} = {CB_RATE_IF_WITHDRAWN:.2f}%")
print(f"13 笔 = {13 / TXN_MONTH * 100:.2f}% 越 1% 线")
print(f"败诉最坏 = {AMOUNT} + {DISPUTE_FEE} = {AMOUNT + DISPUTE_FEE:.2f} USD")
print(f"协商保留 = {AMOUNT} - {SETTLE_OFFER} = {AMOUNT - SETTLE_OFFER:.2f} USD")
print(f"两策略差额 = {AMOUNT + DISPUTE_FEE - SETTLE_OFFER:.2f} USD")
