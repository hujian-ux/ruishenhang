# 企业 AI 智能体中台 · 推送操作详细指南（M11 补推版）

> 适用场景：本地已有提交 `8aaac42`，远程还没有，需要补推一次并确认 CI 通过。
> 更新日期：2026-11-03　｜　本文件替代早先的「首次推送小白版」。

---

## 〇、一句话结论

**只需要 3 条命令，1 分钟。** 凭据已经存在 Windows 凭据管理器里，不会再让你输密码。

```cmd
cd /d D:\enterprise-ai-platform
git push origin main
git status
```

---

## 一、现在的真实状态（我已逐项核对）

| 核对项 | 实测值 | 说明 |
|---|---|---|
| 本地仓库 | `D:\enterprise-ai-platform` | 唯一真源，工作区已迁到这里 |
| 远程仓库 | `https://github.com/hujian-ux/ruishenhang.git` | 公开仓库，别名 `origin` |
| 当前分支 | `main` | 已跟踪 `origin/main` |
| 领先远程 | **2 个提交** | `8aaac42`（M11）+ 本文档更新 |
| 工作区 | 干净，无未提交改动 | `git status` 无改动行即正常 |
| 本地提交总数 | 27 个 | M0 到 M11 全部在内 |
| 本机 Git | `D:\Program Files\Git`，2.55.0.windows.2 | **已在系统 PATH 里**，终端直接敲 `git` 可用 |
| 凭据助手 | Git Credential Manager（manager） | 上次推送已授权，凭据已保存 |
| CI 触发条件 | push 到 main 或任意 PR | 推上去约 80 秒跑完 |

> 注意：我这边执行环境连 GitHub 长连接会被掐断（试了 3 次：`Empty reply` / `schannel closed` / `CONNECT tunnel 502`），所以必须由你在本机终端执行。你的机器是直连，之前推成功过一次，没问题。

---

## 二、操作步骤（照抄即可）

### 第 1 步：打开一个终端

三种方式任选，**推荐方式 C**（最省事，目录自动对好）：

| 方式 | 操作 |
|---|---|
| A · Win+R | 按 `Win + R` → 输入 `cmd` → 回车 |
| B · 开始菜单 | 搜 `PowerShell` → 打开 |
| C · 资源管理器（推荐） | 打开 `D:\enterprise-ai-platform` 文件夹 → 点顶部地址栏 → 输入 `cmd` → 回车 |

> 方式 C 打开后已经在项目目录里，第 3 步可以跳过。

### 第 2 步：确认 git 能用

```cmd
git --version
```

**预期输出**：`git version 2.55.0.windows.2`

如果提示「'git' 不是内部或外部命令」，说明系统 PATH 没生效 —— 关掉终端重开一次即可；还不行就用完整路径：`"D:\Program Files\Git\cmd\git.exe"`。

### 第 3 步：进入仓库目录

cmd：

```cmd
cd /d D:\enterprise-ai-platform
```

PowerShell：

```powershell
cd D:\enterprise-ai-platform
```

确认位置对不对：

```cmd
git rev-parse --show-toplevel
```

**预期输出**：`D:/enterprise-ai-platform`

### 第 4 步：推送前三项核对（30 秒，别跳）

```cmd
git status
git log --oneline -3
git remote -v
```

**预期输出对照**：

```
On branch main
Your branch is ahead of 'origin/main' by 2 commits.      ← 关键：ahead 2
nothing to commit, working tree clean                    ← 关键：干净

<hash>  docs: 推送指南更新为 M11 补推版
8aaac42 feat(M11): 真实 bge-m3 接入 + Qdrant http 真服务验证...
08ed440 feat(M9): 规模化——向量矩阵化...

origin  https://github.com/hujian-ux/ruishenhang.git (fetch)
origin  https://github.com/hujian-ux/ruishenhang.git (push)
```

三项都对得上，说明该推的正好是这两个提交。若 `git status` 显示 `ahead of ... by 3 commits` 之类，别慌 —— 多推几个提交也是对的，都是本地已完成的工作。

### 第 5 步：执行推送

```cmd
git push origin main
```

**成功时的输出长这样**：

```
Enumerating objects: 47, done.
Counting objects: 100% (47/47), done.
Delta compression using up to 16 threads
Compressing objects: 100% (23/23), done.
Writing objects: 100% (26/26), 18.42 KiB | 2.30 MiB/s, done.
Total 26 (delta 18), reused 0 (delta 0), pack-reused 0
remote: Resolving deltas: 100% (18/18), completed with a specific timeout.
To https://github.com/hujian-ux/ruishenhang.git
   08ed440..<新哈希>  main -> main        ← 这一行是关键
```

**判断标准**：最后一行 `旧哈希..新哈希  main -> main`，右边应该是 `8aaac42` 或更新的哈希。

只要看到 `main -> main`，就成功了。

### 第 6 步：验证推送结果

```cmd
git status
```

**预期输出**：

```
On branch main
Your branch is up to date with 'origin/main'.      ← 变成了 up to date
nothing to commit, working tree clean
```

然后打开浏览器看两个页面：

