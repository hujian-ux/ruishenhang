# M24 · 对标 WorkBuddy 内置技能：参考什么，不抄什么

> 一句话：WorkBuddy 内置技能值得抄的是**定义范式**（触发条件 + 输入输出契约 + 能力路由），不是技能名单。按这个标准，公共库补了 24 条的完整契约，并新增 1 条真正走知识库的「制度查证（带出处）」；其余 30+ 个内置技能绝大多数**不该放**，原因写在第四节。

## 一、WorkBuddy 有哪些内置技能（实际盘点）

按能力域归堆，不逐个念名字：

| 能力域 | 代表技能 | 解决的问题 |
|---|---|---|
| 文档 / 表格 / 演示 | workbuddy-docx（含 writer/formatter/converter 三级链）、workbuddy-sheet-generation、workbuddy-sheetagent、workbuddy-pptx、workbuddy-pdf、workbuddy-local-office-edit | 材料的新建、编辑、格式转换、阅读理解 |
| 路由与总入口 | workbuddy-office-routing、workbuddy-finance-skill、tdoc-orchestrator | 先判断该走哪条链，再分派；不是让用户自己挑技能 |
| 知识检索 | library、knowledge_search、wecom-wedrive-reference | 先查资料库和网盘，再答，带出处 |
| 信息获取 | WebSearch、WebFetch、domain_search、conversation_search | 联网核验、跨会话回忆 |
| 多模态 | buddy-image-processing、buddy-multimodal-generation、ImageGen、VideoGen、AudioTranscribe | 图像视频生成、去水印、语音转写 |
| 发布与连接 | 发布为应用、cloud-service、mini-program-development、recommend-connectors、fbs-connector | 把产物变成在线服务 / 接外部系统 |
| 领域方法论 | bp-architect、ruishenxing-brand-story、ruishenxing-coldstart、duan-yongping-perspective | 有判断框架的专家型技能，不是通用工具 |
| 平台治理 | expert-manager、skill-creator、marketplace-skill-installer、geo-map-compliance-guard | 管理技能本身、强制合规红线 |
| 协作与支付 | tencent-docs、tencent-saas-docs、weixinpay-*、wecom-media-upload | 云文档、支付、企微文件 |

## 二、三个值得抄的范式

### 1. 技能定义写「什么时候用」，不只写名字

WorkBuddy 每个 skill 都有明确的触发描述和输入要求（例如表格技能要求传完整 `local_path`，文档转换要求传 `html_output_path`）。用户搜的是自己的话——"把这个表按地区汇总""签合同前帮我看一眼"——不是技能名。

**我们的差距**：M23 的 23 条只有 `name / description / system_prompt / default_format`。前端搜索只匹配 name+description+分组，用户得先猜我们怎么命名。

**本次落地**：24 条全部补上 `when_to_use` / `inputs_contract` / `outputs_contract`，前端搜索纳入 `when_to_use`，卡片悬停显示三行契约。字段走 `brief()` 与 `/agents` 两个接口回显，启动按代码刷新（管理员改动不会造成口径漂移）。

### 2. 技能是「能力 + 工具链」，不是一段提示词

WorkBuddy 的查资料类技能会**先检索再答**；PDF 技能会先解析文件；文档技能会走 create→insert→save→verify 的链路。提示词只是其中一环。

**我们的差距**：公共库 23 条全是 `adapter_type=llm` 的纯生成，一条都不走 `/rag/retrieve`。这正是 M22、M23 文档里两次记下的短板——「制度与流程起草」「合同与条款审阅」都在无依据生成。

**本次落地**：新增第 24 条「制度查证（带出处）」，`adapter_type=llm_retrieval`，运行时会命中 `RETRIEVAL_ADAPTERS` 先查知识库；提示词明令**只用检索片段作答、逐字引用并标文档名与条款号、查不到就说没查到、新旧冲突不替公司选**。这是公共库第一条真正挂工具链的技能。

### 3. 有路由层和红线，不靠用户自觉

WorkBuddy 用 `office-routing` / `finance-skill` 做总入口，用 `geo-map-compliance-guard` 强制触发合规检查。

**我们的现状**：平台已有 `/agents/roles` 岗位路由和审批红线（未 published 403），这一层是对齐的，不需要改。但**公共库内部没有「什么时候不该用」的提示**——本次把「查不到就说没查到」「不替公司选冲突口径」写进提示词，属于同一类红线，只是落在技能级。

## 三、平台能力对照（决定哪些能落地）

