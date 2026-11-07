"""运行时配置：从环境变量读取，提供默认值，无额外依赖。"""
import os

def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)

class Settings:
    APP_NAME = _env("APP_NAME", "enterprise-ai-platform")
    APP_ENV = _env("APP_ENV", "dev")
    API_PREFIX = _env("API_PREFIX", "/api/v1")

    PG_HOST = _env("PG_HOST", "127.0.0.1")
    PG_PORT = int(_env("PG_PORT", "5432"))
    PG_DB = _env("PG_DB", "aiplat")
    PG_USER = _env("PG_USER", "app")
    PG_PASSWORD = _env("PG_PASSWORD", "dev")

    QDRANT_HOST = _env("QDRANT_HOST", "127.0.0.1")
    QDRANT_PORT = int(_env("QDRANT_PORT", "6333"))

    MINIO_ENDPOINT = _env("MINIO_ENDPOINT", "127.0.0.1:9000")
    MINIO_USER = _env("MINIO_USER", "minioadmin")
    MINIO_PASSWORD = _env("MINIO_PASSWORD", "minioadmin")
    MINIO_BUCKET = _env("MINIO_BUCKET", "kb-files")

    REDIS_URL = _env("REDIS_URL", "redis://127.0.0.1:6379/0")

    LITELLM_BASE_URL = _env("LITELLM_BASE_URL", "http://127.0.0.1:4000")
    # 可选：LiteLLM 代理主密钥（新版代理默认要求鉴权）。本地开发可留空，
    # 并给 litellm 设 LITELLM_DANGEROUSLY_PERMIT_WEAK_OR_UNSET_MASTER_KEY=true。
    LITELLM_MASTER_KEY = _env("LITELLM_MASTER_KEY", "")

    # ── M26 联网搜索通道（公网事实获取，与内部 RAG 完全隔离）──
    # auto = 有官方 key 就用官方 API，一个都没配走免费网页抓取（搜狗 → Bing 互为备份）。
    # 中文长 query 实测搜狗相关性更好；生产务必配 key，网页抓取依赖对方页面结构。
    WEB_SEARCH_ENABLED = _env("WEB_SEARCH_ENABLED", "1") == "1"
    WEB_SEARCH_PROVIDER = _env("WEB_SEARCH_PROVIDER", "auto").strip().lower()
    WEB_SEARCH_TIMEOUT_S = float(_env("WEB_SEARCH_TIMEOUT_S", "10"))
    WEB_SEARCH_MAX_RESULTS = int(_env("WEB_SEARCH_MAX_RESULTS", "6"))
    WEB_SEARCH_FETCH_CHARS = int(_env("WEB_SEARCH_FETCH_CHARS", "3000"))
    # 是否允许抓取搜索结果正文（竞品调研需要）。抓之前有 URL 白名单 + DNS 校验。
    WEB_SEARCH_ALLOW_FETCH = _env("WEB_SEARCH_ALLOW_FETCH", "1") == "1"
    # 逗号分隔的域名黑名单，命中的结果直接丢弃
    WEB_SEARCH_BLOCK_DOMAINS = _env("WEB_SEARCH_BLOCK_DOMAINS", "")
    BING_SEARCH_KEY = _env("BING_SEARCH_KEY", "")          # Bing Web Search v7
    BOCHA_API_KEY = _env("BOCHA_API_KEY", "")              # 博查（中文）
    SERPER_API_KEY = _env("SERPER_API_KEY", "")            # Serper / Google
    TAVILY_API_KEY = _env("TAVILY_API_KEY", "")            # Tavily
    SEARXNG_BASE_URL = _env("SEARXNG_BASE_URL", "")        # 自建 SearXNG，如 http://host:8080

    # ── M17 真模型接入：两种通道，业务代码不感知 ──
    # litellm —— 经 LiteLLM 代理（多厂商统一账单，生产推荐）
    # direct  —— 直连厂商 OpenAI 兼容端点（单厂商起步最快，不额外起容器）
    LLM_PROVIDER = _env("LLM_PROVIDER", "litellm").strip().lower()
    # 直连通道的端点与密钥。LLM_PROVIDER=direct 时生效；
    # 留空则按 VENDOR 取默认端点（见 model_gateway.PROVIDER_PRESETS）。
    LLM_VENDOR = _env("LLM_VENDOR", "zhipu").strip().lower()
    LLM_BASE_URL = _env("LLM_BASE_URL", "")      # 例：https://open.bigmodel.cn/api/paas/v4
    LLM_API_KEY = _env("LLM_API_KEY", "")        # 留空则按 VENDOR 读对应环境变量
    # M6：对话默认路由的 model_id；探活通过走真生成，否则 stub
    LLM_DEFAULT_MODEL = _env("LLM_DEFAULT_MODEL", "glm-4.6")
    # 生成参数：技能包要吐多张表，输出上限给小了会被截断成半张表
    LLM_MAX_TOKENS = int(_env("LLM_MAX_TOKENS", "8192"))
    # 财务归因类任务要可复现，温度压到 0.2（同输入两次跑结论应当一致）
    LLM_TEMPERATURE = float(_env("LLM_TEMPERATURE", "0.2"))
    LLM_TIMEOUT_S = float(_env("LLM_TIMEOUT_S", "180"))
    # 真模型失败时是否直接报错（1）还是回退确定性 stub（0）。
    # 默认 0 保开发/CI 可用，但**验收与生产必须置 1** —— 静默 stub 会把
    # 「链路通了」误当成「效果达标」，这正是 M17 要堵的坑。
    LLM_STRICT = _env("LLM_STRICT", "0") == "1"
    # GLM-4.5/4.6 默认混合思考。财务归因/表格抽取这类结构化任务关掉思考
    # 更稳更快也更省 token（思考 token 也计费）。要推理能力时置 0。
    LLM_DISABLE_THINKING = _env("LLM_DISABLE_THINKING", "1") == "1"
    # 牌价覆盖：{"glm-4.6": [0.004, 0.014]}（元/1k tokens，[输入, 输出]）
    MODEL_PRICES_JSON = _env("MODEL_PRICES_JSON", "")

    # M7 计量配额：部门滚动 30 天 token 上限（0 = 不限额）。超限调用直接 429，
    # 不是事后告警——计费在调用链路上是红线，不只是报表。
    USAGE_QUOTA_TOKENS = int(_env("USAGE_QUOTA_TOKENS", "2000000"))

    # M8 试点：月度成本预算线（CNY，0 = 不设预算线）。趋势页按此画水位与月末外推，
    # 超过 80% 预警、100% 标红——两周试点靠它判断"这个部门到底烧不烧得起"。
    USAGE_MONTHLY_BUDGET_CNY = float(_env("USAGE_MONTHLY_BUDGET_CNY", "500"))
    # M8 批量导入：单次扫描文件数上限、入索引字符上限（超出部分只存原文不建索引）
    INGEST_MAX_FILES = int(_env("INGEST_MAX_FILES", "1000"))
    INGEST_INDEX_CHAR_CAP = int(_env("INGEST_INDEX_CHAR_CAP", "20000"))
    # 入库防投毒：命中强注入信号的文档不入库（计入 poisoned 清单）。默认开。
    INGEST_REJECT_POISONED = _env("INGEST_REJECT_POISONED", "1") == "1"
    # M8 试点：是否加载 18 篇演示语料。真实语料进场后必须设 0，
    # 否则演示文档会参与竞争、稀释真实召回（实测会把 Recall@10 抬高成假象）。
    SEED_DEMO_CORPUS = _env("SEED_DEMO_CORPUS", "1") == "1"

    # 决策：中文 Embedding 选 bge（bge-m3，维度 1024）
    # 真实模型开关：默认 0（确定性伪向量，离线/CI 稳定，不会触发 HF 下载）。
    # 开 1 时本地须已缓存模型（或设 HF_ENDPOINT=https://hf-mirror.com 走国内镜像）。
    USE_LOCAL_EMBEDDING = _env("USE_LOCAL_EMBEDDING", "0") == "1"
    EMBEDDING_MODEL = _env("EMBEDDING_MODEL", "bge-m3")
    EMBEDDING_DIM = int(_env("EMBEDDING_DIM", "1024"))
    # 真实模型的编码批大小。CPU 上 16 比 8 快约 40%，比 32 稳（内存/缓存友好）。
    EMBED_BATCH_SIZE = int(_env("EMBED_BATCH_SIZE", "16"))
    # 重排层 cross-encoder（第36章 W2）；未安装时退化为启发式打分
    RERANK_MODEL = _env("RERANK_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2")

    # M9 规模化：Embedding 结果持久化缓存（sqlite 单文件）。
    # 重启载回 5000 篇时，命中缓存 = 不重算向量；真 bge 模型下这是分钟级 vs 秒级的差别。
    # 缓存键含模型名与维度，换模型自然失效，不会串味。
    EMBED_CACHE = _env("EMBED_CACHE", "1") == "1"
    EMBED_CACHE_PATH = _env("EMBED_CACHE_PATH", "data/embed_cache.db")
    # 批量写入的落盘节流（条数）：攒够再一次性提交，避免每条一个事务
    EMBED_CACHE_FLUSH_EVERY = int(_env("EMBED_CACHE_FLUSH_EVERY", "500"))

    # M9 向量后端：memory（numpy 内存矩阵，默认）| qdrant | auto（远程可达则用 qdrant）
    # 本地没装 Docker 也能验证 qdrant 路径：QDRANT_MODE=local 用 qdrant-client 的文件模式，
    # 跑的是真实 Qdrant 引擎，只是不占端口。生产切远程只改 QDRANT_MODE=http。
    VECTOR_BACKEND = _env("VECTOR_BACKEND", "memory")
    QDRANT_MODE = _env("QDRANT_MODE", "http")
    QDRANT_PATH = _env("QDRANT_PATH", "data/qdrant")
    QDRANT_COLLECTION = _env("QDRANT_COLLECTION", "kb_chunks")
    # 5s 而非 1s：建集合 + 首批批量写入在冷启动（尤其 Windows 落盘）会超过 1 秒，
    # 1 秒超时会让「第一次建库」直接失败并降级。检索单次仍在几十毫秒量级。
    QDRANT_TIMEOUT_S = float(_env("QDRANT_TIMEOUT_S", "5.0"))
    # Qdrant 写入默认同步（wait=True）。异步（wait=False）更快，但「写完立刻检索」
    # 会看到不完整的集合——实测 Recall@100 从 1.000 掉到 0.948，直接跌破第36章门禁。
    # 上传后立刻召回是产品主路径，所以默认同步；批量重建索引时才值得关掉。
    QDRANT_ASYNC_UPSERT = _env("QDRANT_ASYNC_UPSERT", "0") == "1"

    # M9 规模化：索引内存上限保护。超过该子块数时检索自动收窄候选
    # （向量全扫仍保留，BM25 只走倒排候选），避免单机被超大语料拖垮。
    INDEX_MAX_CHILDREN = int(_env("INDEX_MAX_CHILDREN", "200000"))
    # tombstone 占比超过该比例时压缩索引（删除多、增量少时回收内存）
    INDEX_COMPACT_RATIO = float(_env("INDEX_COMPACT_RATIO", "0.25"))

    CALLER_SIGNING_SECRET = _env("CALLER_SIGNING_SECRET", "dev-only-change-me")
    DEFAULT_TENANT = _env("DEFAULT_TENANT", "t_demo")

    # M12 真实身份（企业 SSO / OIDC）。全部留空 = 不启用，仅 dev token 可用（测试/演示）。
    # 已选定 Casdoor 作为标准 OIDC 中间件（Apache-2.0 开源、自托管免费、原生接企业微信/钉钉）。
    # 对接方式：填 issuer 即可，本平台用标准 discovery + JWKS 验签（RS256），无需写死各厂商 SDK。
    # Casdoor 默认部署：后端/OIDC 在 8000 端口，issuer 即其访问地址（如 http://127.0.0.1:8000）。
    OIDC_ENABLED = _env("OIDC_ENABLED", "0") == "1"
    OIDC_ISSUER = _env("OIDC_ISSUER", "")          # 例：http://127.0.0.1:8000（Casdoor 地址，须与 token 的 iss 完全一致）
    OIDC_AUDIENCE = _env("OIDC_AUDIENCE", "")       # 本平台在 Casdoor 注册的 Application 的 Client ID（验签时的 aud）
    # 中转换码端点用：向后端 Casdoor 换码时填的 client_id（未填则回退 OIDC_AUDIENCE）
    OIDC_CLIENT_ID = _env("OIDC_CLIENT_ID", "")
    # 前端回调地址（需与 Casdoor 应用登记的 Redirect URLs 完全一致）。前端也可在 Setup 页/请求里覆盖。
    OIDC_REDIRECT_URI = _env("OIDC_REDIRECT_URI", "http://127.0.0.1:5173/callback")
    OIDC_DISCOVERY_TIMEOUT = float(_env("OIDC_DISCOVERY_TIMEOUT", "3.0"))
    # claim 映射（不同 IdP 字段名不同，按你的 IdP 调整）
    # 用户名：优先下面字段，其次 preferred_username / email，最后 sub（保证非空）
    OIDC_USER_CLAIM = _env("OIDC_USER_CLAIM", "preferred_username")
    # 部门/组织：Casdoor 把组织名放在 affiliation 字段，故默认取它；其它 IdP 改成自己的部门字段
    OIDC_DEPT_CLAIM = _env("OIDC_DEPT_CLAIM", "affiliation")
    # 角色：若 IdP 直接下发 employee|dept_admin|platform_admin 字符串/数组则用之；
    #       否则按 Casdoor 布尔标志推导（isGlobalAdmin→platform_admin，isAdmin→dept_admin）。
    OIDC_ROLE_CLAIM = _env("OIDC_ROLE_CLAIM", "role")
    # 租户（可选）：留空则回退 DEFAULT_TENANT；Casdoor 多组织可映射到 organization 字段
    OIDC_TENANT_CLAIM = _env("OIDC_TENANT_CLAIM", "")
    OIDC_NAME_CLAIM = _env("OIDC_NAME_CLAIM", "name")

    # 演示/开发 token 有效期（秒）。默认 12 小时：默认 2 小时会让演示到一半
    # 突然全线 401「missing caller context」，看不出是过期。
    DEV_TOKEN_TTL_S = int(_env("DEV_TOKEN_TTL_S", "43200"))

    # ── M27 蓝图缺口收口（第 19/20/21/26/27/28/29/30/31/34/38/39/40/41/42/43/44 章）──
    # 总原则：默认「记录 + 提示」，不默认改变既有 248 条测试依赖的行为；
    # 只有 *_STRICT=1 才阻断。生产环境应由运维显式打开严格模式。
    GUARDRAILS_ENABLED = _env("GUARDRAILS_ENABLED", "1") == "1"
    GUARDRAILS_STRICT = _env("GUARDRAILS_STRICT", "0") == "1"
    GUARDRAILS_SOURCE_ALLOWLIST = _env("GUARDRAILS_SOURCE_ALLOWLIST", "")  # 逗号分隔可信域名
    GUARDRAILS_TOOL_AUTH = _env("GUARDRAILS_TOOL_AUTH", "1") == "1"
    GUARDRAILS_OUTPUT_MASK = _env("GUARDRAILS_OUTPUT_MASK", "1") == "1"
    # 输出严格改写（脱敏后答案真正落回响应）。默认关：仅观测，避免把依赖答案原文的验收断言悄悄改掉。
    GUARDRAILS_OUTPUT_ENFORCE = _env("GUARDRAILS_OUTPUT_ENFORCE", "0") == "1"
    GUARDRAILS_MAX_INJECTION_HITS = int(_env("GUARDRAILS_MAX_INJECTION_HITS", "3"))

    FEEDBACK_ENABLED = _env("FEEDBACK_ENABLED", "1") == "1"
    EVOLUTION_REQUIRE_APPROVAL = _env("EVOLUTION_REQUIRE_APPROVAL", "1") == "1"
    AUDIT_RETENTION_DAYS = int(_env("AUDIT_RETENTION_DAYS", "180"))
    SECURITY_EVENT_RETENTION_DAYS = int(_env("SECURITY_EVENT_RETENTION_DAYS", "365"))

    OBSERVABILITY_ENABLED = _env("OBSERVABILITY_ENABLED", "1") == "1"
    OPS_ALERT_ERROR_RATE = float(_env("OPS_ALERT_ERROR_RATE", "0.01"))
    OPS_ALERT_P95_MS = float(_env("OPS_ALERT_P95_MS", "3000"))
    OPS_ALERT_MODEL_429_SPIKE = int(_env("OPS_ALERT_MODEL_429_SPIKE", "10"))

    SEMANTIC_CACHE_ENABLED = _env("SEMANTIC_CACHE_ENABLED", "1") == "1"
    SEMANTIC_CACHE_THRESHOLD = float(_env("SEMANTIC_CACHE_THRESHOLD", "0.92"))
    SEMANTIC_CACHE_TTL_S = int(_env("SEMANTIC_CACHE_TTL_S", "3600"))
    SEMANTIC_CACHE_MAX_ENTRIES = int(_env("SEMANTIC_CACHE_MAX_ENTRIES", "500"))

    ROUTING_ENABLED = _env("ROUTING_ENABLED", "1") == "1"
    ROUTING_SMALL_MODEL = _env("ROUTING_SMALL_MODEL", "glm-4.6-air")
    ROUTING_LARGE_MODEL = _env("ROUTING_LARGE_MODEL", "glm-4.6")
    ROUTING_SMALL_TIER_PRICE = float(_env("ROUTING_SMALL_TIER_PRICE", "0.0005"))
    ROUTING_LARGE_TIER_PRICE = float(_env("ROUTING_LARGE_TIER_PRICE", "0.005"))

    CONFIDENCE_THRESHOLD = float(_env("CONFIDENCE_THRESHOLD", "0.45"))
    HANDOFF_ENABLED = _env("HANDOFF_ENABLED", "1") == "1"
    DEPT_SELF_SERVICE = _env("DEPT_SELF_SERVICE", "1") == "1"
    WORKFLOW_MAX_NODES = int(_env("WORKFLOW_MAX_NODES", "12"))
    WEBHOOK_TOKEN_SECRET = _env("WEBHOOK_TOKEN_SECRET", "dev-only-change-me")
    WEBHOOK_TIMEOUT_S = float(_env("WEBHOOK_TIMEOUT_S", "3.0"))
    # 岗位由组织下发：1 = 服务端按 deptId 解析岗位，忽略前端自选的越权岗位
    ROLE_FROM_ORG = _env("ROLE_FROM_ORG", "0") == "1"
    # deptId → 岗位映射（组织侧真实数据到位后改这里 / 换成 IdP 下发）
    ORG_DEPT_ROLE_MAP = _env(
        "ORG_DEPT_ROLE_MAP",
        "d_sales:sales,d_finance:finance,d_hr:hr,d_admin:admin,d_marketing:marketing,"
        "d_production:manufacturing,d_crossborder:crossborder,d_it:it,d_design:design,"
        "d_procurement:procurement,d_service:service,d_quality:quality,d_platform:admin",
    )

    # M27 默认只「观测 + 建议」，不改变既有生成结果；低置信自动转人工需显式打开
    HANDOFF_AUTO = _env("HANDOFF_AUTO", "0") == "1"


settings = Settings()
