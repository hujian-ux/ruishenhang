# 本地文件网关（Local File Gateway）

把本地目录里的文件**自动同步**进企业 AI 中台：监听一个或多个文件夹，发现新增/变更就
通过平台的 `POST /agent/files` 上传（带解析与去重）。适合「本地知识库/方案库 → 中台」
的轻量同步场景，免去人工逐个上传。

## 特性

- **去重防重传**：按内容 sha256 + 大小 + mtime 判定是否真的变化，状态文件记录已传文件，重启不重复传。
- **include / exclude**：glob 模式过滤（exclude 优先于 include）。
- **轮询即可**：纯轮询实现，**不依赖 watchdog**；想用文件系统事件可在上层自行包装。
- **两种运行模式**：常驻轮询（`run_forever`）或单次扫描（`--once`，适合 cron / 任务计划）。
- **可注入客户端**：平台客户端可替换，便于离线单测（不触发任何网络）。

## 安装

```bash
cd local-gateway
pip install -r requirements.txt
```

## 配置

复制 `config.example.yaml` 为 `config.yaml`，按需修改：

| 字段 | 说明 |
|------|------|
| `backend_url` | 中台后端地址（生产填 `https://你的域名`，不要带 `/api/v1`） |
| `token` | 服务令牌；留空则启动时用 `dev_admin` 登录（仅演示/内网） |
| `tenant` | 多租户部署时指定租户 ID |
| `watch_dirs` | 监听的本地目录（可多个） |
| `include` / `exclude` | glob 过滤，exclude 优先 |
| `endpoint` | 上传接收端点，默认 `/agent/files` |
| `poll_interval` | 轮询间隔（秒） |
| `state_file` | 去重状态文件路径 |

> 令牌也可通过环境变量 `GW_TOKEN` 传入，优先级高于配置文件；生产环境**建议直配 token 而非 dev_admin**。

## 运行

```bash
# 常驻（每 30s 轮询一次）
python gateway.py --config config.yaml

# 单次扫描（适合放进 cron / Windows 任务计划，每分钟一次）
python gateway.py --config config.yaml --once
```

## 与中台的对接点

- 上传端点：`POST {backend_url}/agent/files`（multipart 字段 `file`），返回 `data.file_id`。
- 鉴权：按平台约定发**裸 token**（不带 `Bearer `），由 `platform_client.PlatformClient` 处理。
- 多租户：上传归属调用者所在租户（通过 token 解析），跨租户隔离由中台保证。

## 说明 / 边界

- 本网关是**同步客户端**，不是中台内置组件；独立进程运行。
- 真机联调需在能访问中台后端的机器上运行（桌面/服务器均可）。
- 不做双向同步、不做冲突合并——只负责「本地 → 中台」单向投递。
- 测试见 `tests/test_gateway.py`（注入假客户端，零网络）。
