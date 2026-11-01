# -*- coding: utf-8 -*-
"""生成《模型工程补强 · 实施附册 A4》(第35-44章)，每章一页实施卡片：问题/依据/步骤/验收。
包含 2 张 SVG 图(选型决策树、RAG管线)，与蓝图 HTML 同源。"""
import re
from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

FONT = "C:/Windows/Fonts/msyh.ttc"
pdfmetrics.registerFont(TTFont("msyh", FONT, subfontIndex=0))
pdfmetrics.registerFont(TTFont("msyhb", "C:/Windows/Fonts/msyhbd.ttc", subfontIndex=0))
CN = "msyh"; CB = "msyhb"

W, H = A4
M = 16 * mm
CW = W - 2 * M

def font(sz, bold=False): return pdfmetrics.getFont((CB if bold else CN)).fontName, sz

def _f(size):
    try: return ImageFont.truetype(FONT, size)
    except: return ImageFont.load_default()

def render_svg(svg, w=640, h=200, scale=2):
    m = re.search(r'viewBox="0 0 (\d+) (\d+)"', svg)
    vw, vh = int(m.group(1)), int(m.group(2))
    IW, IH = int(vw*scale*0.6), int(vh*scale*0.6)
    img = Image.new('RGB', (IW, IH), 'white'); d = ImageDraw.Draw(img)
    for rect in re.findall(r'<rect\s+([^>]+)/?>', svg):
        a = dict(re.findall(r'(\w+)="([^"]+)"', rect))
        x,y,w2,h2 = int(a['x'])*scale*0.6, int(a['y'])*scale*0.6, int(a['width'])*scale*0.6, int(a['height'])*scale*0.6
        fill = a.get('fill','#ddd'); rx = int(a.get('rx',0))*scale*0.6
        d.rounded_rectangle([x,y,x+w2,y+h2], radius=rx, fill=fill, outline='#334155')
    for line in re.findall(r'<line\s+([^>]+)/?>', svg):
        a = dict(re.findall(r'(\w+)="([^"]+)"', line))
        d.line([int(a['x1'])*scale*0.6,int(a['y1'])*scale*0.6,int(a['x2'])*scale*0.6,int(a['y2'])*scale*0.6], fill=a.get('stroke','#334'), width=max(1,int(float(a.get('stroke-width',1))*scale*0.6)))
    for t in re.findall(r'<text\s+([^>]+)>(.*?)</text>', svg):
        a, txt = dict(re.findall(r'(\w+)="([^"]+)"', t[0])), t[1]
        x,y = int(a['x'])*scale*0.6, int(a['y'])*scale*0.6
        fs = int(a.get('font-size',12))*scale*0.6; fill=a.get('fill','#000')
        anc = a.get('text-anchor','start'); f=_f(max(10,fs))
        tw = d.textlength(txt, font=f)
        if anc=='middle': x-=tw/2
        elif anc=='end': x-=tw
        d.text((x,y-fs*0.25), txt, font=f, fill=fill)
    return img

SVG35 = '''<svg viewBox="0 0 680 250">
<rect x="8" y="14" width="180" height="40" rx="8" fill="#0a2540"/><text x="98" y="39" fill="#fff" font-size="13" text-anchor="middle">任务输入类型？</text>
<line x1="98" y1="54" x2="98" y2="86" stroke="#334"/>
<rect x="8" y="86" width="170" height="34" rx="8" fill="#e8f1fb"/><text x="93" y="108" fill="#0a2540" font-size="12" text-anchor="middle">表格/数值/确定性</text>
<line x1="188" y1="103" x2="262" y2="103" stroke="#334"/>
<rect x="262" y="86" width="170" height="34" rx="8" fill="#e8f1fb"/><text x="347" y="108" fill="#0a2540" font-size="12" text-anchor="middle">文本/图像/模糊语义</text>
<line x1="93" y1="120" x2="93" y2="152" stroke="#334"/><line x1="347" y1="120" x2="347" y2="152" stroke="#334"/>
<rect x="8" y="152" width="170" height="34" rx="8" fill="#16a34a"/><text x="93" y="174" fill="#fff" font-size="12" text-anchor="middle">规则 / 传统 ML</text>
<rect x="262" y="152" width="170" height="34" rx="8" fill="#2563eb"/><text x="347" y="174" fill="#fff" font-size="12" text-anchor="middle">LLM（必要时微调）</text>
<line x1="177" y1="169" x2="262" y2="169" stroke="#334"/>
<text x="220" y="163" fill="#666" font-size="10" text-anchor="middle">需可解释/可批量/低成本→左</text></svg>'''

