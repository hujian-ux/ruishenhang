# 主提示词 · 内部效率自动化工作流（r4-2）

> 学员在 WorkBuddy 中实际发送的主提示词。把待自动化操作与数据源替换到对应位置即可。

---

## 提示词正文（复制即用）

```
你是工作流编排与运维专家。帮我把下面这条内部重复劳动，搭成一个可定时触发、凭证托管、带容错与告警的自动化工作流。

【待自动化操作】
运营部每天 8:30-10:00 手工做私域运营日报：登录企微→导会话数→登录有赞→导 GMV→登录 CRM→导私域转化→Excel VLOOKUP→截图→企微群发，耗时 75 min/人，每月漏发 3-5 次。

【可用数据源 / 连接器】
- 企微客服 API：wework_kf_api（GET /cgi-bin/kf/statistic）
- 有赞开放 API：youzan_open_api（order.list.get）
- 只读 MySQL：mysql_readonly（SELECT dws_private_conversion）

【调度与 SLA】
工作日 08:30 触发（Asia/Shanghai，jitter 0-60s），08:45 前必须推送完成。

【输出要求】
1. 输出一体化工作流 YAML：workflow_id / schedule(cron+jitter+timezone) / owner / sla / 7 个步骤（每步 connector + action + output 变量）/ credential_management（KMS、最小权限、双人审批、审计）/ 容错（L1-L3）/ 告警（P0-P2）。
2. 给出日报渲染后的示例输出（含核心指标、分区表现、AI 点评三句）。
3. 附"一句话搭建说明"：给运营/实施同学照做的最小步骤。

要求：凭证全部走 Secret Vault，工作流账号最小权限（仅 GET/SELECT），任何外部依赖失败都有降级路径。
```

---

## 使用说明
- 此提示词直接发送给已启用「内部效率自动化工作流搭建」Skill 的 WorkBuddy。
- 第 1 步产出 `自动化工作流配置.md` 中的"日报配置示例"；工单聚类、素材归档可同理替换【待自动化操作】与【数据源】复用同一模板。
- ROI 测算口径见交付物内"工时节省测算"表：12 人团队年省约 4,625 小时（≈2.4 FTE）。
