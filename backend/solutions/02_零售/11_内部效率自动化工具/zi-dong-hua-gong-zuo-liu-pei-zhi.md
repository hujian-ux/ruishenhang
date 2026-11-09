# 自动化工作流配置 · 内部效率自动化工具（r4-2）

> 面向 Agency 运营部 12 人团队。把高频重复劳动编排成可定时触发、凭证托管、带三层容错与分级告警的自动化工作流。
> 对齐效益：**12 人团队年省约 4,625 小时（≈2.4 FTE）**，投资回收期 ≈ 3 个月，三年净收益 ≈ 165 万。

## 一、工作流一句话搭建说明

1. **选型**：列出团队高频重复操作，评估日耗时/出错风险，优先搭 1-3 条（日报 / 工单聚类 / 素材归档）。
2. **调度**：写 `cron + jitter + timezone + owner + sla`，避峰触发。
3. **凭证**：全部用系统专属应用密钥，走 Secret Vault（KMS），最小权限（仅 GET/SELECT），90-365d 分级轮换，双人审批，审计日志。
4. **编排**：多源拉取 → JOIN/计算 → AI 摘要/聚类/识别 → 渲染 → 推送/归档，每步声明 `connector` 与 `output` 变量。
5. **容错告警**：L1 步内指数退避；L2 步间降级；L3 工作流回滚 + P1 告警；配 P0/P1/P2 三级通道。
6. **可观测**：runtime 日志入 ClickHouse（180 天），audit 日志入 WORM（3 年），Grafana + OpenTelemetry 监控成功率/p95/单次成本。

---

## 二、工作流一：每日运营数据日报

### 触发与调度
```yaml
workflow_id: wf_daily_ops_report
schedule:
  cron: "0 30 8 * * MON-FRI"   # 工作日 08:30
  timezone: Asia/Shanghai
  jitter: 0-60s                # 抖动避峰
owner: ops-platform@agency.internal
sla: "08:45 前必须完成推送"
```

### 7 步执行（关键片段）
```yaml
steps:
  - id: s1_pull_wecom
    connector: wework_kf_api
    action: GET /cgi-bin/kf/statistic
    output: {var: wecom_data}
  - id: s2_pull_youzan
    connector: youzan_open_api
    action: order.list.get
    output: {var: ecom_data}
  - id: s3_pull_crm
    connector: mysql_readonly
    sql: SELECT channel, lead_cnt FROM dws_private_conversion WHERE dt = CURRENT_DATE - INTERVAL 1 DAY
  - id: s4_join_and_calc      # 三源 JOIN + 环比同比 + 3σ 异常
  - id: s5_ai_summary         # LLM 生成 3 句点评
  - id: s6_render_markdown    # 渲染日报 MD + 图表
  - id: s7_push_wecom_group   # 推企微群 + @负责人
```

### 凭证管理
```yaml
credential_management:
  vault: WorkBuddy Secret Vault (KMS 加密)
  rotation: 90-365d 分级
  principles:
    - 员工个人账号 ≠ 工作流账号，全部使用系统专属应用密钥
    - 最小权限：只赋 GET / SELECT，禁止 DELETE / UPDATE
    - 双人审批：新增或轮换密钥需两人确认
    - 审计日志：每次读取自动打点，7 天异常访问告警
```

### 日报输出示例
```
📊 私域运营日报 · 2026-07-29（周三）
核心指标：新增私域客户 1,284（+8.3%）· GMV ¥386,450（-3.1%）· 响应率 96.4% · 私域转化 4.87% · 退款 ¥12,340（+42.5% 异常）
分区表现：华东 189K 领跑 · 华南 121K 下滑 -8.4% · 华北 76K 新客占比创新高
AI 点评：亮点 - 华北新客占比 37%，7 月投放策略见效；隐忧 - 华南 GMV 连续 3 日下滑 + 退款激增，建议核查夏季清仓链接是否有 SKU 错配；今日建议 - 全员优先跟进华南工单，客服排班上调 20%。
📎 本报告由 WorkBuddy 于 08:44:12 自动生成
```

---

## 三、工作流二：客户工单周分类统计

### AI 聚类算法配置
```yaml
clustering:
  step1_embedding:
    model: bge-large-zh-v1.5
    dim: 1024
  step2_cluster:
    algorithm: HDBSCAN          # 自适应聚类数
    min_cluster_size: 8        # 少于 8 条为"长尾"
    metric: cosine
    fallback: KMeans(k=auto_elbow)
  step3_label:
    model: workbuddy-llm
    prompt: "基于 5 条代表性工单，起 6 字类目名 + 一句摘要"
    temperature: 0.3
  step4_hierarchy:
    l1: [售前咨询, 售后问题, 物流投诉, 产品建议, 其他]
```

### 话术优化建议样例
```
🔴 高优类目：尺码不合退换 (87 单, +23% WoW)
问题诊断：63% 的对话中客服跳过了"确认穿着体型"环节，直接建议退货，导致挽回率仅 12%（行业基线 28%）。
推荐话术（挽回型）："亲，我们这款版型偏[大/小]约半码，能否告诉我您平时穿的[品牌]是什么码？我帮您对照一下，如果换个尺码合适，我们免运费直接换～"
本周培训要点：① 尺码对照表工具使用 ② 挽回话术演练 ③ 免运费权限申请流程
```