SVG36 = '''<svg viewBox="0 0 680 180">
<rect x="6" y="70" width="120" height="40" rx="8" fill="#0a2540"/><text x="66" y="95" fill="#fff" font-size="12" text-anchor="middle">用户 Query</text>
<line x1="126" y1="90" x2="162" y2="90" stroke="#334"/>
<rect x="162" y="40" width="130" height="34" rx="8" fill="#2563eb"/><text x="227" y="62" fill="#fff" font-size="12" text-anchor="middle">稀疏检索 BM25</text>
<rect x="162" y="96" width="130" height="34" rx="8" fill="#2563eb"/><text x="227" y="118" fill="#fff" font-size="12" text-anchor="middle">稠密向量检索</text>
<line x1="292" y1="57" x2="330" y2="80" stroke="#334"/><line x1="292" y1="113" x2="330" y2="95" stroke="#334"/>
<rect x="330" y="70" width="130" height="40" rx="8" fill="#7c3aed"/><text x="395" y="95" fill="#fff" font-size="12" text-anchor="middle">融合 + 重排</text>
<line x1="460" y1="90" x2="496" y2="90" stroke="#334"/>
<rect x="496" y="70" width="120" height="40" rx="8" fill="#16a34a"/><text x="556" y="95" fill="#fff" font-size="12" text-anchor="middle">质量门禁</text>
<text x="556" y="128" fill="#666" font-size="10" text-anchor="middle">Recall@100≥0.95</text></svg>'''

