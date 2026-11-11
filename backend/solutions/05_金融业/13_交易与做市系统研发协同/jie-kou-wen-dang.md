# 交易与做市系统接口文档（FIN-13 · 某投资银行内部系统）

> 本文档为某投资银行交易与做市平台的内部接口定义。当前版本中，标记「待补全」的接口段落为本轮研发协同的重点：实现能力时必须同步补全接口说明，改完代码顺带把文档补上。接口多、会话状态多，任何改动先对齐本文档与传值方式。

## 一、接口总览

| 接口编号 | 接口名称 | 功能说明 | 输入参数 | 返回值 | 会话参数要求 | 文档状态 |
| --- | --- | --- | --- | --- | --- | --- |
| API-01 | queryMarketTradableVolume | 取指定市场债券的实时可成交量 | bondCode, marketId, depthLevel | tradableVolume, depthLevels | marketSession 必填 | 待补全 |
| API-02 | submitTradeOrder | 提交交易指令 | bondCode, side, price, volume, account | orderId, submitStatus | sessionToken 必填 | 完整 |
| API-03 | cancelTradeOrder | 撤销未成交交易指令 | orderId | cancelStatus | sessionToken 必填 | 完整 |
| API-04 | queryBondPosition | 查询债券持仓明细 | bondCode, account | positionList, positionCount | marketSession 可选 | 完整 |
| API-05 | calcBondPnL | 计算单券债券损益 | bondCode, clearSeq | pnlDetail | 无会话参数 | 完整 |
| API-06 | aggregateBondPnl | 按清盘顺序对债券损益实体排序汇总 | clearSeq, groupBy | pnlSummary | 无会话参数 | 待补全 |
| API-07 | generateConfirmation | 按模板生成成交确认书 | tradeId, templateId | confirmationUrl | sessionToken 必填 | 完整 |
| API-08 | updateCacheValue | 更新缓存服务传值（减少中间类型转换） | cacheKey, valuePayload | updateStatus | sessionProfile 必填 | 待补全 |
| API-09 | batchImportCases | 用例批量导入（含会话参数） | caseList | importResult | sessionProfile 必填 | 待补全 |

## 二、接口明细

### API-01 queryMarketTradableVolume · 取市场可成交量

- 功能：取指定市场债券的实时可成交量与市场深度，供做市报价参考。
- 输入：`bondCode`（债券代码）、`marketId`（市场标识）、`depthLevel`（深度档位，默认 10）。
- 返回：`tradableVolume`（可成交量）、`depthLevels`（逐档深度）。
- 会话参数：`marketSession`（市场会话）必填；缺失时返回「会话未建立」错误，不允许跨会话取量。
- 文档状态：**待补全** —— 原文档缺会话参数与错误码说明，本轮实现时同步补全。

### API-02 submitTradeOrder · 交易指令提交

- 功能：提交交易指令，返回指令编号与受理状态。
- 输入：`bondCode`、`side`（BUY/SELL）、`price`、`volume`、`account`。
- 返回：`orderId`、`submitStatus`（受理 / 校验失败 / 会话错误）。
- 会话参数：`sessionToken` 必填；过期返回「重新认证」错误。

### API-03 cancelTradeOrder · 交易指令撤销

- 功能：撤销未成交交易指令。
- 输入：`orderId`。
- 返回：`cancelStatus`（已撤销 / 未找到 / 会话错误）。
- 会话参数：`sessionToken` 必填。

### API-04 queryBondPosition · 持仓查询

- 功能：查询账户下债券持仓明细。
- 输入：`bondCode`（可省略表示全量）、`account`。
- 返回：`positionList`（持仓明细列表）、`positionCount`。
- 会话参数：`marketSession` 可选；不传时默认当前市场会话。

### API-05 calcBondPnL · 债券损益计算

- 功能：按清盘顺序计算单券损益明细。
- 输入：`bondCode`、`clearSeq`（清盘顺序号）。
- 返回：`pnlDetail`（逐笔损益明细）。
- 会话参数：无（损益计算不绑定会话）。

### API-06 aggregateBondPnl · 损益排序汇总

- 功能：按清盘顺序等指标对债券损益实体做排序与汇总。
- 输入：`clearSeq`、`groupBy`（分组维度，缺省按清盘顺序逐笔）。
- 返回：`pnlSummary`（排序后的汇总结果）。
- 会话参数：无。
- 文档状态：**待补全** —— 原文档缺分组维度说明，本轮实现时同步补全。

### API-07 generateConfirmation · 确认书生成

- 功能：按模板生成成交确认书并返回下载地址。
- 输入：`tradeId`、`templateId`。
- 返回：`confirmationUrl`。
- 会话参数：`sessionToken` 必填；缺失时返回「会话未建立」错误。

### API-08 updateCacheValue · 缓存传值更新

- 功能：更新缓存服务中的传值；重新设计后直接传结构化载荷，减少中间类型转换与冗余类型定义。
- 输入：`cacheKey`、`valuePayload`（结构化载荷）。
- 返回：`updateStatus`（写入成功 / 载荷超限拒绝 / 会话错误）。
- 会话参数：`sessionProfile` 必填；不同会话配置下缓存命名空间隔离。
- 文档状态：**待补全** —— 传值方式重设计后原说明已过期，本轮同步补全。

### API-09 batchImportCases · 用例批量导入

- 功能：批量导入交易链路测试用例，支持携带会话参数并输出会话配置内容。
- 输入：`caseList`（用例清单）。
- 返回：`importResult`（逐条导入结果 + 会话配置回显）。
- 会话参数：`sessionProfile` 必填；与会话配置不匹配时导入前校验失败。
- 文档状态：**待补全** —— 新增会话参数后原文档未覆盖，本轮同步补全。

## 三、文档同步约定

- 「待补全」接口在实现合入时，必须同步更新本文档对应段落（参数、返回值、会话参数要求、错误码）。
- 用例生成以本文档为唯一事实源；接口改动后用例随参数变化批量再生成。
