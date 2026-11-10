# 分支与 Worktree 规则（多 agent 并行开发）

> 适用范围：**会动源代码或测试文件的任务**（新功能/修复/重构/测试）——按任务判定：
> 任务触碰任何代码/测试文件即**整任务**走 worktree，顺带的文档更新在 worktree 里一起做。
> 以下可直接在 main 上做（照常 commit + 合并即推）：只读分析与问答；只改文档、配置、
> 纯资源文件（图片/内容/静态资产）的任务；一处以内的 typo/注释小修。
> **不以「当前只有我一个 agent」为由豁免**——你无法知道别的会话/机器/Multca worker
> 是否正在动同一仓库（2026-10-08 修订：原「单 agent 顺序开发可复用主目录」豁免
> 因前提不可自检而移除，改为按改动类型判定）。
> 本仓库（harness 根）自身改动走「即改即推」：直接 commit 到 main 并
> push，无需开分支。
> 事故背景：2026-08-21 实测——多 agent 共用同一工作目录，未提交改动随
> `git checkout` 被并行提交裹走。**分支只隔离提交历史，不隔离工作目录**；
> worktree 才隔离工作目录。
> 复盘 2026-08-26：「收尾清理」屡被跳过（一次巡检出 5 个已合并未删分支 +
> 空壳 worktree；PR 常在会话结束后由用户合并，届时无人清理；本地 main
> 未更新时 `branch -d` 也会失败被跳过）。对策 = 开工巡检自愈 + 收尾硬判定。
> 隔离单位 = 任务 / 主会话。session 内子 agent 共享主会话的 worktree
> （见 implement.md「子 agent 使用」），不另开，也**不因此豁免本规则**；
> 子工程的提交纪律（如直推 main）同样不豁免——在 worktree 分支上干，
> 合并回主分支再推，两者兼容。

## 规则

### 1. 开工：新任务 = 新 worktree + 新分支

```bash
git -C <仓库> fetch --prune
git -C <仓库> status --short            # 主目录有别人的在途改动：先问清归属（见下），确认后再 pull
git -C <仓库> pull origin main          # 从最新 main 起步
git branch --merged origin/main | grep -v main$   # 有输出 = 已合并残留，逐个 git branch -d
git worktree prune && git worktree list  # 先清失效登记；看现有 worktree 避免撞车，已合并挂着的空壳一并 remove
git worktree add ../.worktree/<仓库>-wt-<简述> -b feat/<简述>
cd ../.worktree/<仓库>-wt-<简述>        # 在这里开发
```

- **开工巡检（自愈）**：第 3 行清的是**已合并**残留——上个 agent 忘了清，你来清；
  `-d` 天然不会误删未合并分支，放心用。分支未合并、或 worktree 里有未提交改动的
  一律不动，报告用户。
- 发现 main 上有未提交改动：不是你的就不要动，先问清归属，绝不能顺手提交。
- 命名：分支用 `feat/<简述>` / `fix/<简述>` / `task/<简述>`（任务/实验）；worktree
  目录统一建在**仓库同级的 `.worktree/` 目录下**（不直接散在同级），子目录名
  `<仓库>-wt-<简述>`。**目录名必须以
  仓库标识开头**——同级多仓库并存，裸 `wt-`/`task-` 开头看不出归属。类型只放
  分支名，目录名不重复。简述 kebab-case ≤20 字符。
- **建后即补运行配置**：worktree 天生缺被 .gitignore 的部署/运行配置
  （`config/deploy.cfg`、`config/env`、`.env` 等）。本工程有 deploy 流程或需要
  本地起服务验证的，`worktree add` 后**立即**从主目录复制过来——不要拖到部署时
  才发现缺配置（rsync `--delete` 危险侧见 §3「从自己的 worktree 部署」）。
- **`.worktree/` 必须被本仓 `.gitignore` 覆盖**（防嵌套仓被 `git add -A` 当 gitlink
  提交进 main，2026-10-28 事故）。建 worktree 前自检：`git check-ignore .worktree
  >/dev/null && echo ok || echo "需先补 .gitignore"`；目录约定再改名时同步全家族
  各仓 .gitignore。校验只用 `git check-ignore`——`grep '^\.worktree'` 会误命中
  `.worktrees/` 旧条目。

### 2. 并行互不干扰：每个 agent 一个 worktree