| WorkBuddy 能力 | 本平台 | 结论 |
|---|---|---|
| 知识库检索（library / knowledge_search） | `/rag/retrieve` + `/kb/documents` + `llm_retrieval` 适配器 | ✅ 本次接上 1 条 |
| 文件进（PDF/Excel/Word 上传解析） | `/agent/files` + M16 表格画像 | ✅ 已具备，公共库尚未有专门技能引导 |
| 文件出（docx/pptx/xlsx/html/pdf） | M17 五格式 + `default_format` | ✅ 已对齐 |
| 云文档协作 | 无（自研后台 + 代理下载） | ❌ 不是员工技能，属平台选型 |
| 联网核验（WebSearch / domain_search） | **无**（httpx 仅用于 OIDC 与模型网关） | ❌ 过不了「开箱即用」闸 |
| 图像 / 视频 / 3D 生成 | 无 | ❌ 平台未接模型 |
| 浏览器自动化 / 页面抓取 | 无 | ❌ 权限与合规成本高 |
| 小程序 / 云服务 / 发布应用 | 无 | ❌ 研发基建，不是办公室日常 |
| 微信支付 / 企微媒体 | 无 | ❌ 场景不相关 |
| 专家包 / 技能安装 / 地图合规 | 部分有对应（审批、审计） | ❌ 管理员动作，不该出现在员工技能库 |

**这张表是「不照抄」的依据**：本机装着的 30+ 个内置技能里，真正能被企业公共库吸收的只有「检索」「文件进出」「触发条件」三项，其余要么平台没能力，要么根本不是员工每天干的活。

## 四、明确不放进公共库的东西（以及为什么）

| 想法 | 不放的原因 |
|---|---|
| 「联网查资料 / 竞品调研」 | 平台没有搜索通道。写进提示词只会让模型编造来源，比不放更危险 |
| 「AI 配图 / 做海报」 | 无图像模型；M17 五格式里也没有图片产物 |
| 「帮我读网页 / 抓数据」 | 无浏览器自动化，且涉及外部数据合规 |
| 「一键发布成网页 / 小程序」 | 这是研发能力，不是办公室高频；且需要托管与备案 |
| 「管理技能 / 安装技能 / 配 MCP」 | 管理员动作，放进员工公共库只会增加误操作面 |
| 「微信支付 / 企微发文件」 | 与本平台业务无关 |
| 直接把 30 个内置技能名搬成 30 张卡 | 违反 M23 的四道闸和「20-25 条上限」，会变成第二个「不知道点哪个」 |

## 五、本次改了什么

**后端**
- `public_skills.py`：24 条全部带三件契约；新增 `pub_kb_qa`「制度查证（带出处）」（`llm_retrieval`）；`build_agents()` 支持单条覆盖 `adapter_type`。
- `agent_store.py`：`PACK_FIELDS`、`brief()` 回显契约；`ensure_public_skills()` 启动时同步刷新契约和 `adapter_type`。
- `tests/test_m24_skill_contract.py`：8 条，守契约非空、RAG 条目真接到运行时、两个接口回显、启动刷新、115 口径不变。

**前端**
- `Skills.tsx`：搜索纳入 `when_to_use`（可搜「签合同前」「请个假」这类自己的话）；卡片悬停显示「什么时候用 / 要给什么 / 会拿到什么」；公共库页提示语补引导；阅读组说明加「制度查证」。
- 卡片**没有加第四行文字**：M23 定的密度优先，契约放悬停，需要的人查得到，不需要的人看到的还是原样。

**结果**：23 → 24 条，6 组仍为 5/3/4/5/4/3（每组 3-5），`total` 仍 115，`public_total=24`。

## 六、实测

- 后端 **195 passed**（M24 新增 8 条）；前端 `tsc --noEmit` + `vite build` 通过。
- 接口：`catalog.total=115 / public_total=24`，24/24 条都带三件契约；`/agents?category=public` 24 条全带；`/agents/roles?role=finance` 本岗 17 + 公共库 24。
- 实调「制度查证（带出处）」：`tool_calls` 中出现 `rag_retrieve`，返回 6 条授权片段，产出 37KB docx。**检索链路是真通的**，不是只换了个标签。
- 页面：搜「签合同前」只命中「合同与条款审阅」（技能名里根本没有这几个字，靠 `when_to_use` 命中）；阅读组新增的「制度查证（带出处）」右上角显示「查制度」，与其余「生成」卡片一眼可分。

## 七、还没做的（下一步）

1. **RAG 答案质量未验**：上面只证明了接线和引用片段能取到，模型仍是占位（`[stub:glm-4.6]`），没验证「查不到时会不会老实说没查到」「新旧冲突会不会并列」。要往知识库灌 2-3 份带冲突的制度文档，等真模型再跑分。
2. **「先读文件再加工」仍是空白**：M16 已支持上传 xlsx，但公共库没有一条技能明确引导"上传已有表 → 清洗 / 合并 / 改写"。补这条要占掉 25 上限的最后一格，建议等 RAG 验收完再定。
3. **技能级红线再生一层**：WorkBuddy 用强制触发的 guard skill 管合规；本平台可以把「金额不许编」「不替审批人拍板」抽成公共硬规则注入所有公共库提示词，避免 24 份各写一遍、改一处漏一处。
4. **真模型跑分**：模型仍为占位（硅基流动余额 0），24 条提示词的实际质量无法验证。
