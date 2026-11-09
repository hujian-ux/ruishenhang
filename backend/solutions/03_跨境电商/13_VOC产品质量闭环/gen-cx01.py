# -*- coding: utf-8 -*-
"""CX-01 VOC产品质量闭环 · 原始数据源与交付物真实生成脚本
案例：海拓生活科技 HT-H03 加湿器 近 90 天（2025-12-16 ~ 2026-03-15）
差评 214 / 退货 163 / 工单 96 = VOC 合并 473 条
聚类：尺寸误解 288(61%) / 噪音 90(19%) / 漏水 52(11%) / 其他 43(9%)
尺寸类退货 99 单 x 18.4 USD = 1,821.6 USD
"""
import csv
import os
import random
import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
os.makedirs(BASE, exist_ok=True)
random.seed(20260315)

SKU = "HT-H03"
WIN_START = datetime.date(2025, 12, 16)
WIN_END = datetime.date(2026, 3, 15)
V23_DATE = datetime.date(2026, 1, 18)
V23_SPIKE_END = datetime.date(2026, 1, 31)

THEMES = ["尺寸误解", "噪音", "漏水", "其他"]
REVIEW_N = {"尺寸误解": 130, "噪音": 41, "漏水": 24, "其他": 19}
RETURN_N = {"尺寸误解": 99, "噪音": 31, "漏水": 19, "其他": 14}
TICKET_N = {"尺寸误解": 59, "噪音": 18, "漏水": 9, "其他": 10}
UNIT_LOSS = {"尺寸误解": 18.4, "噪音": 19.6, "漏水": 21.3, "其他": 17.5}

assert sum(REVIEW_N.values()) == 214
assert sum(RETURN_N.values()) == 163
assert sum(TICKET_N.values()) == 96


def d2s(d):
    return d.strftime("%Y-%m-%d")


def rand_day(a, b):
    return a + datetime.timedelta(days=random.randint(0, (b - a).days))


MK = [("US", "en"), ("US", "en"), ("US", "en"), ("DE", "de"), ("UK", "en"), ("FR", "fr")]

REV = {
    "尺寸误解": [
        ("Much smaller than I expected", "The photos made it look like a full room humidifier. It only covers my nightstand. The listing shows the diameter only, not the real footprint."),
        ("Not as expected - tiny", "Way too small for a bedroom. Nothing in the pictures gives you a size reference. Returning it."),
        ("Zu klein fuer das Schlafzimmer", "Auf den Bildern sieht das Geraet viel groesser aus. Nur der Durchmesser ist angegeben, nicht die Gesamtbreite mit Sockel."),
        ("Sizing info is misleading", "Product works fine but it is not the size I ordered. Please put total width and height in the main image."),
        ("Trop petit, photos trompeuses", "Le produit fonctionne mais il est beaucoup plus petit que sur les photos. Aucune reference de taille."),
        ("Good product, wrong size for me", "No quality issue at all, but the size chart only lists 168mm diameter. With the base it is a desk item, not a room unit."),
        ("Photos oversell the size", "Compared to the old picture with the plant, this thing is half the size. The size reference was removed from the new images."),
    ],
    "噪音": [
        ("Fan noise got worse after 3 weeks", "It started quiet and now there is a clear buzzing from the fan, loud enough to wake me up."),
        ("Laut, Luefter brummt", "Nach zwei Wochen brummt der Luefter deutlich. Nachts nicht benutzbar."),
        ("Not a quiet humidifier", "Measured around 45 dB next to the bed. The listing says whisper quiet, mine is not."),
        ("Rattling sound at high mist", "On level 3 the unit rattles. Sounds like the fan bearing."),
    ],
    "漏水": [
        ("Leaks water on the desk", "Every morning there is a puddle under the tank. The seal ring does not hold."),
        ("Wasser laeuft aus dem Tank", "Der Tank ist nach einer Nacht undicht, Wasser auf dem Nachttisch."),
        ("Water seeps from the bottom", "After refilling, water leaks from the base within an hour."),
    ],
    "其他": [
        ("App pairing failed", "Cannot connect to the app, it keeps timing out on 5GHz."),
        ("Filter not available", "No replacement filter is listed for this model."),
        ("Arrived with scratched lid", "Cosmetic damage on the lid, otherwise fine."),
        ("Manual only in English", "The German manual is missing from the box."),
    ],
}

