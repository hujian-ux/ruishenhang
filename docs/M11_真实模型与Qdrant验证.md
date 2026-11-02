# M11 真实模型与 Qdrant 真服务验证

> 补上 M9/M10 明确留白的两项：**Qdrant 远程 http 模式**（当时本机 Docker daemon 没起，只跑过 local 文件模式）
> 与**真实 bge-m3**（当时无模型缓存，所有性能数字都基于确定性伪向量）。
>
> 结论先放这里：**这两项一验，M9/M10 的性能数字全部要重新读**——它们都不含 embedding。
> 同时挖出 3 个只在真实环境才暴露的 bug，其中一个会让 Recall@100 直接跌破门禁。

---

## 一、一句话结论

| 问题 | 结论 |
|---|---|
| Qdrant http 能用吗 | **能用**，功能与内存后端逐条一致；代价是检索慢 15 倍（44.7ms vs 2.9ms） |
| 真实 bge-m3 效果如何 | **召回明显更好**：Recall@5 0.9206 → **0.9802** |
| 代价是什么 | **检索从 0.2ms 变成 244ms**（每次都要现场编码 query）；靠缓存可压到 10.5ms |
| 之前 M9/M10 的延迟数字 | **全部不含 embedding**，真实部署要按本节新增的「查询编码」成本重算 |
| 顺带修掉的 bug | **3 个**，其中 1 个会让召回跌破 0.95 门禁、1 个会静默丢 97.6% 的向量 |

---

## 二、Qdrant 真服务（不依赖 Docker）

本机只有 Docker CLI、没有 Desktop，`npipe` 连不上。Qdrant 官方提供 **Windows 原生二进制**，
不需要 Docker 就能起真服务——这条路值得记下来，比装 Docker Desktop 轻得多。

```bash
# GitHub 直连 release 下载会 SSL 失败（exit 35），用镜像一次成功
mkdir -p /d/tools/qdrant && cd /d/tools/qdrant
curl -sL -o qdrant.zip "https://ghfast.top/https://github.com/qdrant/qdrant/releases/download/v1.20.0/qdrant-x86_64-pc-windows-msvc.zip"
unzip -o -q qdrant.zip
./qdrant.exe            # HTTP 6333 / gRPC 6334，zip 里只有二进制，配置用内置默认
```

验证结果（Qdrant 1.20.0）：

| 项 | 结果 |
|---|---|
| 服务 | `{"title":"qdrant - vector search engine","version":"1.20.0"}` |
| upsert / search / delete / count | 全通；自索引位次 1、余弦 0.9999998 |
| 220 篇 → 2686 块导入 | **4.1s**，`points=2686`、`errors=0` |
| 检索 | p50 **44.72ms** / p95 66.46ms |
| 召回（demo 语料 60 块） | 与 memory **逐位一致**：recall@5 0.9206 / @10 0.9881 / @100 1.0 |

**取舍**：内存后端 220 篇导入 0.4s、检索 p50 2.85ms；Qdrant http 是 4.1s / 44.72ms。
多花的钱是每次检索一次 HTTP 往返。小于万篇仍建议 `memory`；要横向扩容或多实例共享时再切 `qdrant`。

---

## 三、真实 bge-m3

模型 `BAAI/bge-m3`（1024 维，与 `EMBEDDING_DIM` 一致），从 `hf-mirror.com` 下载（直连 HuggingFace 超时）：

```bash
HF_ENDPOINT=https://hf-mirror.com HF_HUB_ENABLE_HF_TRANSFER=1 \
python -c "from huggingface_hub import snapshot_download; snapshot_download('BAAI/bge-m3', local_dir='D:/models/bge-m3')"
```

启动方式（三个开关缺一不可，否则内核静默退回伪向量，量出来的数字毫无意义）：

```bash
USE_LOCAL_EMBEDDING=1 EMBEDDING_MODEL=D:/models/bge-m3 \
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_HOME=D:/models/.hfhome \
EMBED_CACHE=1 VECTOR_BACKEND=memory \
./.venv/Scripts/python.exe scripts/bench_embed.py --docs 220
```

### 3.1 召回：真实向量确实更强

同一份语料（demo 18 篇 / 60 块）、同一份 golden（252 条），只换向量来源：

| 指标 | 伪向量（M9/M10） | 真实 bge-m3 | 变化 |
|---|---|---|---|
| Recall@5 | 0.9206 | **0.9802** | +5.96 pt |
| Recall@10 | 0.9881 | **0.9960** | +0.79 pt |
| Recall@100 | 1.0000 | 1.0000 | — |
| 同族文本余弦均值 | — | 0.675 | 真实语义可分 |

伪向量（按字节哈希散列）在 Recall@100 上早就满分，所以历次压测没暴露差距；
**短列表（@5）才是真实语义的用武之地**，这正是产品里用户实际看的 top-5。

### 3.2 成本：编码是新的主成本项

