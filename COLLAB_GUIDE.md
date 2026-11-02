# 团队协作指南：把仓库共享给同事（小白版）

> 仓库地址：`https://github.com/hujian-ux/ruishenhang`（当前为 **Public 公开**）
> 目标：同事能拿到代码、能协作开发，且 CI 门禁保证质量。

---

## 一、先想清楚：同事要"只能看"还是"能一起改"？

| 需求 | 做法 |
|------|------|
| 只看代码/下载 | 仓库是公开的，**什么都不用做**——把链接发给同事即可 |
| 能上传修改代码 | 需要你把同事**加为协作者（Collaborator）**，见第二节 |

⚠️ **一个重要提醒**：当前仓库是 **Public（公开）**，任何人都能看到全部代码。如果这套平台代码不想被外人看到，建议改成 Private：
1. 仓库页 → **Settings** → 拉到最底部 **Danger Zone** → **Change repository visibility** → Change to **Private** → 按提示输入仓库名确认。
2. 改成 Private 后，同事必须被加为协作者才能看到。

---

## 二、加协作者（能一起改代码）

你（`hujian-ux`，仓库主人）操作：

1. 打开 `https://github.com/hujian-ux/ruishenhang` → **⚙️ Settings**
2. 左侧 **Collaborators**（Access 栏目下）→ 点 **Add people**
3. 输入同事的 **GitHub 用户名或注册邮箱** → 角色选 **Write（可写）** → **Add**
4. GitHub 会给同事发邀请邮件；同事点 **Accept invitation** 接受后即可推送代码

> 撤销权限：同一页面找到该同事 → 右侧 **⋯** → **Remove**。

## 三、同事怎么拿到代码（Clone）

协作者接受邀请后，在自己电脑上：

```powershell
# 第一次：把仓库下载到自己电脑（会生成 ruishenhang 文件夹）
git clone https://github.com/hujian-ux/ruishenhang.git
cd ruishenhang
```
首次推送时同样会弹浏览器授权一次（之后免登录）。

## 四、协作流程（推荐：feature 分支 + PR）

**不要**大家都直接往 `main` 推——用分支 + 合并请求（PR），CI 门禁会自动把关：

```powershell
# 1. 每做一件事，先开一个新分支（分支名随便起，见名知意）
git switch -c feature/session-ui

# 2. 改代码、提交
git add -A
git commit -m "feat: 会话界面"

# 3. 推送这个分支
git push -u origin feature/session-ui
```

然后在 GitHub 仓库页点 **Compare & pull request** → 填写说明 → **Create pull request**。
此时 **quality-gate 三道门禁自动跑**（构建→RAG召回→客观评测）：
- ✅ 全绿 → 你点 **Merge pull request** 合并进 main
- ❌ 有红叉 → 改完再推一次，门禁重跑

## 五、保护 main 分支（可选但推荐）

防止有人直接 push 到 main 绕过门禁：
1. **Settings** → **Branches** → **Add branch ruleset**（或经典版 Add rule）
2. Branch name pattern 填 `main`
3. 勾选 **Require a pull request before merging**（必须走 PR）
4. 保存。以后 main 只能通过 PR + 门禁合并。

## 六、日常协作口诀（发给同事）

```
每天开工：git switch main → git pull        （拿最新代码）
做事开分支：git switch -c feature/xxx        （别在 main 上直接改）
提交三连：git add -A → git commit -m "说明" → git push
下班前：git push                             （备份 + 让别人看到进度）
冲突了：git pull --rebase → 解决冲突 → 再推
```

## 七、常见问题

| 报错 | 原因 | 解决 |
|------|------|------|
| `403 ... denied to xxx` | 还没被加为协作者 | 找仓库主人按第二节加人 |
| `rejected (non-fast-forward)` | 远程有新提交你本地没有 | 先 `git pull --rebase` 再推 |
| 推送弹出浏览器 | 首次登录授权 | 用自己的 GitHub 账号登录一次即可 |
| PR 上 CI 红叉 | 门禁没过（如召回率不达标） | 看红叉日志改代码，推上来自动重跑 |