---

## 四、工作流三：素材批量重命名归档

### 命名规则
```yaml
naming_template: "{活动代码}_{渠道}_{素材类型}_{日期YYYYMMDD}_{尺寸or时长}_v{版本}.{ext}"
examples:
  - SUMMER2607_WECHAT_POSTER_20260729_1080x1920_v2.jpg
  - MIDYEAR_DOUYIN_VIDEO_20260729_15s_v1.mp4
  - JULYNEW_XHS_ARTICLE_20260729_v3.md
```

### 目录方案
```yaml
directory_scheme:
  root: oa://marketing_assets/
  hierarchy: "{年}/{月}/{活动代码}/{渠道}/{素材类型}/"
  example: oa://marketing_assets/2026/07/SUMMER2607/WECHAT/POSTER/
  auto_create:
    - 目录不存在 → 自动创建 + 写 .metadata.json
    - 容量 > 5GB → 告警 + 提示归档到冷存储
    - 每月末自动生成 monthly_index.md
```

### 主流程伪代码
```python
@workflow(id="wf_asset_auto_archive")
def archive_assets(event):
    for f in event.new_files:
        meta = classify(f)                       # 1. AI 识别
        if meta.confidence < 0.75:
            move_to_review_queue(f); continue
        if similar_exists(f, threshold=0.95):
            notify_uploader(f, reason="重复素材"); continue
        new_name = render_template(NAMING_TPL, meta)   # 2. 命名
        target_dir = ensure_dir(DIR_TPL, meta)         # 3. 目录
        move(f, target_dir / new_name)                 # 4. 归档
        index_to_es(new_name, meta)                    # 5. 索引
        notify_owner(meta.owner, path=...)             # 6. 通知
```

---

## 五、容错与运维

### 三层重试策略
| 层级 | 触发时机 | 策略 | 示例 |
| --- | --- | --- | --- |
| L1 步内重试 | 单个 API 429/5xx/超时 | 指数退避 3 次（30s/90s/270s） | 有赞接口限流 |
| L2 步间降级 | 某步骤连续失败 | 兜底数据源 / 缓存 / 昨日快照 | CRM 挂了 → 用昨日私域快照 + 告警 |
| L3 工作流回滚 | 关键步骤最终失败 | 停止后续步骤 + 简版结果 + P1 告警 | 日报无法生成 → "请人工登录查看" |

### 告警分级
- **P0 · 15 min 响应**：凭证泄露/数据被删/连续 3 次工作流全线失败 → 电话+短信+企微@全员运维 → 30min 未响应升级 CTO
- **P1 · 1 小时响应**：单次工作流失败/SLA 超时/依赖 API 5 次失败 → 企微机器人+邮件+值班群
- **P2 · 当天处理**：单步重试成功/轻微异常 → 企微机器人静默

### 日志与可观测
- runtime_log：JSON · ClickHouse · 180 天 `[wf_id, run_id, step_id, ts, level, latency_ms, tokens_used]`
- audit_log：WORM 对象存储 · 3 年 · HMAC-SHA256 签名（凭证读取/配置变更/数据源写入）
- observability：Grafana + OpenTelemetry → `wf_success_rate`, `p95_latency`, `cost_per_run`

---

## 六、ROI 与效益（与素材数字对齐）

### 工时节省测算（12 人 · 年化）
| 工作流 | 单人日耗时 | 覆盖人数 | 年工作日 | 年节省工时 |
| --- | --- | --- | --- | --- |
| 日报 | 75 min | 12 | 240 | 3,600 h |
| 工单周报 | 90 min/周 | 3（客服组长） | 50 | 225 h |
| 素材归档 | 40 min | 5（内容组） | 240 | 800 h |
| **合计** | — | — | — | **≈ 4,625 h/年 · 约 2.4 FTE** |

### 财务测算
- 年节省工时 4,625 h ÷ 全年工时 1,920 h = 约 **2.4 个 FTE**
- 按人均成本 25 万/年 → 年释放人力价值 ≈ **60 万**
- WorkBuddy 平台 + 配置实施成本 ≈ **15 万**
- 投资回收期 ≈ **3 个月** · 三年净收益 ≈ **165 万**

### 质量效益（自动化前 → 后）
| 指标 | 自动化前 | 自动化后 | 改善 |
| --- | --- | --- | --- |
| 日报口径错误 | 3-5 次/月 | 0-1 次/月 | ↓ 80% |
| 数据延误 | 12% 天数 | <1% 天数 | ↓ 92% |
| 素材找不回率 | 15% | <2% | ↓ 87% |
| 工单长尾覆盖 | 60% | 100% | ↑ 40pp |

---

## 七、10 个可复用工作流模板
1. 竞品朋友圈内容监控日报（每日 9:00）
2. KOL 合作 ROI 自动核算（合作结束 T+3）
3. 直播间数据复盘报告（直播结束 30min）
4. 会员生日/纪念日营销触达（每日 0:00 扫描）
5. 售后差评预警与 SOP 派单（差评实时）
6. 私域社群健康度周报（每周一）
7. 内容合规扫描（发布前）
8. 员工客户流失预警（离职当天）
9. 招投标客户资质核查（收到需求单）
10. 月度数据大盘一键生成（每月 1 号）