| 环节 | 伪向量 | 真实 bge-m3 | 倍数 |
|---|---|---|---|
| 单条编码 | ~0.05ms | **1108ms**（逐条）/ **633ms**（batch=16） | 万倍级 |
| 220 篇全量导入（2686 块） | 0.4s | **303.6s**（0.7 篇/秒） | 760x |
| 检索（query 需现场编码） | 0.2ms | **p50 244ms** / p95 338ms | 千倍级 |
| 检索（query 命中缓存） | 0.2ms | **p50 10.5ms** | 50x |

三点必须讲清楚：

1. **M9/M10 报的 3.35ms / 19ms 都是「不含 embedding」的纯索引延迟**。真实部署下，
   用户感知的延迟 = query 编码 + 索引检索，前者是后者的一到两个数量级。
2. 本机 CPU 编码很慢（**633-1108ms/条**，20 核但实际吞吐有限），这是环境限制不是模型上限。
   上 GPU 或 ONNX 量化后应能压到几十毫秒，但那属于部署调优，本轮没做。
3. **缓存是唯一能在纯 CPU 上把延迟拉回可用区间的办法**：热门问题重复问从 244ms 降到 10.5ms。

### 3.3 缓存收益：67 倍

| 场景 | 耗时 | 吞吐 | 命中率 |
|---|---|---|---|
| 冷启动全量导入（缓存空） | **303.6s** | 0.7 篇/秒 | — |
| 缓存命中后重导 | **4.5s** | **49.2 篇/秒** | 100% |
| 重启载回重编码 2686 块 | **0.18s** | — | 100% |

结论：**embedding 缓存不是"锦上添花"，在真实模型下它是能不能用的分界线**。
重启一次 300 秒 vs 4.5 秒，差的是试点客户等不等得起。

---

## 四、这一轮挖出来的 3 个 bug

### bug 1：`available()` 与 `_backend` 自相矛盾 → 召回跌破门禁

**现象**：切到 `VECTOR_BACKEND=qdrant` 跑测试，**Recall@100 = 0.948 < 0.95**，门禁红；而两个后端单独对比时召回又完全一致（都是 1.0）。稳定复现、两端各自看都"正常"，最难查的那种。

**根因**：`_ensure_collection()` 失败时把 `_backend = "memory"`（表示降级），但**没有清 `_client`**；而 `available()` 只判 `_client is not None`。于是出现：声称已降级到内存，实际检索还在走 Qdrant——而 Qdrant 里只有一部分点（失败那批没写进去），其余块在向量路彻底消失。

**修复**：`available()` 改为 `_client is not None and _backend != "memory"`；`stats()` 同步。降级要降得干净，否则"部分可用"比"完全不可用"更难排查。

**为什么之前没暴露**：`QDRANT_TIMEOUT_S` 默认 **1.0s**，首次建集合 + 批量写入在冷启动时超过 1 秒就触发降级。`local` 文件模式不走网络，从未超时。已改为 **5.0s**。

### bug 2：批量 upsert 超时 → 静默丢掉 97.6% 的向量

**现象**：220 篇导入后 `points=64`（应为 2686），但导入"成功"返回、`errors` 只记 1 次。

**根因**：2686 个点一次性 `upsert` 撞 5s 超时，异常被"降级不阻塞业务"的 `except` 吞掉，只留下失败前写进去的那部分。

**修复**：按 256 一批切分写入；失败原因写进 `_stats["last_error"]`，在 `/kb/index` 上可见。
降级可以不阻塞业务，但**失败原因必须可见**——否则"导入成功但搜不到"会被当成检索 bug 查一周。

### bug 3：缓存命中率统计口径错 → 28.89% 假象

**现象**：重启载回重编码 2686 块只花 **0.20s**，但 `/kb/index` 显示命中率 **28.89%**——两者自相矛盾（真编 1910 块要 200 秒）。

**根因**：`get_many` 用 dict 收集命中，**重复文本的 key 会塌成一条**，而 `hits += len(out)` 用的是去重后的 key 数，`misses` 却用文本条数。模板化语料里 2686 块只有 776 个不同文本 → 命中率被算成 776/2686 = 28.89%。实际 100%。

**修复**：按「文本条数」统计（`for k in keys: if k in out: hits += 1`）。这个数字直接显示在试点看板上，会把运维引向错误判断（"缓存没生效，是不是配置错了"）。

---

## 五、两个真实生效的优化

### 5.1 批量编码（`_embed_batch`）

原 `_embed_many` 是 `[_embed_one(t) for t in miss_texts]` —— **逐条**编码。伪向量一条几十微秒看不出来，真模型下把 CPU 向量化单元晾在那儿。

| 方式 | 耗时 | 单条 |
|---|---|---|
| 逐条 | 35.47s / 32 条 | 1108 ms |
| batch=8 | 43.01s / 64 条 | 672 ms |
| batch=16 | 42.12s / 64 条 | **633 ms** |
| batch=32 | 43.60s / 64 条 | 681 ms |
| batch=64 | 40.48s / 64 条 | 633 ms |

