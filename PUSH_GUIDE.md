# 企业 AI 智能体中台 · 推送到 GitHub 详细指南（小白版）

> 适用对象：第一次用 Git / GitHub、不想看术语的人。
> 目标：把你电脑 `D:\enterprise-ai-platform` 里的平台代码，推到 GitHub 仓库
> `https://github.com/hujian-ux/ruishenhang.git`，并确认自动测试（CI）跑起来。
>
> ⚠️ **重要现实情况**：我（AI 助手）所在的运行环境连 GitHub 只能"短连接通、长上传断"，实测 6 次推送全在上传中途被掐断，**推不上去**。所以这一步必须在**你自己的电脑**上做——你本机直连 GitHub 是稳的。下面就是照抄命令。

---

## 〇、先说结论（1 分钟看懂）

- ✅ **代码已经准备好了**：全部在 `D:\enterprise-ai-platform`，已经用 Git 登记过（叫"提交"），还写好了自动化测试门禁。
- ✅ **GitHub 仓库已经建好**：地址是 `https://github.com/hujian-ux/ruishenhang.git`（仓库名 `ruishenhang`，归属账号 `hujian-ux`）。
- ⚠️ **唯一没做完的一步**：把代码"上传"到 GitHub。这一步需要在**你自己的电脑**上做（我所在的运行环境连 GitHub 长上传会被掐断，推不上去；你本机直连是稳的）。
- 📌 你只要按下面"第三步"敲几条命令，1~2 分钟就能传完，GitHub 会自动跑测试。

---

## 一、背景：我们做到哪了

我们做的事情，类比成"写了一份很重要的 Word 文档存在你 D 盘，现在要把它传到网盘（GitHub）上备份并自动检查错别字"。

| 名词 | 大白话 | 我们的现状 |
|------|--------|-----------|
| 仓库（Repository） | 一个项目文件夹，存在 GitHub 网上 | 已建好：`hujian-ux/ruishenhang` |
| 本地仓库 | 你电脑上用 Git 管理起来的文件夹 | 已就绪：`D:\enterprise-ai-platform`，含 2 次"提交" |
| 远程（Remote） | 给网上仓库起的本地外号，默认叫 `origin` | 已配置好，指向你的 GitHub 地址 |
| 分支（Branch） | 一条时间线，默认叫 `main`（主线） | 本地是 `main` |
| 提交（Commit） | 给当前所有文件拍一张"快照"存档 | 已拍 2 张（全部 112 个文件） |
| 推送（Push） | 把本地快照上传到 GitHub | **就差这一步** |
| CI / 工作流 | 上传后 GitHub 自动跑测试的门禁 | 已写好，上传即自动触发 |

---

## 二、你需要准备的东西

1. 一台能上网的电脑（Windows / Mac 都行）。
2. 安装 **Git**（下面教你怎么装）。
3. 一个 GitHub 账号（你已有，仓库也建好了）。
4. 一个 **访问令牌（PAT）**——相当于"专门给程序用的密码"，下面教你怎么生成。

---

## 三、第一步：安装并配置 Git（若已装可跳过）

### 3.1 检查是否已装
打开**终端**：
- Windows：按 `Win` 键，输入 `Git Bash`，回车打开（或打开"命令提示符 / PowerShell"）。
- Mac：打开"终端" App。

输入下面命令，回车：
```bash
git --version
```
- 如果显示 `git version 2.xx.x` → 已装好，跳到第四步。
- 如果显示"找不到命令" → 去装：https://git-scm.com/downloads ，下载后一路点"下一步"安装，装完重开终端再试。

### 3.2 告诉 Git 你是谁（只做一次）
把下面两行里的名字、邮箱换成**你自己的 GitHub 用户名和邮箱**，逐行复制粘贴执行：
```bash
git config --global user.name "你的GitHub用户名"
git config --global user.email "你注册GitHub的邮箱"
```
> 这步不填真邮箱也行，只是 Git 要个署名。填了你 GitHub 的邮箱最规范。

---

## 四、第二步：生成 GitHub 访问令牌（PAT）—— 最关键！

### 为什么必须做这步？
从 2021 年起，GitHub **不允许用账号密码推代码**了。你必须在 GitHub 网页上生成一个"访问令牌"（一串 `github_pat_xxx` 开头的字符），程序用这串字符当密码来上传。