CH = [
 ("35","模型选型决策框架","p0","把 AI 等同 LLM+RAG，用 LLM 做确定性/批量任务→成本高数倍、不可复现、不可解释。",
  "IBM 框架：表格→传统ML；文本→LLM。LLM 批量分类成本高出数倍且非确定性。",
  ["建选型决策树(输入类型→确定性→可解释→成本)并嵌入新建智能体向导",
   "第25章网关新增 adapter_type{llm,ml_serving,rule}，非LLM也走网关",
   "默认策略：确定性/结构化/可批量→非LLM；仅模糊/生成/推理→LLM",
   "建成本/质量对比看板，同一任务并行跑两类模型沉淀选型知识"],
  "决策树上 | ≥3 传统ML/规则接入网关 | 新需求上线前过选型评审"),
 ("36","RAG 质量工程","p0","纯向量检索漏 30–50% 相关文档；只做权限过滤≠检索质量。",
  "2025-2026 共识：混合检索+重排；先测 Recall@100≥0.95 再调生成。",
  ["混合检索：BM25 + 向量，RRF/学习排序融合",
   "重排层：Top-100 用 cross-encoder 重排至 Top-K",
   "语义切分+父子文档，保留来源/章节/权限元数据",
   "建 ≥200 条 golden queries 评测集，CI 门禁 Recall@100<0.95 阻断",
   "两层过滤：质量层(找得全) + 合规层(第18章看不见)解耦"],
  "混合检索上线 | 评测集≥200条自动化 | 质量门禁 Recall@100≥0.95"),
 ("37","ML 级评测体系","p0","评测依赖 LLM-as-judge，人机相关性仅 0.59–0.66 会一致错，演化飞轮地基不可靠。",
  "ICLR2025+arXiv 2025/2026：LLM-as-judge 有系统偏差，仅宜辅助。",
  ["客观指标用规则校验：格式/工具参数/检索命中/引用/越权拦截",
   "建 ≥500 条黄金集(多轮+边界)作回归基线",
   "LLM-as-judge 仅辅助：多模型交叉+校准阈值，不单独定生死",
   "人工抽检+用户反馈回流第20章演化",
   "演化建议须过评测门禁(黄金集不退化)才灰度"],
  "黄金集≥500条 | 5类客观指标自动化 | 演化接评测门禁"),
 ("38","非 LLM 模型 MLOps","p1","机器学习方案只有推理调用，缺训练/特征/版本/漂移/重训。",
  "标准 MLOps：特征存储→训练流水线→注册表→漂移检测→重训。",
  ["特征存储：离线+在线统一管理，训练-serving 一致",
   "训练流水线：清洗→特征→训练→评估→注册，可定时重跑",
   "模型注册表与版本：与第25章网关同表(adapter=ml_serving)",
   "漂移检测：PSI/KS 监控输入分布与效果衰减",
   "重训与回滚：漂移触发重训，灰度+旧版可回滚"],
  "≥1 传统ML走完整MLOps | 模型版本一键回滚"),
 ("39","幻觉抑制与 Grounding","p1","第27章只列威胁无手段，企业最怕说错话。",
  " grounding：每段附来源；无证据声明须拒答/转人工。",
  ["引用强制：每段附可点击来源，无来源不得生成事实断言",
   "事实校验：生成后做声明-证据比对，证据不在检索集则标记/拒答",
   "置信校准与拒答：低置信/无支撑走拒答或转人工(第42章)",
   "输出合规校验层：生成后规则/小模型过滤敏感与越权"],
  "关键事实100%附来源 | 人工幻觉抽检率<2%"),
 ("40","成本与延迟优化","p1","第25章有计费无优化；无路由无语义缓存，规模上来成本失控。",
  "任务路由+语义缓存可降 30–60% 调用成本。",
  ["任务路由器：简单→小模型，复杂→大模型，策略可配",
   "语义缓存：相似 Query 命中缓存(embedding 相似度阈值)",
   "模型分层与预算：注册表标 tier/单价，高成本单独审批，超预算熔断",
   "高频请求预计算，命中即返回"],
  "简单任务小模型承接>40% | 语义缓存命中>20%"),
 ("41","模型质量可观测性","p1","第25章只记 token/成本，不记成功率/幻觉率/转人工率。",
  "质量遥测需与可观测性打通，按维度下钻。",
  ["扩展计量埋点：成功率/幻觉率/转人工率/满意度/延迟p95",
   "与第26章指标-日志-链路三支柱打通，按 tenant/dept/agent 下钻",
   "质量告警：幻觉率/转人工率突增自动告警触发演化复盘",
   "质量看板受 scope 约束，成本/质量仅管理员可见"],
  "看板含≥5质量指标可下钻 | 超阈自动告警"),
 ("42","优雅降级与人工升级","p2","Agent 静默失败或硬报错，无置信阈值触发升级。",
  "置信阈值→降级链→人工升级是标配。",
  ["置信阈值：每答带置信分，低于阈值走降级链(小模型→规则→人工)",
   "降级链配置：每智能体可配降级顺序与兜底话术",
   "人工升级：转人工带完整上下文与置信理由，进工单/审批流"],
  "每智能体配降级链 | 低置信自动转人工带上下文"),
 ("43","中文语言特化","p2","Embedding/LLM 需中文优化未单列，分词专名易错。",
  "中文优化 embedding + 中文基准更稳。",
  ["默认中文 embedding(bge/bce 系列)，第25章注册表可切",
   "检索切分考虑中文语义边界，避免标点粗暴切",
   "建中文少样本/系统 Prompt 模板库，规避翻译腔",
   "用 C-Eval/CMMLU/内部中文集做回归基准"],
  "中文优化 embedding 为默认 | 中文集纳入回归"),
 ("44","Agent/工具层安全闭环","p2","第18章过滤点只在检索层；工具调用/外部API 可绕过权限外泄。",
  "工具层须复用同一过滤器，出网脱敏。",
  ["工具调用也过权限：每工具声明 scope/数据域，调用前校验 caller",
   "外部 API 沙箱与脱敏：去手机/身份证/密钥，仅白名单域名",
   "工具审计：入参/出参/调用者进统一审计流(第29章)",
   "复用第18章单一过滤点代码，不另起炉灶"],
  "100% 工具调用经校验 | 出网经脱敏网关白名单"),
]