**1.75x**（不是理想的 3-4x，因为这台机器算力本身就受限，批处理拿不到更多并行度）。默认 `EMBED_BATCH_SIZE=16`。

### 5.2 query 也走 embedding 缓存

`retrieve` 里原来是 `qvec = embed(query)` —— 直接算，**绕过缓存**。于是每一次检索都要现场编码 query，热门问题问一百遍编一百遍。

改为 `qvec = _embed_many([query])[0]` 之后：

| | 延迟 |
|---|---|
| 同一 query 首次 | 244 ms（必须编码） |
| 同一 query 再次 | **0.4–0.5 ms**（demo 语料）/ **10.5 ms**（220 篇语料） |

**300 倍**。这是纯 CPU 部署下让检索回到可用的关键一步。

---

## 六、新增与调整的配置

| 开关 | 默认 | 说明 |
|---|---|---|
| `USE_LOCAL_EMBEDDING` | `0` | `1` 才启用真实模型；保持 `0` 走确定性伪向量（测试/离线用） |
| `EMBEDDING_MODEL` | `bge-m3` | 可填本地绝对路径（如 `D:/models/bge-m3`），配合 `HF_HUB_OFFLINE=1` 完全离线 |
| `EMBED_BATCH_SIZE` | `16` | 真实模型编码批大小；CPU 上 16 最稳 |
| `QDRANT_TIMEOUT_S` | **`5.0`**（原 1.0） | 1 秒会让「首次建集合」直接降级 |
| `QDRANT_ASYNC_UPSERT` | `0` | `1` 才异步写；**默认同步**，避免"写完立刻检索"看不到数据 |
| `VECTOR_BACKEND` | `memory` | `qdrant` 需配合真服务；`auto` 不可达时静默降级 |

---

## 七、复现命令

```bash
cd D:/enterprise-ai-platform/backend

# Qdrant 真服务（Windows 二进制，不用 Docker）
cd /d/tools/qdrant && ./qdrant.exe        # 另开一个窗口

# Qdrant http 后端：功能 + 性能
VECTOR_BACKEND=qdrant QDRANT_MODE=http PG_PORT=1 SEED_DEMO_CORPUS=0 EMBED_CACHE=0 \
  ./.venv/Scripts/python.exe -m pytest -q

# 真实 bge-m3 压测（冷启动 → 缓存 → 检索 → 召回）
USE_LOCAL_EMBEDDING=1 EMBEDDING_MODEL=D:/models/bge-m3 \
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_HOME=D:/models/.hfhome \
EMBED_CACHE=1 PG_PORT=1 SEED_DEMO_CORPUS=0 \
  ./.venv/Scripts/python.exe scripts/bench_embed.py --docs 220

# 伪向量全量回归（79 项，CI 同款）
./.venv/Scripts/python.exe -m pytest -q
```

---

## 八、如实说明：没做的、没验的

| 项 | 状态 |
|---|---|
| Qdrant 集群 / 多副本 / 快照恢复 | **未做**。只验了单机单集合 |
| GPU 编码、ONNX 量化、bge-m3 稀疏/多向量输出 | **未做**。本机纯 CPU，633ms/条是环境上限 |
| 220 篇以上真实模型的规模验证 | **未做**。冷启动 300s/220 篇，上千篇需上 GPU 或先做离线预计算 |
| 外部 golden（`pilot_corpus/golden_v2.jsonl`）直接算召回 | **未做**。它是「标题反查」格式，与 `compute_recall_at_k` 的 `viewer + expected_doc_id` 口径不同，脚本选择**跳过而不是硬算**——错口径的召回比没有召回更误导 |
| 本机 C 盘满载导致的两次中断 | 已记录。编码/写盘类操作对磁盘可用空间敏感，C 盘 0MB 时 Python 会 ENOSPC 失败，测试会表现为随机报错 |

---

## 九、对部署的建议

1. **试点（< 1000 篇）**：`VECTOR_BACKEND=memory` + 真实 bge-m3 + `EMBED_CACHE=1`。
   一次性把语料编码入库（220 篇约 5 分钟），之后重启 4.5 秒。
2. **检索体验**：query 编码是唯一慢点，**务必让缓存生效**（默认已开）。
   如果用户 query 高度分散（缓存命中率低），就该上 GPU 或换小模型编码 query。
3. **超过千篇或要多实例**：切 `VECTOR_BACKEND=qdrant` + 独立 Qdrant 服务，
   接受每次检索多 40ms 的网络往返，换来内存解耦与横向扩容。
4. **别再只看 `last_query_ms`**：真实模型下它只反映索引部分。
   完整链路延迟 = query 编码（首次 244ms / 缓存后 <1ms）+ 索引检索。