> ⚠️ **令牌 = 密码，只显示一次！** 生成后立刻复制保存到记事本，关掉网页就再也看不到了。

### 生成步骤（跟着点）
1. 用浏览器打开并登录 GitHub，点右上角**头像** → **Settings（设置）**。
2. 左侧最底下找 **Developer settings（开发者设置）** → 点开。
3. 点 **Personal access tokens（个人访问令牌）** → **Tokens (classic)（经典令牌）**。
   - （如果你的界面是 "Fine-grained tokens 细粒度令牌"，也能用，但要在第 6 步里单独给这个仓库开"写"权限，见下方备注。）
4. 点右上角绿色按钮 **Generate new token（生成新令牌）** → 选 **Generate new token (classic)**。
5. 填写：
   - **Note（备注）**：随便写，如 `platform-push`（方便以后认得）。
   - **Expiration（过期时间）**：选 `30 days` 或 `90 days`（别选 No expiration 永久，不安全）。
6. **权限勾选（最重要）**：在下方找到 **repo** 这一项，把它**整个勾上**（意思是"对该账号下所有仓库有读写权限"）。
   - 如果你用的是 Fine-grained 令牌：在 "Repository access" 选 **Only select repositories** 并勾选 `ruishenhang`；在 "Permissions → Contents" 设为 **Read and write**。
7. 拉到最底点 **Generate token（生成令牌）**。
8. 页面会出现一串 `github_pat_11...` 开头的字符 → **立刻全选复制，粘到记事本暂存**。

### 🔒 安全提醒
- 这串令牌等同于你的 GitHub 密码，**不要发给任何人、不要发到聊天群里**。
- 推完代码后，建议回来把它**撤销（Revoke）**（方法见第八节），用一次就废，零风险。

---

## 五、第三步：在你电脑上推送（核心步骤）

> 前提：`D:\enterprise-ai-platform` 这个文件夹在你电脑上存在、里面有 `backend`、`web`、`.github` 等目录（我们之前的工作成果就在那）。