review_rows = []
rid = 1
for th in THEMES:
    n = REVIEW_N[th]
    spike = 66 if th == "尺寸误解" else 0
    for i in range(n):
        if i < spike:
            day = rand_day(V23_DATE, V23_SPIKE_END)
        else:
            day = rand_day(WIN_START, WIN_END)
            if th == "尺寸误解":
                while V23_DATE <= day <= V23_SPIKE_END:
                    day = rand_day(WIN_START, WIN_END)
        mk, lang = random.choice(MK)
        title, body = REV[th][i % len(REV[th])]
        review_rows.append({
            "review_id": f"REV-{rid:04d}",
            "sku": SKU,
            "review_date": d2s(day),
            "marketplace": mk,
            "language": lang,
            "star_rating": random.choice([1, 1, 2, 2, 3]),
            "review_title": title,
            "review_body": body,
            "verified_purchase": "Y" if random.random() < 0.93 else "N",
            "listing_version": "v2.3" if day >= V23_DATE else "v2.2",
            "theme_tag": th,
        })
        rid += 1
review_rows.sort(key=lambda r: r["review_date"])

with open(os.path.join(BASE, "差评明细.csv"), "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=list(review_rows[0].keys()))
    w.writeheader()
    w.writerows(review_rows)

RET_COMMENT = {
    "尺寸误解": ["too small", "not as expected", "too small for the room", "not as expected - size",
                 "smaller than pictures", "too small / wrong size"],
    "噪音": ["too noisy at night", "fan buzzing", "noise level not as advertised"],
    "漏水": ["leaking from tank", "water on the desk", "seal leaking"],
    "其他": ["app will not pair", "cosmetic damage on lid", "changed my mind", "missing manual"],
}
RET_REASON = {
    "尺寸误解": ["DEFECTIVE", "NOT_AS_DESCRIBED", "DEFECTIVE", "NOT_AS_DESCRIBED"],
    "噪音": ["DEFECTIVE", "PERFORMANCE_ISSUE"],
    "漏水": ["DEFECTIVE", "PERFORMANCE_ISSUE"],
    "其他": ["UNWANTED_ITEM", "DAMAGED_BY_CARRIER", "DEFECTIVE"],
}
DISPO = {
    "尺寸误解": ["Defective"] * 71 + ["Customer Damaged"] * 21 + ["Sellable"] * 7,
    "噪音": ["Defective"] * 28 + ["Sellable"] * 3,
    "漏水": ["Defective"] * 17 + ["Customer Damaged"] * 2,
    "其他": ["Defective"] * 6 + ["Sellable"] * 5 + ["Customer Damaged"] * 3,
}
WH_NOTE = {
    "尺寸误解": ["外观无破损，通电正常；客户备注 too small", "功能测试通过，无渗漏；备注 not as expected",
                 "整机完好，配件齐全；备注 too small", "外观完好，雾量正常；备注 size not as expected"],
    "噪音": ["通电后风机异响，dB(A)@1m 实测 44.6", "风机运行有明显嗡鸣，判定功能不良", "高档位有异响，需返厂拆检"],
    "漏水": ["水箱保压 30min 渗水 6ml，密封圈压缩量不足", "底座积水，密封圈压痕不均", "注水后 40min 底部渗水"],
    "其他": ["外观有划痕，功能正常", "整机完好，未拆封", "包装内缺少德文说明书"],
}


def spread_loss(n, mean):
    target = round(n * mean, 2)
    vals = [round(mean + ((i % 7) - 3) * 0.9, 2) for i in range(n)]
    diff = round(target - round(sum(vals), 2), 2)
    vals[-1] = round(vals[-1] + diff, 2)
    assert round(sum(vals), 2) == target, (n, mean, sum(vals), target)
    return vals


return_rows = []
oid = 10001
tid = 1
for th in THEMES:
    n = RETURN_N[th]
    losses = spread_loss(n, UNIT_LOSS[th])
    dispo_pool = DISPO[th][:]
    assert len(dispo_pool) == n, (th, len(dispo_pool), n)
    for i in range(n):
        day = rand_day(WIN_START, WIN_END)
        mk, lang = random.choice(MK)
        return_rows.append({
            "return_id": f"RET-{tid:04d}",
            "order_id": f"HT-{mk}-{oid}",
            "sku": SKU,
            "return_date": d2s(day),
            "marketplace": mk,
            "quantity": 1,
            "platform_reason_code": RET_REASON[th][i % len(RET_REASON[th])],
            "customer_comment": RET_COMMENT[th][i % len(RET_COMMENT[th])],
            "warehouse_disposition": dispo_pool[i],
            "warehouse_note": WH_NOTE[th][i % len(WH_NOTE[th])],
            "unit_loss_usd": losses[i],
            "theme_tag": th,
        })
        oid += 1
        tid += 1
return_rows.sort(key=lambda r: r["return_date"])

with open(os.path.join(BASE, "FBA退货报告.csv"), "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=list(return_rows[0].keys()))
    w.writeheader()
    w.writerows(return_rows)

TK = {
    "尺寸误解": [
        ("收到的尺寸和图片不一样", "Is this the full size unit? Mine only covers my nightstand, the picture looked much bigger.", "话术解释+退货退款"),
        ("下单前想确认整机宽度", "The listing says 168mm diameter. What is the total width including the base?", "话术解释"),
        ("尺寸和预期差太多想退", "It is not broken, it is just far smaller than the photos suggested.", "退货退款"),
    ],
    "噪音": [
        ("使用三周后风机噪音变大", "The fan noise increased a lot after three weeks, can I get a replacement?", "换货"),
        ("夜间噪音无法接受", "Measured 45 dB beside the bed, the listing claims whisper quiet.", "升级产品部"),
    ],
    "漏水": [
        ("水箱底部渗水", "Water leaks from the bottom of the tank overnight.", "换货"),
        ("补水后底座积水", "After refilling there is water under the base within an hour.", "升级产品部"),
    ],
    "其他": [
        ("App 无法配网", "The app keeps timing out during pairing.", "话术解释"),
        ("滤芯在哪里买", "Where can I buy the replacement filter for this model?", "话术解释"),
        ("缺少德文说明书", "The German manual was missing in the box.", "补发配件"),
    ],
}
CHANNEL = ["邮件", "站内信", "邮件", "WhatsApp", "电话"]

ticket_rows = []
kid = 1
for th in THEMES:
    for i in range(TICKET_N[th]):
        day = rand_day(WIN_START, WIN_END)
        mk, lang = random.choice(MK)
        subj, verbatim, reso = TK[th][i % len(TK[th])]
        ticket_rows.append({
            "ticket_id": f"TK-{kid:04d}",
            "sku": SKU,
            "created_date": d2s(day),
            "marketplace": mk,
            "channel": CHANNEL[i % len(CHANNEL)],
            "subject": subj,
            "customer_verbatim": verbatim,
            "first_reply_hours": round(random.uniform(1.2, 11.8), 1),
            "resolution": reso,
            "linked_order_id": f"HT-{mk}-{10001 + (kid * 7) % 163}",
            "theme_tag": th,
        })
        kid += 1
ticket_rows.sort(key=lambda r: r["created_date"])

with open(os.path.join(BASE, "客服工单.csv"), "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=list(ticket_rows[0].keys()))
    w.writeheader()
    w.writerows(ticket_rows)

# ----------------------------------------------------------- 交付物：VOC质量闭环表
voc_total = {th: REVIEW_N[th] + RETURN_N[th] + TICKET_N[th] for th in THEMES}
assert voc_total == {"尺寸误解": 288, "噪音": 90, "漏水": 52, "其他": 43}, voc_total
VOC_ALL = sum(voc_total.values())
assert VOC_ALL == 473

loss_by_theme = {th: round(RETURN_N[th] * UNIT_LOSS[th], 2) for th in THEMES}
assert loss_by_theme["尺寸误解"] == 1821.60, loss_by_theme
LOSS_ALL = round(sum(loss_by_theme.values()), 2)

# 取真实存在的证据 ID：尺寸类且 Disposition=Defective 但备注写 too small / not as expected
ev_size = [r["return_id"] for r in return_rows
           if r["theme_tag"] == "尺寸误解" and r["warehouse_disposition"] == "Defective"][:3]
ev_size_rev = [r["review_id"] for r in review_rows
               if r["theme_tag"] == "尺寸误解" and r["listing_version"] == "v2.3"][:2]
ev_noise = [r["return_id"] for r in return_rows if r["theme_tag"] == "噪音"][:2]
ev_leak = [r["return_id"] for r in return_rows if r["theme_tag"] == "漏水"][:2]
ev_other = [r["ticket_id"] for r in ticket_rows if r["theme_tag"] == "其他"][:2]

size_defective = sum(1 for r in return_rows
                     if r["theme_tag"] == "尺寸误解" and r["warehouse_disposition"] == "Defective")
all_defective = sum(1 for r in return_rows if r["warehouse_disposition"] == "Defective")
spike_cnt = sum(1 for r in review_rows if r["theme_tag"] == "尺寸误解"
                and V23_DATE <= datetime.date.fromisoformat(r["review_date"]) <= V23_SPIKE_END)
assert spike_cnt == 66, spike_cnt
assert size_defective == 71, size_defective

HEAD = ["序号", "问题主题", "VOC合并条数", "占比", "退货单数", "单均损失USD", "损失金额USD",
        "星标证据编号", "发生原因_为什么发生", "流出原因_为什么没被拦住", "根因判定",
        "CAPA编号", "CAPA动作", "动作类型", "责任岗位", "验收标准", "目标完成日", "状态"]

CAPA = [
    # ---- 尺寸误解（主题级指标只在首行填写，合计行可直接列求和）----
    dict(theme="尺寸误解", first=True,
         ev=f"{ev_size[0]}/{ev_size[1]}/{ev_size[2]} 备注 too small 但 Disposition=Defective；{ev_size_rev[0]} 落在 v2.3",
         occ="Listing 尺寸信息只标外径 φ168mm，未标含底座总宽 205mm 与含盖总高 286mm；v2.3 主图移除了参照物，客户按想象下单",
         esc="Listing 改图上线无「尺寸信息完整性」审校项；VOC 未按 Listing 版本回溯，异常两周才被看见",
         root="描述性预期差（非产品失效）——不得按 Defective 向供应商追责",
         no="A1", act="五点描述第 1 条前置「含底座总宽 205mm × 含盖总高 286mm」，A+ 首段同步", typ="改描述文字（零成本）",
         owner="运营-Listing 负责人", acc="前台页面可见且与规格书 SPEC-HT-H03 一致，运营+客服双签",
         due="2026-03-18", st="进行中"),
    dict(theme="尺寸误解", no="A2", act="重制主图 3 为尺寸标注图：外径/含底座总宽/含盖总高三维标注 + A4 纸参照物", typ="改图",
         owner="设计-视觉主设", acc="v2.4 图片包上线并留存版本记录，主图审校单勾选「尺寸完整」",
         due="2026-03-27", st="待执行",
         occ="主图缺尺寸标注与参照物", esc="图片上线无审校项", root="同上", ev=f"{ev_size_rev[1]}"),
    dict(theme="尺寸误解", no="A3", act="A+ 内容新增「桌面占位示意」模块（与 13 寸笔电同框实拍）", typ="改图",
         owner="设计-视觉主设", acc="A+ 模块上线截图归档", due="2026-04-03", st="待执行",
         occ="缺少真实使用场景比例参照", esc="A+ 变更无复核", root="同上", ev="-"),
    dict(theme="尺寸误解", no="A4", act="客服话术库新增「尺寸确认」预回复 + 售前尺寸确认卡（含三维数值）", typ="改话术",
         owner="客服-售后组长", acc="话术库版本号更新，全组培训签到表归档", due="2026-03-20", st="进行中",
         occ="售前未主动澄清尺寸", esc="客服知道问题但未回流运营", root="同上", ev="-"),
    dict(theme="尺寸误解", no="A5", act="撤销对供应商 S-A 的尺寸类质量索赔 71 单，改判描述性退货并更正质量月报", typ="定责更正",
         owner="品质-QE / 采购", acc="索赔撤销单 + 供应商确认回签，质量月报重发", due="2026-03-25", st="待审批",
         occ="Disposition=Defective 被直接当作质量定责依据", esc="定责流程缺「客户备注交叉核对」步骤",
         root="定责口径错误", ev=f"{ev_size[0]}~ 共 {size_defective} 单"),
    # ---- 噪音 ----
    dict(theme="噪音", first=True, no="B1", act="风机噪音项由抽检改为逐批检（dB(A)@1m ≤ 38dB 判合格），追溯近 3 个批次", typ="加质检项",
         owner="品质-IQC", acc="3 个批次检测报告齐全，超标批次隔离并出处置单", due="2026-03-22", st="进行中",
         occ="供应商 B 批次风机轴承同心度超差，实测 44.6dB 超上限 38dB", esc="噪音项为抽检，批次间波动未被拦住",
         root="产品失效（真质量问题，保留 Defective 定责）", ev=f"{ev_noise[0]}/{ev_noise[1]} 仓库实测 44.6dB"),
    dict(theme="噪音", no="B2", act="向供应商 S-A 索取 8D 报告并现场验证轴承压装工艺参数", typ="供应商整改",
         owner="采购-SQE", acc="8D 报告闭环 + 工艺参数变更单，验证批 dB 合格", due="2026-04-10", st="待执行",
         occ="轴承压装同心度控制缺失", esc="来料检验无噪音项", root="同上", ev="-"),
    dict(theme="噪音", no="B3", act="Listing 噪音表述改为「实测 36dB(A)@1m，测试条件见 A+」，删除 whisper quiet 绝对化表述", typ="改描述文字（零成本）",
         owner="运营-Listing 负责人", acc="前台文案更新，测试条件与报告一致", due="2026-03-19", st="进行中",
         occ="宣称与实测口径不一致", esc="文案未与检测报告绑定", root="表述与实测不一致", ev="-"),
    # ---- 漏水 ----
    dict(theme="漏水", first=True, no="C1", act="出厂新增「注水保压 30 分钟」全检项，渗水判不合格", typ="加质检项",
         owner="品质-OQC", acc="全检记录表上线，抽查 200 台漏检率 0", due="2026-03-24", st="进行中",
         occ="水箱密封圈压缩量不足，保压 30min 渗水 6ml", esc="出货抽检未含保压项，渗漏在客户端才暴露",
         root="产品失效（真质量问题）", ev=f"{ev_leak[0]}/{ev_leak[1]} 仓库保压记录"),
    dict(theme="漏水", no="C2", act="密封圈来料公差由 ±0.10mm 收紧至 ±0.05mm，并要求供应商附首件报告", typ="供应商整改",
         owner="采购-SQE", acc="新公差图纸签发，首件报告合格", due="2026-04-08", st="待执行",
         occ="密封圈厚度公差过宽", esc="来料仅目视检验", root="同上", ev="-"),
    # ---- 其他 ----
    dict(theme="其他", first=True, no="D1", act="对「其他」43 条做二次标注，占比降到 5% 以下才允许结案", typ="归类细化",
         owner="客服-售后组长", acc="二次标注表提交，其他类占比 ≤5%", due="2026-03-21", st="进行中",
         occ="标签粒度不足，问题被归到其他后失去线索", esc="无「其他类占比上限」规则",
         root="归类口径不足", ev=f"{ev_other[0]}/{ev_other[1]}"),
    dict(theme="其他", no="D2", act="建立 VOC 月度对齐例会：差评/退货/工单三源按 SKU 对齐，责任人签字", typ="流程闭环",
         owner="客服-售后组长 / 运营 / 品质", acc="首次例会纪要 + 三源对齐表归档，纳入月度考核", due="2026-03-31", st="待执行",
         occ="三类数据分散在平台后台/FBA 报告/客服系统，无人合看", esc="无固定对齐机制，问题重复三个月",
         root="组织流程缺口", ev="-"),
]

rows = []
seq = 0
for c in CAPA:
    seq += 1
    th = c["theme"]
    first = c.get("first", False)
    rows.append([
        seq, th,
        voc_total[th] if first else "",
        f"{voc_total[th] / VOC_ALL * 100:.1f}%" if first else "",
        RETURN_N[th] if first else "",
        UNIT_LOSS[th] if first else "",
        loss_by_theme[th] if first else "",
        c.get("ev", "-"),
        c.get("occ", ""), c.get("esc", ""), c.get("root", ""),
        c["no"], c["act"], c["typ"], c["owner"], c["acc"], c["due"], c["st"],
    ])

rows.append([
    "合计", "4 类主题", VOC_ALL, "100.0%", sum(RETURN_N.values()), "",
    LOSS_ALL,
    f"仓库 Disposition=Defective 共 {all_defective} 单（{all_defective / 163 * 100:.1f}%），其中尺寸类 {size_defective} 单备注写 too small / not as expected",
    "", "",
    f"尺寸类占 VOC {voc_total['尺寸误解'] / VOC_ALL * 100:.1f}%、占退货损失 {loss_by_theme['尺寸误解'] / LOSS_ALL * 100:.1f}%",
    f"{len(CAPA)} 项", "改描述 2 / 改图 2 / 改话术 1 / 加质检 2 / 供应商整改 2 / 定责更正 1 / 归类细化 1 / 流程闭环 1",
    "", "", "尺寸图上线前不得判定「已解决」；上线后 30 天尺寸类退货占比 ≤25%", "2026-04-10",
    "整体未闭环",
])

OUT = os.path.join(BASE, "VOC质量闭环表.csv")
with open(OUT, "w", newline="", encoding="utf-8-sig") as f:
    w = csv.writer(f)
    w.writerow(HEAD)
    w.writerows(rows)

# ------------------------------------------------------------------ 验证
def verify(p, min_rows):
    assert os.path.exists(p), p
    sz = os.path.getsize(p)
    assert sz > 0, p
    with open(p, encoding="utf-8-sig") as fh:
        n = sum(1 for _ in csv.reader(fh)) - 1
    assert n >= min_rows, (p, n, min_rows)
    print(f"OK  {os.path.basename(p):24s} size={sz:>7d}B rows={n}")


verify(os.path.join(BASE, "差评明细.csv"), 214)
verify(os.path.join(BASE, "FBA退货报告.csv"), 163)
verify(os.path.join(BASE, "客服工单.csv"), 96)
verify(OUT, 13)
print("---- 复算校验 ----")
print("VOC 合并 =", VOC_ALL, "= 214+163+96")
print("聚类占比 =", {th: f"{voc_total[th] / VOC_ALL * 100:.1f}%" for th in THEMES})
print("尺寸类退货 99 x 18.4 =", loss_by_theme["尺寸误解"], "USD")
print("四类损失合计 =", LOSS_ALL, "USD")
print("Defective 总数 =", all_defective, " 其中尺寸类 =", size_defective)
print("v2.3 后两周尺寸类差评 =", spike_cnt, "条 / 尺寸类差评", REVIEW_N["尺寸误解"], "条")
print("尖峰日均 =", round(spike_cnt / 14, 2), " 其余日均 =", round((130 - spike_cnt) / 76, 2))