1. **确认代码到了**：<https://github.com/hujian-ux/ruishenhang/commits/main>
   最上面一条应该是 `8aaac42 feat(M11): 真实 bge-m3 接入...`

2. **确认 CI 在跑**：<https://github.com/hujian-ux/ruishenhang/actions>
   最新一条 `quality-gate` 记录：
   - 黄圈转 → 正在跑（约 80 秒）
   - 绿勾 → **通过**，M11 才算真正验收闭环
   - 红叉 → 失败，把日志贴给我

---

## 三、可能遇到的 6 种情况（对症处理）

### 情况 1：弹出一个浏览器登录窗口

说明凭据失效了。直接用 `hujian-ux` 账号登录授权即可，勾选授权后终端会自动继续。

### 情况 2：`remote: Permission to hujian-ux/ruishenhang.git denied` 或 `403`

**当前登录的 GitHub 账号不对**。清理旧凭据再推：

```cmd
git credential-manager logout
git push origin main
```

或者手动删：控制面板 → 凭据管理器 → Windows 凭据 → 找到 `git:https://github.com` → 删除 → 重新 push。

### 情况 3：`! [rejected] main -> main (non-fast-forward)`

远程比你本地多了一些提交（可能是之前在网页上改过文件）。**先拉再推**：

```cmd
git pull --rebase origin main
git push origin main
```

`--rebase` 会把你的提交排到远程提交之后，不会产生多余的合并提交。

如果 rebase 过程中报冲突，执行 `git rebase --abort` 回到推之前的状态，然后叫我处理，别硬来。

### 情况 4：卡在 `Writing objects: 45%` 不动 / 超时断开

网络问题。先看有没有残留的代理配置（**这是之前实际踩过的坑**，会导致 `CONNECT tunnel 502`）：

```cmd
git config --global --get http.proxy
git config --global --get https.proxy
```

- 有输出：说明走了代理。确认代理软件（Clash / v2ray 等）在运行、端口对得上；或直接清掉：`git config --global --unset http.proxy` 与 `git config --global --unset https.proxy`
- 无输出：直接重跑一次 `git push origin main`，第二次通常会成功

### 情况 5：`fatal: not a git repository`

没进对目录。执行 `cd` 确认，或直接 `cd /d D:\enterprise-ai-platform`。

### 情况 6：`'git' 不是内部或外部命令，也不是可运行的程序`

终端启动时 PATH 还没刷新。关掉全部终端窗口重开。仍然不行就用绝对路径：

```cmd
"D:\Program Files\Git\cmd\git.exe" -C D:\enterprise-ai-platform push origin main
```

---

## 四、命令行实在不想用？两个图形方案

### 方案 A：GitHub Desktop（推荐给不想碰命令的人）

1. 下载安装 <https://desktop.github.com/>
2. 打开 → `File` → `Add local repository` → 选 `D:\enterprise-ai-platform`
3. 顶部会出现 `Push origin` 按钮（因为检测到有未推送提交）→ 点它
4. 点右上角 `Fetch origin` 确认变成 `Last fetched just now` 且无待推

### 方案 B：网页手动上传（不推荐）

GitHub 网页的 `Add file → Upload files` 上传的是**文件**不是**提交**，会把 M11 的提交信息与增量历史全部压平。除非命令行彻底走不通，别用这个。

---

## 五、推完之后请把这两样发我

1. **`git push` 的完整输出**（复制文本或截图都行）
2. **Actions 页面的结论**：绿勾 / 红叉

- 绿勾 → 我更新记忆里的「已推送」状态，M11 正式收尾
- 红叉 → 把失败 job 的日志贴给我。CI 里最可能踩的两个点：`python-multipart` 依赖（已修，见 `ed861cb`）和真实模型相关开关（CI 里应保持伪向量，不能依赖本机 bge-m3）

---

## 六、速查表（贴在便签上）

| 目的 | 命令 |
|---|---|
| 看有没有东西要推 | `git status` |
| 看具体哪几个提交 | `git log --oneline origin/main..HEAD` |
| 推 | `git push origin main` |
| 推后确认 | `git status`（应显示 up to date） |
| 远程多提交被拒 | `git pull --rebase origin main` 再 `git push origin main` |
| 清代理 | `git config --global --unset http.proxy` |
| 清 GitHub 凭据 | `git credential-manager logout` |
| 撤销上一次提交（**慎用**） | `git reset --soft HEAD~1` |

---

## 附录：本次待推提交包含什么

`8aaac42 feat(M11)` —— 79 项测试全过，包含：

- Qdrant Windows 原生二进制接入，`QDRANT_MODE=http` 全链路验证通过
- 真实 bge-m3（1024 维）接入，Recall@5 从 0.9206 提升到 **0.9802**
- 修掉 3 个只在真实环境暴露的 bug：
  1. 向量后端降级后仍走 qdrant → Recall@100 跌到 0.948 跌破门禁
  2. 2686 点一次性 upsert 超时被吞 → 静默丢 97.6% 向量
  3. 缓存命中率统计用去重 key 数当分子 → 显示 28.89%，实际 100%
- 新增 `backend/scripts/bench_embed.py` 压测脚本、`docs/M11_真实模型与Qdrant验证.md`
- openapi 保持 27 paths / 24 schemas