PCOLOR = {"p0":colors.HexColor("#dc2626"),"p1":colors.HexColor("#d97706"),"p2":colors.HexColor("#2563eb")}

def cwrap(s, n):
    out=[]; cur=""
    for w in s:
        cur+=w
        if len(cur)>=n: out.append(cur); cur=""
    if cur: out.append(cur)
    return out

def draw_card(c, ch, idx):
    c.setFillColor(colors.HexColor("#0a2540")); c.rect(0,0,W,14*mm,fill=1,stroke=0)
    c.setFillColor(colors.white); c.setFont(*font(13,True)); c.drawString(M, 4.5*mm, "模型工程补强 · 实施附册　第 %s 章" % ch[0])
    y = H - 22*mm
    # title
    c.setFillColor(PCOLOR[ch[2]]); c.roundRect(M, y-6*mm, 22*mm, 7*mm, 2*mm, fill=1, stroke=0)
    c.setFillColor(colors.white); c.setFont(*font(10,True)); c.drawCentredString(M+11*mm, y-4.3*mm, {"p0":"P0","p1":"P1","p2":"P2"}[ch[2]])
    c.setFillColor(colors.HexColor("#0a2540")); c.setFont(*font(14,True)); c.drawString(M+25*mm, y-4*mm, "%s. %s" % (ch[0], ch[1]))
    y -= 12*mm
    def block(title, body, color):
        nonlocal y
        c.setFillColor(color); c.setFont(*font(10.5,True)); c.drawString(M, y, title)
        y -= 5*mm; c.setFont(*font(9.5)); c.setFillColor(colors.HexColor("#222"))
        for line in body:
            tw = c.beginText(M+3*mm, y); tw.setFont(*font(9.5)); tw.setTextOrigin(M+3*mm, y)
            for seg in cwrap(line, 92): tw.textLine("• "+seg)
            c.drawText(tw); y -= 4.6*mm*max(1,len(cwrap(line,92)))
        y -= 1.5*mm
    block("问题(缺陷)", [ch[3]], colors.HexColor("#dc2626"))
    block("外部依据", [ch[4]], colors.HexColor("#d97706"))
    block("实施步骤", ch[5], colors.HexColor("#0a2540"))
    block("验收清单", [ch[6]], colors.HexColor("#16a34a"))
    # 图
    if ch[0]=="35":
        img=render_svg(SVG35); iw=120*mm; ih=iw*img.height/img.width
        fn="_svg35.png"; img.save(fn); c.drawImage(fn, M, 16*mm, width=iw, height=ih, mask='auto')
    elif ch[0]=="36":
        img=render_svg(SVG36); iw=120*mm; ih=iw*img.height/img.width
        fn="_svg36.png"; img.save(fn); c.drawImage(fn, M, 16*mm, width=iw, height=ih, mask='auto')

def main():
    out="模型工程补强_实施附册_A4.pdf"
    c=canvas.Canvas(out, pagesize=A4)
    for i,ch in enumerate(CH):
        if i>0: c.showPage()
        draw_card(c, ch, i)
    c.save()
    print("saved", out, "pages=", len(CH))

if __name__=="__main__":
    main()
