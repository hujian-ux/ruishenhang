"""运行时配置：从环境变量读取，提供默认值，无额外依赖。"""
import os
import pathlib

def _load_env_files(root_dir: pathlib.Path | None = None):
    """启动期把 .env 注入 os.environ。三层优先级（低 → 高）：
    1) 仓库根 .env          —— 项目级基础配置（含联网搜索等密钥）。历史版本只读了
                              backend/.env 与 backend/.env.local，导致根 .env 的
                              TAVILY/BOCHA/WEB_SEARCH 等密钥实际不生效（"配置假通"）。
    2) backend/.env         —— 后端级覆盖（通常不存在）。
    3) backend/.env.local   —— 平台运营台在线配置落盘，优先级最高。
    EAP_SKIP_ENV_LOCAL=1 时跳过 backend/.env.local（测试隔离真实厂商 key）。
    空值（KEY=）不注入，避免空串覆盖默认值。

    root_dir 默认取本文件上溯 4 级（enterprise-ai-platform/）；单测可传临时目录。
    """
    if root_dir is None:
        here = pathlib.Path(__file__).resolve()
        root_dir = here.parents[3]   # backend/app/core/config.py → 上溯 4 级 = 仓库根
    backend_dir = root_dir / "backend"
    # (path, is_local) —— is_local=True 的层始终覆盖（最高优先级）
    layers = [
        (root_dir / ".env", False),
        (backend_dir / ".env", False),
        (backend_dir / ".env.local", True),
    ]
    skip_local = os.environ.get("EAP_SKIP_ENV_LOCAL") == "1"
    for p, is_local in layers:
        if is_local and skip_local:
            continue
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if not v:
                continue  # 空值不注入，避免空串覆盖默认值
            if is_local or k not in os.environ:
                os.environ[k] = v