每个 agent 一个 worktree，不在同一目录里切分支。worktree 共享仓库对象（不重复
占空间），但工作目录、未提交改动、运行时产物完全隔离。**是否并行不由你判断**：
默认总有其他会话/机器上的 agent 在途，动代码任务一律走 worktree；能否在 main
直改只看改动类型（见文件头适用范围），不看「有没有别人在跑」。

### 3. 收尾：PR 合并回 main → **部署** → 清理

```bash
git status --short          # 应只有自己的改动；只 add 自己动过的文件
git push -u origin feat/<简述>
gh pr create && gh pr merge --delete-branch   # 或 GitHub MCP；PR 留给用户合并也不阻塞，清理由下次开工巡检兜底
git -C <主目录> pull origin main              # 必须先更新本地 main，否则下一步 -d 必失败
git -C <主目录> push origin main              # 合并即推：主分支与远程推平（rsync 部署 ≠ 已 push；无远程的工程跳过）
git worktree remove ../.worktree/<仓库>-wt-<简述>  # 先删 worktree（分支还挂在其上，删不掉）
git branch -d feat/<简述>    # -d 失败 = 未真合并：停下排查，禁止换 -D 硬删
```

- **收尾硬判定**（三条全过才算完成，不满足不得报告「已完成」）：
  ```bash
  git branch --merged origin/main | grep -v main$   # ① 应无输出：无已合并未删分支
  git worktree list                                  # ② 自己的 <仓库>-wt-<简述> 已不在列表
  git rev-list --count origin/main..main            # ③ 应为 0：主分支与远程推平
  ```
  只清自己的：别的会话/机器的在途 worktree 与分支不算残留，不动也不等。

- **合并让 main 产生了代码改动 → 若该工程有部署流程，立即重新部署并验证
  health**（`./manage.sh deploy` + 重启 + health 检查）。合并不部署 = 生产
  还跑旧代码，后续「已修复」的说法都是假的。
- **从自己的 worktree 部署（主目录有别的 agent 在途改动时必选）**：deploy 是
  rsync 按源目录原样同步，脏主目录 = 把别人的半成品推上生产（2026-08-25 险情
  实录）。全新 worktree 天生缺被 gitignore 的部署配置，部署前从主 worktree
  复制本工程的部署配置；注意其中不在 rsync 排除表、属正常同步项的文件——
  源目录缺失会被 `--delete` 从远端**删掉**（危险侧失败），缺失即中止 deploy
  的属安全侧，事先分清。
- push 前执行危险文件扫描（`git diff origin/main..HEAD --name-only`）：
  credentials/密钥/`config/local.*`/`config/*.json` 发现即阻断；日志/缓存确认
  `.gitignore` 覆盖。
- 每个 worktree 只做一件事；遵守范围限定，不因同一 worktree 混改多个子工程。

### 4. 切分支的铁律

任何 `git checkout` 切换之前工作区必须干净（commit 或 stash）。带着未提交改动
切分支 = 把半成品送到别人的分支上。

### 5. 子工程差异

- **主分支名**：示例命令里的 `main` 按该仓实际主分支替换（个别仓库用
  `master`）；巡检/清理用 `git branch --merged origin/<主分支>`。
- **合并方式**：部分子工程走本地 `git merge` 而非 PR。合并后顺序不变：
  本地主分支更新 → `git push`（**合并即推**，有远程必做）→ `git worktree remove`
  → `git branch -d`；`-d` 失败说明未真合并，停下排查，禁止 `-D` 硬删。

### 6. 共享 venv 工程（trade-system）的 worktree 纪律

- worktree 里 `.venv` **软链到主 checkout**（`ln -s <主worktree>/.venv .venv`），
  不装独立环境；测试用 `PYTHONPATH=src .venv/bin/pytest`（PYTHONPATH 让 src
  解析到 worktree 自己的代码，venv 只出解释器和依赖）。
- **禁止在 worktree 里跑 `uv run` / `uv sync` / `manage.sh init|rebuild|start|deploy`**：
  uv 会把共享 venv 的 editable pth 改写到 worktree，删 worktree 后主环境
  import 断、金丝雀假绿。manage.sh 已加软链守卫（deploy 直拒、check/status
  安全）；pth 被改写时 deploy 前置自愈会修回主 src。
