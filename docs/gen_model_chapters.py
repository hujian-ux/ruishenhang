# -*- coding: utf-8 -*-
"""把 AI 工程评审发现的 10 项模型工程缺陷，展开为可执行的实施章节(35-44)，
注入主蓝图 HTML，并升级版本号到 v1.15，与现有 34 章拼成"平台+模型"双闭环。"""

HTML = r'''

  <h2>35. 模型选型决策框架（何时用 LLM / 传统 ML / 规则） <span class="tagp p0">P0</span></h2>
  <div class="note"><b>缺陷回顾（来自 AI 工程评审）：</b>当前设计把"AI"等同于"大模型 + RAG"，缺少模型选型框架。用 LLM 做确定性、结构化、可批量的任务，会导致成本高出"几个数量级"、结果不可复现、不可解释，且引入幻觉。</div>
  <h3>35.1 外部依据</h3>
  <ul>
    <li>IBM 与企业实践共识：<b>输入是表格/数值 → 传统 ML（XGBoost / LR）；输入是文本/多模态 → LLM</b>。</li>
    <li>用 LLM 跑批量分类/回归，成本较传统 ML 高<b>数倍至数十倍</b>，且非确定性、不可解释、难审计。</li>
  </ul>
  <h3>35.2 选型决策树（实施核心）</h3>
  <svg viewBox="0 0 680 250" aria-label="模型选型决策树" style="width:100%;max-width:680px;border:1px solid #e2e8f0;border-radius:10px;background:#fff">
    <rect x="8" y="14" width="180" height="40" rx="8" fill="#0a2540"/><text x="98" y="39" fill="#fff" font-size="13" text-anchor="middle">任务输入类型？</text>
    <line x1="98" y1="54" x2="98" y2="86" stroke="#334"/>
    <rect x="8" y="86" width="170" height="34" rx="8" fill="#e8f1fb"/><text x="93" y="108" fill="#0a2540" font-size="12" text-anchor="middle">表格/数值/确定性</text>
    <line x1="188" y1="103" x2="262" y2="103" stroke="#334"/>
    <rect x="262" y="86" width="170" height="34" rx="8" fill="#e8f1fb"/><text x="347" y="108" fill="#0a2540" font-size="12" text-anchor="middle">文本/图像/模糊语义</text>
    <line x1="93" y1="120" x2="93" y2="152" stroke="#334"/>
    <line x1="347" y1="120" x2="347" y2="152" stroke="#334"/>
    <rect x="8" y="152" width="170" height="34" rx="8" fill="#16a34a"/><text x="93" y="174" fill="#fff" font-size="12" text-anchor="middle">规则 / 传统 ML</text>
    <rect x="262" y="152" width="170" height="34" rx="8" fill="#2563eb"/><text x="347" y="174" fill="#fff" font-size="12" text-anchor="middle">LLM（必要时微调）</text>
    <line x1="177" y1="169" x2="262" y2="169" stroke="#334"/>
    <text x="220" y="163" fill="#666" font-size="10" text-anchor="middle">需可解释/可批量/低成本→左</text>
  </svg>
  <h3>35.3 落地步骤</h3>
  <div class="step"><b>① 建立选型决策树：</b>按"输入类型 → 任务确定性 → 可解释性要求 → 延迟/成本预算"四问定模型类别，写入平台"新建智能体"向导，强制选型说明。</div>
  <div class="step"><b>② 扩展模型网关适配器：</b>在第 25 章注册表中新增 <code>adapter_type ∈ {llm, ml_serving, rule}</code>，传统 ML 与规则引擎也走同一网关，统一 caller 注入与计量。</div>
  <div class="step"><b>③ 默认策略与红线：</b>凡"确定性、结构化、可批量"的任务默认非 LLM；仅"模糊语义、生成、推理"用 LLM；关键决策引入 LLM 须人机共签（见第 33 章）。</div>
  <div class="step"><b>④ 成本/质量对比看板：</b>对同一任务并行跑 LLM 与传统 ML，记录准确率、成本、延迟，沉淀为选型知识库。</div>
  <h3>35.4 验收清单</h3>
  <table><tr><th>项</th><th>标准</th></tr>
  <tr><td>选型决策树</td><td>已上线并嵌入"新建智能体"向导，强制填写</td></tr>
  <tr><td>非 LLM 接入</td><td>≥ 3 个传统 ML / 规则模型接入第 25 章网关</td></tr>
  <tr><td>选型评审</td><td>每个新需求上线前过选型评审，留存记录</td></tr></table>
  <div class="note"><b>衔接：</b>第 25 章（模型网关，扩展 adapter）· 第 34 章（成本优化）· 第 33 章（责任边界）。</div>

  <h2>36. RAG 质量工程（检索质量门禁） <span class="tagp p0">P0</span></h2>
  <div class="note"><b>缺陷回顾：</b>当前 RAG 只做权限过滤，缺检索质量工程。纯向量检索会漏掉 30–50% 相关文档，导致答案错误/幻觉；权限过滤正确 ≠ 检索质量过关。</div>
  <h3>36.1 外部依据</h3>
  <ul>
    <li>2025–2026 工程共识：<b>混合检索（稀疏 + 稠密）+ 重排（cross-encoder）</b>显著优于纯向量。</li>
    <li>先测 <b>Recall@100 ≥ 0.95</b> 再调重排与生成；检索质量需独立评测集与门禁。</li>
  </ul>
  <h3>36.2 检索管线（实施核心）</h3>
  <svg viewBox="0 0 680 180" aria-label="RAG质量管线" style="width:100%;max-width:680px;border:1px solid #e2e8f0;border-radius:10px;background:#fff">
    <rect x="6" y="70" width="120" height="40" rx="8" fill="#0a2540"/><text x="66" y="95" fill="#fff" font-size="12" text-anchor="middle">用户 Query</text>
    <line x1="126" y1="90" x2="162" y2="90" stroke="#334"/>
    <rect x="162" y="40" width="130" height="34" rx="8" fill="#2563eb"/><text x="227" y="62" fill="#fff" font-size="12" text-anchor="middle">稀疏检索 BM25</text>
    <rect x="162" y="96" width="130" height="34" rx="8" fill="#2563eb"/><text x="227" y="118" fill="#fff" font-size="12" text-anchor="middle">稠密向量检索</text>
    <line x1="292" y1="57" x2="330" y2="80" stroke="#334"/><line x1="292" y1="113" x2="330" y2="95" stroke="#334"/>
    <rect x="330" y="70" width="130" height="40" rx="8" fill="#7c3aed"/><text x="395" y="95" fill="#fff" font-size="12" text-anchor="middle">融合 + 重排</text>
    <line x1="460" y1="90" x2="496" y2="90" stroke="#334"/>
    <rect x="496" y="70" width="120" height="40" rx="8" fill="#16a34a"/><text x="556" y="95" fill="#fff" font-size="12" text-anchor="middle">质量门禁</text>
    <text x="556" y="128" fill="#666" font-size="10" text-anchor="middle">Recall@100≥0.95</text>
    <text x="66" y="140" fill="#666" font-size="10" text-anchor="middle">↑ 评测集回流</text>
  </svg>
  <h3>36.3 落地步骤</h3>
  <div class="step"><b>① 混合检索：</b>Query 同时走 BM25（关键词/专有名词）与向量检索，按 RRF 或学习排序融合。</div>
  <div class="step"><b>② 重排层：</b>取 Top-100 候选用 cross-encoder 重排至 Top-K，提升精度。</div>
  <div class="step"><b>③ 切分与元数据：</b>采用语义切分 + 父子文档（子块检索、父块喂给模型），保留来源/章节/权限元数据。</div>
  <div class="step"><b>④ 评测集与门禁：</b>人工构建 ≥ 200 条 golden queries + 标准相关 doc_id 集合；指标 Recall@K、MRR、nDCG；CI 中跑门禁，<b>Recall@100 < 0.95 阻断上线</b>。</div>
  <div class="step"><b>⑤ 两层过滤：</b>检索阶段先保证"找得全"（质量层），再在第 18 章单一过滤点做"看不见"（合规层），二者解耦。</div>
  <h3>36.4 验收清单</h3>
  <table><tr><th>项</th><th>标准</th></tr>
  <tr><td>混合检索</td><td>BM25 + 向量 + 重排三件套上线</td></tr>
  <tr><td>评测集</td><td>≥ 200 条 golden queries，指标自动化</td></tr>
  <tr><td>质量门禁</td><td>Recall@100 ≥ 0.95 才放行，接入 CI</td></tr></table>
  <div class="note"><b>衔接：</b>第 5 章（知识库切分/元数据）· 第 18 章（合规过滤点，质量层与合规层解耦）· 第 28/37 章（评测）。</div>

  <h2>37. ML 级评测体系（不依赖 LLM-as-judge） <span class="tagp p0">P0</span></h2>
  <div class="note"><b>缺陷回顾：</b>当前评测依赖 LLM-as-judge。研究显示即便 GPT-4o，人机相关性仅 0.59–0.66，无歧义样本上反而低于人-人相关性，会"一致性地错"——自动演化飞轮若建在这之上，可能把变差的版本推上生产。</div>
  <h3>37.1 外部依据</h3>
  <ul>
    <li>ICLR 2025 与 arXiv 2025/2026：LLM-as-judge 存在系统性偏差，仅宜作辅助。</li>
    <li>正确做法：<b>客观指标用规则校验 → 黄金集做回归基线 → 多模型交叉 + 人工抽检</b>。</li>
  </ul>
  <h3>37.2 分层评测</h3>
  <div class="step"><b>① 客观指标（规则校验，不靠 LLM）：</b>格式正确性、工具调用参数正确性、检索命中 doc_id 是否在区间、引用是否存在、权限越权拦截率。</div>
  <div class="step"><b>② 黄金集（golden set）：</b>人工标注 ≥ 500 条多轮对话 + 标准答案/标准动作，作为回归基线，每次改动必跑。</div>
  <div class="step"><b>③ LLM-as-judge 仅辅助：</b>若用，须多模型交叉并设校准阈值，结果只作参考不单独定生死。</div>
  <div class="step"><b>④ 人工抽检 + 用户反馈：</b>按置信度抽样人工复核；用户"踩/赞"实时回流第 20 章演化。</div>
  <div class="step"><b>⑤ 评测驱动演化：</b>第 20 章演化建议必须通过评测门禁（黄金集不退化 + 客观指标达标）才允许灰度上线。</div>
  <h3>37.3 验收清单</h3>
  <table><tr><th>项</th><th>标准</th></tr>
  <tr><td>黄金集</td><td>≥ 500 条，覆盖多轮与边界，自动化跑批</td></tr>
  <tr><td>客观指标</td><td>格式/工具/检索/引用/越权 5 类自动化校验</td></tr>
  <tr><td>演化门禁</td><td>第 20 章演化建议接评测门禁，不达标不灰度</td></tr></table>
  <div class="note"><b>衔接：</b>第 20 章（自动演化）· 第 28 章（测试与评测体系）· 第 25 章（模型网关，可切换 judge 模型）。</div>

  <h2>38. 非 LLM 模型的 MLOps（训练/特征/漂移/重训） <span class="tagp p1">P1</span></h2>
  <div class="note"><b>缺陷回顾：</b>"机器学习模型方案"目前只有推理调用，缺训练流水线、特征管理、模型版本、数据漂移检测与重训机制。</div>
  <h3>38.1 落地步骤</h3>
  <div class="step"><b>① 特征存储：</b>离线特征（批）与在线特征（实时）统一管理，保证训练- serving 一致性。</div>
  <div class="step"><b>② 训练流水线：</b>数据清洗 → 特征 → 训练 → 评估 → 注册的离线流水线，可定时/触发重跑。</div>
  <div class="step"><b>③ 模型注册表与版本：</b>每个模型带版本、训练数据指纹、指标；与第 25 章网关同一注册表（adapter_type=ml_serving）。</div>
  <div class="step"><b>④ 漂移检测：</b>监控输入分布（PSI / KS）与效果衰减，超阈告警。</div>
  <div class="step"><b>⑤ 重训与回滚：</b>漂移或效果下降触发重训，新版本灰度，旧版本可回滚。</div>
  <h3>38.2 验收清单</h3>
  <table><tr><th>项</th><th>标准</th></tr>
  <tr><td>MLOps 闭环</td><td>≥ 1 个传统 ML 模型走完训练→注册→serving→漂移→重训</td></tr>
  <tr><td>版本回滚</td><td>模型版本可一键回滚</td></tr></table>

  <h2>39. 幻觉抑制与 Grounding 机制 <span class="tagp p1">P1</span></h2>
  <div class="note"><b>缺陷回顾：</b>第 27 章只列了"幻觉"威胁，未给具体抑制手段。企业最怕 AI"说错话"。</div>
  <h3>39.1 落地步骤</h3>
  <div class="step"><b>① 引用强制（citation grounding）：</b>回答每段必须附可点击来源片段；无来源声明不得生成事实断言。</div>
  <div class="step"><b>② 事实校验：</b>生成后做"声明-证据"比对，证据不在检索集内则标记或拒答。</div>
  <div class="step"><b>③ 置信校准与拒答：</b>低置信/无检索支撑时走拒答或转人工（见第 42 章）。</div>
  <div class="step"><b>④ 输出合规校验层：</b>在生成后加一层规则/小模型过滤（敏感词、越权信息、违规内容）。</div>
  <h3>39.2 验收清单</h3>
  <table><tr><th>项</th><th>标准</th></tr>
  <tr><td>引用覆盖</td><td>关键事实回答 100% 附来源</td></tr>
  <tr><td>幻觉抽检</td><td>人工抽检幻觉率 < 2%</td></tr></table>
  <div class="note"><b>衔接：</b>第 27 章（AI 行为安全）· 第 36 章（RAG 质量）· 第 42 章（降级转人工）。</div>

  <h2>40. 成本与延迟优化（路由 + 语义缓存） <span class="tagp p1">P1</span></h2>
  <div class="note"><b>缺陷回顾：</b>第 25 章有计费但无优化手段；无任务复杂度路由、无语义缓存，规模上来后成本与延迟失控。</div>
  <h3>40.1 落地步骤</h3>
  <div class="step"><b>① 任务路由器：</b>按复杂度把请求路由到合适模型——简单问答/抽取走小模型，复杂推理走大模型，路由策略可配置。</div>
  <div class="step"><b>② 语义缓存：</b>对语义相近的 Query 命中缓存结果（embedding 相似度阈值），降低重复调用。</div>
  <div class="step"><b>③ 模型分层与预算：</b>在第 25 章注册表里标 <code>tier</code> 与单价；高成本模型单独审批；超预算熔断降级。</div>
  <div class="step"><b>④ 常见请求预计算：</b>高频问答/报表预生成，命中即返回。</div>
  <h3>40.2 验收清单</h3>
  <table><tr><th>项</th><th>标准</th></tr>
  <tr><td>小模型占比</td><td>简单任务小模型承接率 > 40%</td></tr>
  <tr><td>缓存命中</td><td>语义缓存命中率 > 20%</td></tr></table>
  <div class="note"><b>衔接：</b>第 25 章（计费/网关）· 第 34 章（性能与成本优化）· 第 35 章（选型）。</div>

  <h2>41. 模型质量可观测性 <span class="tagp p1">P1</span></h2>
  <div class="note"><b>缺陷回顾：</b>第 25 章只记 token/成本，不记成功率、幻觉率、转人工率——无法判断模型"做得好不好"。</div>
  <h3>41.1 落地步骤</h3>
  <div class="step"><b>① 扩展计量埋点：</b>在第 25 章计量层增加质量字段：成功率、幻觉标记率、转人工率、用户满意度、延迟 p95/p99。</div>
  <div class="step"><b>② 打通可观测性：</b>与第 26 章指标/日志/链路三支柱打通，按 tenant/dept/agent/session 维度下钻。</div>
  <div class="step"><b>③ 质量告警：</b>幻觉率/转人工率突增自动告警，触发第 20 章演化复盘。</div>
  <div class="step"><b>④ 看板：</b>模型质量看板（受 scope 约束，成本/质量属敏感仅管理员可见）。</div>
  <h3>41.2 验收清单</h3>
  <table><tr><th>项</th><th>标准</th></tr>
  <tr><td>质量指标</td><td>看板含 ≥ 5 个质量指标，可按部门下钻</td></tr>
  <tr><td>告警</td><td>幻觉率/转人工率超阈自动告警</td></tr></table>
  <div class="note"><b>衔接：</b>第 25 章（计量）· 第 26 章（可观测性）· 第 37 章（评测）。</div>

  <h2>42. 优雅降级与人工升级 <span class="tagp p2">P2</span></h2>
  <div class="note"><b>缺陷回顾：</b>Agent 失败时多为"静默失败"或硬报错，无置信阈值触发的人工升级，体验差且易出错。</div>
  <h3>42.1 落地步骤</h3>
  <div class="step"><b>① 置信阈值：</b>每次回答带置信分；低于阈值走降级链（小模型 → 规则 → 转人工）。</div>
  <div class="step"><b>② 降级链配置：</b>每个智能体可配降级顺序与兜底话术。</div>
  <div class="step"><b>③ 人工升级：</b>转人工带完整上下文与置信理由，进入工单/审批流（见第 18 章人工审核节点）。</div>
  <h3>42.2 验收清单</h3>
  <table><tr><th>项</th><th>标准</th></tr>
  <tr><td>降级链</td><td>每个智能体配置降级顺序</td></tr>
  <tr><td>人工升级</td><td>低置信自动转人工且带上下文</td></tr></table>
  <div class="note"><b>衔接：</b>第 39 章（拒答/转人工）· 第 18 章（人工审核节点）。</div>

  <h2>43. 中文语言特化 <span class="tagp p2">P2</span></h2>
  <div class="note"><b>缺陷回顾：</b>Embedding 与 LLM 需中文优化，未单列；中文分词、专名、成语、政策术语易出问题。</div>
  <h3>43.1 落地步骤</h3>
  <div class="step"><b>① 中文 Embedding：</b>优先选用中文优化模型（如 bge / bce 系列）作为默认向量模型，可在第 25 章注册表切换。</div>
  <div class="step"><b>② 分词与切分：</b>检索切分考虑中文语义边界，避免按标点粗暴切。</div>
  <div class="step"><b>③ 中文 Prompt 模板：</b>建立中文少样本/系统提示模板库，规避翻译腔。</div>
  <div class="step"><b>④ 中文基准：</b>用 C-Eval / CMMLU / 内部中文任务集做回归基准。</div>
  <h3>43.2 验收清单</h3>
  <table><tr><th>项</th><th>标准</th></tr>
  <tr><td>默认向量</td><td>中文优化 embedding 为默认</td></tr>
  <tr><td>中文基准</td><td>内部中文任务集纳入回归</td></tr></table>

  <h2>44. Agent / 工具层安全闭环 <span class="tagp p2">P2</span></h2>
  <div class="note"><b>缺陷回顾：</b>第 18 章单一过滤点只在"检索层"；但 Agent 调用工具、访问外部 API 时可能绕过权限外泄数据。</div>
  <h3>44.1 落地步骤</h3>
  <div class="step"><b>① 工具调用也过权限：</b>每个工具声明所需 scope/数据域，调用前经同一过滤器校验 caller 是否有权。</div>
  <div class="step"><b>② 外部 API 沙箱与脱敏：</b>出网请求经网关脱敏（去手机号/身份证/密钥），仅放白名单域名。</div>
  <div class="step"><b>③ 工具审计：</b>工具入参/出参、调用者、结果进入统一审计流（见第 29 章）。</div>
  <div class="step"><b>④ 与检索层同过滤器：</b>复用第 18 章单一过滤点代码，工具层不另起炉灶。</div>
  <h3>44.2 验收清单</h3>
  <table><tr><th>项</th><th>标准</th></tr>
  <tr><td>工具鉴权</td><td>100% 工具调用经权限校验</td></tr>
  <tr><td>出网脱敏</td><td>外部 API 经脱敏网关，白名单管控</td></tr></table>
  <div class="note"><b>衔接：</b>第 18 章（单一过滤点）· 第 22 章（工具对接）· 第 29 章（审计）。</div>

'''

def main():
    path = "企业AI智能体中台蓝图.html"
    s = open(path, encoding="utf-8").read()
    # 插入在 footer 之前
    marker = '  <footer>企业 AI 智能体中台 · 蓝图 v1.14 · 通用版 · 内部使用 · 2026-11-01</footer>'
    assert marker in s, "footer marker not found"
    s = s.replace(marker, HTML + "\n" + marker, 1)
    # 版本号升 v1.15
    s = s.replace("通用蓝图 v1.14", "通用蓝图 v1.15", 1)
    s = s.replace("蓝图 v1.14", "蓝图 v1.15", 1)
    open(path, "w", encoding="utf-8").write(s)
    print("chapters 35-44 injected, version -> v1.15")

if __name__ == "__main__":
    main()