_load_env_files()

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
    # M26 P1：检索词三轮拆解（品类+价格+渠道）默认关；开启后竞品/政策类查询
    # 拆成多轮再合并、按来源可信度排序。置 1 才生效，对原 search() 零行为变更。
    WEB_SEARCH_DECOMPOSE = _env("WEB_SEARCH_DECOMPOSE", "0") == "1"
    # M15/M17 P1：166 个 xlsx 交付物转 few-shot，默认关；开启后运行时把样例拼进
    # 命中技能的 system_prompt，提升产出物格式一致性。置 1 才生效。
    FEWSHOT_ENABLED = _env("FEWSHOT_ENABLED", "0") == "1"
    # M55：把方案库的「验收清单/关键追问 + 领域规范 md」揉进技能，默认关；开启后
    # 运行时把验收标准（自检清单）与标准模板参考（格式范本，非数据）拼进命中技能的
    # system_prompt，出活更稳、可验收、可校验用户上传格式。置 1 才生效。
    MATERIALS_ENABLED = _env("MATERIALS_ENABLED", "0") == "1"
    # P0-4 跨会话长期记忆：默认开。开启后从每轮对话抽取持久事实（偏好/事实/约束），
    # 注入后续对话的 system_prompt，实现跨会话记忆。抽取走一次轻量 LLM 调用，
    # 模型不可用时（llm_available()=False）安全跳过，绝不报错也不阻塞响应。
    MEMORY_ENABLED = _env("MEMORY_ENABLED", "1") == "1"
    # 注入 system 的持久事实上限（超出按最近使用排序截断）
    MEMORY_MAX_FACTS_INJECT = int(_env("MEMORY_MAX_FACTS_INJECT", "8"))
    # P0-5 调度服务：默认开。开启后在 lifespan 启动 APScheduler 后台线程，
    # 注册所有 enabled 的自动化任务；模型/PG 不可用时降级为内存运行不阻塞启动。
    SCHEDULER_ENABLED = _env("SCHEDULER_ENABLED", "1") == "1"
    # 可靠投递 worker 轮询间隔（秒）；outbox 里 pending/failed 条目由它最终送达。
    OUTBOX_INTERVAL_SEC = int(_env("OUTBOX_INTERVAL_SEC", "15"))
    # outbox 单条最大重试次数（超过标记 dead，保留台账供人工重投）
    OUTBOX_MAX_ATTEMPTS = int(_env("OUTBOX_MAX_ATTEMPTS", "10"))
    # few-shot 方案库根目录（存放「原始数据 ↔ 交付物」成对 xlsx）。为空时
    # /ops/build-fewshot 回退到 os.environ 或默认 D:/workbuddy_解决方案。
    # 真实启用：指向实际 166 包语料目录；本仓附带示例语料见
    # corpus_inbox/fewshot_solutions/（仅 2 个验收样例，验证配对→挂载链路）。
    SOLUTIONS_ROOT = _env("SOLUTIONS_ROOT", "")

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
    # M51：备用模型链（逗号分隔，按顺序轮换）。主模型额度用尽/限流/下线时
    # 自动顺延到下一个，全部不可用才回退 stub。免费模型池（硅基流动）靠它续命。
    LLM_MODEL_CHAIN = _env("LLM_MODEL_CHAIN", "")
    # 轮换冷却：命中配额/余额（额度型）与限流（瞬时型）分别冷却多久才重试。
    # 额度型按天重置，冷却太短会每个请求都白打一次；限流型几十秒就恢复。
    LLM_CHAIN_QUOTA_COOLDOWN_S = float(_env("LLM_CHAIN_QUOTA_COOLDOWN_S", "1800"))
    LLM_CHAIN_RATE_COOLDOWN_S = float(_env("LLM_CHAIN_RATE_COOLDOWN_S", "60"))
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

    # ── P1-1 飞书身份源（授权码 → access_token → user_info → 平台自签 sso 令牌）──
    # 飞书不发 OIDC id_token，故不走 JWKS 验签；改为后端换码后取 user_info 验明身份，
    # 再由平台用 HMAC 自签 `sso` 会话令牌（前缀 sso，区别于 dev）。
    # sso 代表「已通过真实企业身份核验」，生产/收口态放行（属真实 SSO，非本地 dev 后门）。
    FEISHU_ENABLED = _env("FEISHU_ENABLED", "0") == "1"
    FEISHU_APP_ID = _env("FEISHU_APP_ID", "")
    FEISHU_APP_SECRET = _env("FEISHU_APP_SECRET", "")
    FEISHU_REDIRECT_URI = _env("FEISHU_REDIRECT_URI", "http://127.0.0.1:5173/callback/feishu")
    # 飞书双区域：国内 open.feishu.cn / 海外 open.larksuite.com（连接器部分复用此 BASE_URL）
    FEISHU_BASE_URL = _env("FEISHU_BASE_URL", "https://open.feishu.cn")
    # 飞书用户默认归属的租户/部门/角色（可按需落地租户映射，本期先固定 + 配置覆盖）
    FEISHU_TENANT_ID = _env("FEISHU_TENANT_ID", "")
    FEISHU_DEFAULT_ROLE = _env("FEISHU_DEFAULT_ROLE", "employee")
    FEISHU_DEFAULT_DEPT = _env("FEISHU_DEFAULT_DEPT", "d_feishu")

    # ── 微信小程序身份源（wx.login code → code2Session → 平台自签 sso 令牌）──
    # 与飞书同源：微信不发 OIDC id_token，改由后端用 code 调 code2Session 换 openid，
    # 再由平台 HMAC 自签 `sso` 会话令牌（生产/收口态放行，非 dev 后门）。
    # 默认关闭；启用需在微信公众平台拿到 AppID/AppSecret，并把后端 HTTPS 域名加入
    # 小程序后台「request 合法域名」白名单。
    WX_MINI_LOGIN_ENABLED = _env("WX_MINI_LOGIN_ENABLED", "0") == "1"
    WX_MINI_APP_ID = _env("WX_MINI_APP_ID", "")
    WX_MINI_APP_SECRET = _env("WX_MINI_APP_SECRET", "")
    WX_MINI_BASE_URL = _env("WX_MINI_BASE_URL", "https://api.weixin.qq.com")
    WX_MINI_TENANT_ID = _env("WX_MINI_TENANT_ID", "")
    WX_MINI_DEFAULT_ROLE = _env("WX_MINI_DEFAULT_ROLE", "employee")
    WX_MINI_DEFAULT_DEPT = _env("WX_MINI_DEFAULT_DEPT", "d_wx_mp")

    # P1-173 身份收口：显式置 1 时任何环境都拒绝 dev token（不止 prod），且
    # /auth/sso/callback 不再接受 dev_admin 捷径，必须走真实 SSO（带 id_token 验签）。
    # 收口默认（fail-closed）：生产环境（APP_ENV=prod）若未显式设 AUTH_ENFORCE_OIDC=0，
    # 一律强制收口，杜绝「忘记开开关就暴露 dev 后门」。非生产保持演示可用。
    _enforce_raw = _env("AUTH_ENFORCE_OIDC", None)
    AUTH_ENFORCE_OIDC = (_enforce_raw == "1") if _enforce_raw is not None else (APP_ENV == "prod")

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

    # ── 办公62 硬规则（M27 办公域治理）──
    # 默认开启观测告警（OFFICE62_ENABLED=1）；OFFICE62_ENFORCE=1 时 high 级违规
    # 直接拦截办公类 agent 产出物（与 GUARDRAILS_OUTPUT_ENFORCE 一致的 opt-in 取向）。
    # 演示环境可在 .env 设 OFFICE62_ENFORCE=1 实测拦截（见 deploy/D_LAYER_BACKLOG 子页5）。
    OFFICE62_ENABLED = _env("OFFICE62_ENABLED", "1") == "1"
    OFFICE62_ENFORCE = _env("OFFICE62_ENFORCE", "0") == "1"
    # 生产级命中率采集（opt-in，默认关）：开启后把真实 agent 产出物（答案 + 文件正文）
    # 落 data/office62_captures.jsonl，供测量台离线重扫统计生产命中率。含生产文本，
    # 仅受控 QA 环境开启，注意数据留存合规。
    OFFICE62_CAPTURE = _env("OFFICE62_CAPTURE", "0") == "1"

    # ── MCP 方向2：平台消费外部 MCP 工具 ──
    # MCP_SERVERS：JSON（数组或对象）。每个 server：{name, transport(stdio|http|sse),
    #   command/args/env 或 url/headers, timeout}。留空则不连接任何外部 MCP。
    # 例：[{"name":"brave","transport":"stdio","command":"npx","args":["-y","@modelcontextprotocol/server-brave-search"],"env":{"BRAVE_API_KEY":"x"}]
    MCP_SERVERS = _env("MCP_SERVERS", "")
    # 外部 MCP 工具白名单（默认全拒）。支持 mcp__server__tool / mcp__server__* / *。
    MCP_TOOL_ALLOW = _env("MCP_TOOL_ALLOW", "")

    # ── P1-2 MCP 服务端暴露（把平台当 MCP Server 暴露给客户 IDE/自有 Agent）──
    # HTTP 传输（streamable-http，JSON-RPC 2.0）：/api/v1/mcp/serve 暴露
    # kb_search / skill_invoke / artifact_get / list_skills。租户级鉴权复用网关
    # decode_token（含 dev / JWT / 新增 sso 前缀）；配额按租户 + 分钟桶限速。
    MCP_SERVE_ENABLED = _env("MCP_SERVE_ENABLED", "1") == "1"
    MCP_QUOTA_PER_MIN = int(_env("MCP_QUOTA_PER_MIN", "60"))  # 每租户每分钟最大工具调用次数

    FEEDBACK_ENABLED = _env("FEEDBACK_ENABLED", "1") == "1"
    EVOLUTION_REQUIRE_APPROVAL = _env("EVOLUTION_REQUIRE_APPROVAL", "1") == "1"
    # A/B 实验与灰度发布（蓝图第 20/30/41 章）：默认关，零行为变更
    AB_ENABLED = _env("AB_ENABLED", "0") == "1"
    AB_MIN_SAMPLES = int(_env("AB_MIN_SAMPLES", "30"))  # 单变体达此样本量才给选优结论
    AUDIT_RETENTION_DAYS = int(_env("AUDIT_RETENTION_DAYS", "180"))
    SECURITY_EVENT_RETENTION_DAYS = int(_env("SECURITY_EVENT_RETENTION_DAYS", "365"))
    # 法定保留动作：这些动作产生的审计记录永不过期（合规追溯，不参与自动清理）。
    AUDIT_LEGAL_HOLD_ACTIONS = _env("AUDIT_LEGAL_HOLD_ACTIONS", "approval,download,handoff")
    # 审计只读加密导出的 Fernet 密钥（base64 urlsafe 32 字节）。留空则导出为明文 CSV（并告警）。
    AUDIT_EXPORT_KEY = _env("AUDIT_EXPORT_KEY", "")

    # ── P1-11 审计哈希链（tamper-evident，非 tamper-proof）──
    # 默认开：record() 单点为每条审计写入 chain_seq / prev_hash / record_hash，
    # 旧数据（无链字段）视为 legacy 不判失败；设 0 则完全回到 M13 写入行为。
    AUDIT_CHAIN_ENABLED = _env("AUDIT_CHAIN_ENABLED", "1") == "1"
    # 留空用 SHA-256（可检测篡改，但持有 DB 权限者可离线重算整条链）；
    # 配置后用 HMAC-SHA256，密钥不入库，可把检测能力提升为「需持密钥才能伪造」。
    AUDIT_CHAIN_KEY = _env("AUDIT_CHAIN_KEY", "")
    AUDIT_CHAIN_VERIFY_MAX = int(_env("AUDIT_CHAIN_VERIFY_MAX", "20000"))

    # ── MLOps 自动重训调度（P2 真实管线）──
    # 默认关：漂移检测 + 重训建议钩子始终可用；仅当显式开 MLOPS_AUTO_RETRAIN=1 时，
    # /mlops/retrain 与 scripts/mlops_scheduler.py 才会在检测到漂移后真正训练并可选晋升。
    MLOPS_AUTO_RETRAIN = _env("MLOPS_AUTO_RETRAIN", "0") == "1"

    OBSERVABILITY_ENABLED = _env("OBSERVABILITY_ENABLED", "1") == "1"
    OPS_ALERT_ERROR_RATE = float(_env("OPS_ALERT_ERROR_RATE", "0.01"))
    OPS_ALERT_P95_MS = float(_env("OPS_ALERT_P95_MS", "3000"))
    OPS_ALERT_MODEL_429_SPIKE = int(_env("OPS_ALERT_MODEL_429_SPIKE", "10"))

    SEMANTIC_CACHE_ENABLED = _env("SEMANTIC_CACHE_ENABLED", "1") == "1"
    SEMANTIC_CACHE_THRESHOLD = float(_env("SEMANTIC_CACHE_THRESHOLD", "0.92"))
    SEMANTIC_CACHE_EMBED_THRESHOLD = float(_env("SEMANTIC_CACHE_EMBED_THRESHOLD", "0.86"))
    SEMANTIC_CACHE_HOT_HITS = int(_env("SEMANTIC_CACHE_HOT_HITS", "3"))
    # 缓存命中是否真复用答案（跳过模型调用，省成本）。默认关：先只观测命中率，
    # 待 golden 回归确认命中质量后再开；与 GUARDRAILS_OUTPUT_ENFORCE / HANDOFF_AUTO 同策略。
    SEMANTIC_CACHE_SERVE = _env("SEMANTIC_CACHE_SERVE", "0") == "1"
    SEMANTIC_CACHE_TTL_S = int(_env("SEMANTIC_CACHE_TTL_S", "3600"))
    SEMANTIC_CACHE_MAX_ENTRIES = int(_env("SEMANTIC_CACHE_MAX_ENTRIES", "500"))

    ROUTING_ENABLED = _env("ROUTING_ENABLED", "1") == "1"
    ROUTING_SMALL_MODEL = _env("ROUTING_SMALL_MODEL", "glm-4.6-air")
    ROUTING_LARGE_MODEL = _env("ROUTING_LARGE_MODEL", "glm-4.6")
    ROUTING_SMALL_TIER_PRICE = float(_env("ROUTING_SMALL_TIER_PRICE", "0.0005"))
    ROUTING_LARGE_TIER_PRICE = float(_env("ROUTING_LARGE_TIER_PRICE", "0.005"))

    # 网关节流（令牌桶削峰）：默认关闭，零行为变更；开启后对 chat 调用限速。
    # >0 时按 令牌/秒 平滑放行，突发余量 = ROUTING_THROTTLE_BURST；超限抛 RateLimited(429)。
    GATEWAY_THROTTLE_RPS = float(_env("GATEWAY_THROTTLE_RPS", "0"))
    GATEWAY_THROTTLE_BURST = float(_env("GATEWAY_THROTTLE_BURST", "20"))

    # HTTP 层网关节流（A 层安全收尾）：按客户端 IP 令牌桶，保护认证/Webhook 端点免暴力与资源耗尽。
    # 默认全关（=0）；生产经 _fail_if_insecure_for_prod 强制开启。认证/集成端点用更严格的 AUTH_* 桶。
    HTTP_RATE_LIMIT_RPS = float(_env("HTTP_RATE_LIMIT_RPS", "0"))
    HTTP_RATE_LIMIT_BURST = float(_env("HTTP_RATE_LIMIT_BURST", "200"))
    HTTP_AUTH_RATE_LIMIT_RPS = float(_env("HTTP_AUTH_RATE_LIMIT_RPS", "0"))
    HTTP_AUTH_RATE_LIMIT_BURST = float(_env("HTTP_AUTH_RATE_LIMIT_BURST", "30"))

    # 中文特化：长度感知切分（蓝图第 43 章）。默认关，沿用原「一句话一块」；
    # 开启后改用 zh_chunk 合并碎句/劈开超长句，改善检索块粒度。
    ZH_CHUNK_ENABLED = _env("ZH_CHUNK_ENABLED", "0") == "1"
    ZH_CHUNK_MAX_LEN = int(_env("ZH_CHUNK_MAX_LEN", "120"))
    ZH_CHUNK_MIN_LEN = int(_env("ZH_CHUNK_MIN_LEN", "12"))

    CONFIDENCE_THRESHOLD = float(_env("CONFIDENCE_THRESHOLD", "0.45"))
    HANDOFF_ENABLED = _env("HANDOFF_ENABLED", "1") == "1"
    DEPT_SELF_SERVICE = _env("DEPT_SELF_SERVICE", "1") == "1"
    WORKFLOW_MAX_NODES = int(_env("WORKFLOW_MAX_NODES", "12"))
    WEBHOOK_TOKEN_SECRET = _env("WEBHOOK_TOKEN_SECRET", "dev-only-change-me")
    WEBHOOK_TIMEOUT_S = float(_env("WEBHOOK_TIMEOUT_S", "3.0"))
    # CORS（A 层安全收尾）：逗号分隔的前端域名白名单；默认仅放本机前端（开发期）。
    # 生产必须显式配置真实域名；含 * 会在启动自检被拒（不允许「通配+凭据」跨站）。
    CORS_ALLOW_ORIGINS = _env(
        "CORS_ALLOW_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")
    # IM 回调重放防护窗口（秒）：默认 300s；置 0 关闭（开发/测试）。钉钉毫秒时间戳自动兼容。
    IM_REPLAY_WINDOW_S = int(_env("IM_REPLAY_WINDOW_S", "300"))
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

    # M48：审批助手回推 IM/OA 卡片时，审批详情页的绝对链接前缀。
    # 企微/钉钉卡片里的「点击处理」必须是可被员工浏览器打开的公网/内网地址；
    # 留空则卡片只带摘要、不带跳转链接（适合仅查看场景）。
    APPROVAL_UI_BASE_URL = _env("APPROVAL_UI_BASE_URL", "")

    # P1-9 子代理 / 专家执行：委派深度上限（防递归）、单次并发上限、任务/上下文字长上限。
    # 深度 2 表示「顶层委派(depth=0) → 工具内委派(depth=1)」两层；再深直接拒。
    SUBAGENT_MAX_DEPTH = int(_env("SUBAGENT_MAX_DEPTH", "2"))
    SUBAGENT_MAX_BATCH = int(_env("SUBAGENT_MAX_BATCH", "6"))
    SUBAGENT_MAX_TASK_CHARS = int(_env("SUBAGENT_MAX_TASK_CHARS", "4000"))
    SUBAGENT_MAX_CONTEXT_CHARS = int(_env("SUBAGENT_MAX_CONTEXT_CHARS", "6000"))

    # ── P1-10 多模态图像理解（可插拔通道 + 配置开关）──────────────────────────
    # 默认全关：不读图片、不联网、不改 messages，与 P1-9 之前的行为逐字节一致。
    # 开启方式（示例）：
    #   VISION_ENABLED=1 VISION_PROVIDER=openai_compatible \
    #   VISION_MODEL=glm-4v-flash VISION_BASE_URL=https://open.bigmodel.cn/api/paas/v4 \
    #   VISION_API_KEY=<key>
    # VISION_BASE_URL / VISION_API_KEY 留空时回落直连 LLM 通道的同名配置，
    # 这样「同一个厂商同时提供文本与视觉模型」时不必重复配两遍。
    VISION_ENABLED = _env("VISION_ENABLED", "0") == "1"
    VISION_PROVIDER = _env("VISION_PROVIDER", "stub").strip().lower()
    VISION_MODEL = _env("VISION_MODEL", "").strip()
    VISION_BASE_URL = _env("VISION_BASE_URL", "").strip()
    VISION_API_KEY = _env("VISION_API_KEY", "").strip()
    VISION_MAX_IMAGES = int(_env("VISION_MAX_IMAGES", "4"))
    VISION_MAX_IMAGE_BYTES = int(_env("VISION_MAX_IMAGE_BYTES", str(5 * 1024 * 1024)))
    VISION_MAX_PROMPT_CHARS = int(_env("VISION_MAX_PROMPT_CHARS", "2000"))
    VISION_MAX_TOKENS = int(_env("VISION_MAX_TOKENS", "1200"))
    VISION_TIMEOUT_S = float(_env("VISION_TIMEOUT_S", "60"))

    # ── P1-12 上下文窗口配置 + 压缩（默认逐字节零行为变更）────────────────────
    # 窗口口径统一到此处，取代散落在 session_store / agent_runtime 的硬编码：
    #   CTX_SESSION_TURNS  会话内最多保留多少轮（滑动窗口，超出丢最早）
    #   CTX_WINDOW_TURNS   会话详情 / 无覆盖时注入生成器的近窗轮数
    #   CTX_RUNTIME_TURNS  生成器（5 条路径）实际注入的近窗轮数
    # 默认值逐一对齐改动前的 40 / 6 / 2，开关全关时输出逐字节一致。
    CTX_SESSION_TURNS = int(_env("CTX_SESSION_TURNS", "40"))
    CTX_WINDOW_TURNS = int(_env("CTX_WINDOW_TURNS", "6"))
    CTX_RUNTIME_TURNS = int(_env("CTX_RUNTIME_TURNS", "2"))
    # 请求级 context_window 覆盖的夹取上下限（防止一次请求把上下文撑爆）
    CTX_MIN_TURNS = int(_env("CTX_MIN_TURNS", "1"))
    CTX_MAX_TURNS = int(_env("CTX_MAX_TURNS", "40"))
    # 压缩：默认关。开启后被窗口截掉的早期轮次不再彻底丢失，而是压成摘要注入。
    #   extractive = 纯确定性抽取（零依赖 / 零成本 / 零外部调用）
    #   llm        = 调模型网关，失败或无真模型一律回落 extractive 并标注 fallback，
    #                绝不把 stub 文本当摘要（M55 缺料必拒）
    CTX_COMPRESS_ENABLED = _env("CTX_COMPRESS_ENABLED", "0") == "1"
    CTX_COMPRESS_PROVIDER = _env("CTX_COMPRESS_PROVIDER", "extractive").strip().lower()
    CTX_COMPRESS_MAX_CHARS = int(_env("CTX_COMPRESS_MAX_CHARS", "1200"))
    CTX_COMPRESS_TURN_CHARS = int(_env("CTX_COMPRESS_TURN_CHARS", "160"))
    CTX_COMPRESS_MAX_TOKENS = int(_env("CTX_COMPRESS_MAX_TOKENS", "300"))


settings = Settings()