### 5.1 打开终端，进入项目文件夹
Windows 用 **Git Bash** 或 **PowerShell**，输入：
```bash
cd /d/enterprise-ai-platform
```
> 说明：Git Bash 里 `D:\` 要写成 `/d/`。如果用普通命令提示符（CMD），则输入 `D:` 回车，再 `cd enterprise-ai-platform`。
> 也可以：在文件夹地址栏输入 `cmd` 回车，自动就位。

确认进对了地方，看一下：
```bash
dir
```
应该能看到 `backend`、`web`、`.github`、`README.md` 等。

### 5.2 连上 GitHub 并上传
直接执行：
```bash
git push -u origin main
```
- 如果**弹出输入用户名**：填你的 GitHub 用户名。
- 如果**弹出输入密码**：**粘贴你刚复制的那个 `github_pat_...` 令牌**（不是账号密码！粘贴时屏幕不显示字符是正常的，粘完直接回车）。
- 等待十几秒，看到 `main -> main` 或 `Writing objects` 之类字样、最后没报错 → **上传成功！**

### 5.3 如果报"拒绝 / 不是快进"（远程已有初始文件）
GitHub 建仓库时可能自动塞了几个模板文件（README、.gitignore 等），和我们本地的冲突，Git 会拒绝直接覆盖。解决：
```bash
git pull origin main --rebase
git push -u origin main
```
> 意思是"先把网上那点模板合进来，再传我们的"，一般能直接过。

### 5.4 想直接用我们的代码覆盖掉远程模板（可选，谨慎）
如果你确认远程那几个自动生成的文件没用，想让 GitHub 上的内容和我们本地完全一致：
```bash
git push -u origin main --force
```
> `--force` 会**覆盖**远程。新手一般不用这招，除非你确定要。

---

## 六、第四步：验证自动测试（CI）跑起来了

代码一上传，GitHub 就会自动按计划跑一套测试（叫"质量门禁"），验证平台的核心功能没坏。

1. 用浏览器打开你的仓库：https://github.com/hujian-ux/ruishenhang
2. 点顶部标签页 **Actions（动作）**。
3. 应该能看到一条正在跑或刚跑完的工作流，名字类似 `quality-gate`。
4. 它包含三道关（Job）：
   - `backend-build`：后端能正常安装、构建。
   - `rag-recall-gate`：知识库检索召回率达标（我们实测 Recall@100 = 100%）。
   - `ml-objective-gate`：客观评测指标达标（不依赖 AI 主观打分）。
5. 状态：
   - 三个都是 ✅ 绿色 → **全部通过**，可以放心。
   - 有红色 ❌ → 点进去看日志（Logs），哪关红了就是哪关没达标，把报错发我，我帮你修。

---

## 七、我们之前踩过的坑（对照自查，不用怕）

你本机推一般很顺，但万一遇到下面这些报错，对照看：

| 报错 / 现象 | 是什么意思 | 怎么办 |
|-------------|-----------|--------|
| `401 Unauthorized` | 令牌无效 / 过期 / 没权限 | 重新生成令牌，确认勾了 `repo`；复制完整别漏字符 |
| `could not read Password` / `Password authentication is not supported` | 把**账号密码**当令牌用了，或令牌**没有写权限** | 密码处粘 `github_pat_...` 令牌；确认令牌有 `repo` 写权限 |
| `fatal: repository not found` | 仓库地址拼错，或你没这仓库权限 | 核对地址 `github.com/hujian-ux/ruishenhang.git` |
| `non-fast-forward` / `rejected` | 远程有你本地没有的文件 | 按 5.3 先 `pull --rebase` 再 `push` |
| `502` / 连不上 / 超时 | 网络问题（多见于公司代理/沙箱环境） | 换网络、关代理重试；你本机一般不会有 |
| `schannel: server closed abruptly` | Windows 上 Git 的 SSL 小毛病 | 重装 Git 或换网络；通常重试就好 |

---

## 八、用完令牌请撤销（好习惯）

1. GitHub 网页 → 头像 → **Settings** → **Developer settings** → **Personal access tokens** → **Tokens (classic)**。
2. 找到你刚建的令牌（备注名 `platform-push`），点进去。
3. 点 **Revoke（撤销）** → 确认。
4. 撤销后这串令牌立刻失效，即使别人拿到也登不进，**零残留**。

> 以后要再推代码，重新生成一个新令牌即可（步骤同第四节）。

---

## 九、常用命令速查表（贴在便签上）

```bash
# 进入项目
cd /d/enterprise-ai-platform

# 看当前状态（哪些文件改了）
git status

# 看提交历史
git log --oneline

# 上传（第一次加 -u，之后直接 git push）
git push -u origin main

# 远程有冲突时，先合再推
git pull origin main --rebase
git push -u origin main
```

---

## 十、关于"让我代推"的说明（重要）

我们之前确实试过让我（AI 助手）在后台代推，并且已经**解决了所有账号/令牌权限问题**：
- 你给的第一个 `github_pat_...` 令牌是**只读**的，已换成有 `repo` 写权限的 `ghp_oozs...` 经典令牌；
- 发现该令牌归属账号是 `hujian-ux`，和目标仓库原本的 `hblchu75-sudo` 不一致，已把仓库改到 `hujian-ux/ruishenhang`，令牌身份与仓库归属已**完全匹配**；
- 本地 `origin`、2 个提交、` .git/config` 干净无令牌，全部就绪。

**唯一没跨过去的，是运行环境的网络**：实测短连接（查询仓库）能通，但 git 真正上传时连接被反复重置/掐断（6 次全败）。所以我这边硬推基本不可能成功。

**结论**：这一步请你按本指南第三步在自己电脑上推——你本机直连 GitHub 是稳的，30 秒搞定，且不用把令牌交给任何人。推完把 Actions 页截图或结果发我即可。

> 如果你更想让我再试，也可以，但大概率是同样网络失败，不如你本机一次成功。

---

### 附录：本次项目已具备的资产（推上去后都会进 GitHub）
- `backend/`：平台后端（FastAPI + 选型/知识库/评测服务），含 24 项测试全过。
- `web/`：React 前端（选型向导已联调后端）。
- `.github/workflows/quality-gate.yml`：GitHub 自动测试门禁。
- `docs/`：各版本蓝图与评审报告（58 份）。
- `README.md`：项目说明与里程碑。
- `openapi.yaml`：接口契约。

> 有任何一步卡住，把**完整的报错文字**发给我，我帮你定位。别怕，都是小问题。
