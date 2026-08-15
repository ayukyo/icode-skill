# worktree_isolation.md — git worktree 多需求隔离（运行时规范）

> 新建工单入口执行本规范。各步骤引用本文件时必须 **Read 完整内容**再执行（不得凭概述/记忆）。
> 核心：**一个需求 = 一个分支 `icode/<slug>` + 一个兄弟目录 worktree**，主工作区保持干净，多需求并行互不污染。

---

## 1. 决策与创建（新建工单入口）

只对**新建工单**触发（`/icode init/log/start/plan/fast` 判定为「新建」时）；续跑不触发（见 §2 cwd 契约）。

**① 入口询问（每工单一次）**：问用户「本工单用 worktree 隔离吗？（独立分支 + 独立目录，并行互不污染）还是就在当前工作区做？」
- 答「用」→ ② 创建
- 答「不用」→ 原地（走现有「创建新目录」），**不记** worktree metadata
- limit 已写「worktree 默认关闭」→ 跳过询问直接原地

**② 创建（答「用」时）**：创建前展示将创建的 路径 + 分支名，等用户最终确认（创建是写操作影响 `.git`）：

```bash
git rev-parse --is-inside-work-tree             # 前置：必须在 git 仓库（失败→原地降级）
git rev-parse --verify HEAD >/dev/null 2>&1     # 前置：仓库必须有提交（无 HEAD 不能建 worktree）
test -f "$(git rev-parse --show-toplevel)/.git" && { echo "已在 worktree 内→原地"; WT_SKIP=1; }  # 主仓才建
git worktree list                               # 只读：确认目标路径/分支名未占用
[ -z "$WT_SKIP" ] && git worktree add -b "icode/<ticket-slug>" "../<repo>-wt-<ticket-slug>"
```

- **命名**：目录 `<repo目录名>-wt-<ticket-slug>`（主工作区同级兄弟，勿嵌套在主工作区路径内）；分支 `icode/<ticket-slug>`；slug 英文短横线 ≤30 字符小写，冲突时追加序号 `-2`
- **分支基于主仓当前 HEAD**：主工作区未提交改动不会带入 worktree（隔离目的）；worktree 创建不要求主工作区干净
- **含 submodule**：worktree 内 submodule 目录默认**为空**（gitlink 在、内容未检出）——首次 `git submodule update --init`，各 worktree 各自 init，别当丢失
- **repo 多仓库工程**：repo 根非 git 仓库 → 触发判定自动原地降级；子仓库内 cwd 命中 git-root → 该子仓库可单独 worktree 化

**③ 创建后**：
- `cd` 进 worktree → 按 SKILL.md「创建新目录」逻辑在 worktree 内生成 `.icode_output/.icode_output_N`（worktree 内无旧产物 → 通常恒为 `_1`；编号规则不变，不重排），本工单全部产物在 worktree 内
- **校验 worktree 内 `.icode_output/` 应为空**——非空 = 该工程 `.icode_output` 未 gitignore（worktree 带入主仓旧产物）→ 提示「建议配置 `.gitignore` 排除 `.icode_output/`」，L3 不阻断
- metadata 写入（**时序**：worktree 路径在入口已确定，由本工单**首次创建 `.ico_metadata.json` 时**落盘——B7 创建 worktree 在 mkdir 前、metadata 尚不存在，故不在此处写，而在入口步骤生成 metadata 时带上）：`worktree_path`（worktree 绝对路径，非 null）/ `worktree_branch`（`icode/<slug>`），见 §3；降级时同批写 `wt_degraded=true`

**④ 失败降级**：创建失败（**无 HEAD（仓库无提交）**/路径冲突/无写权限/FS 不支持/命名冲突修正后仍失败）→ 原地建工单 + metadata 记 `wt_degraded=true` + 报告说明原因（L3 警告，不阻断）。

---

## 2. cwd 契约（续跑硬性前提）

- 续跑 worktree 工单（`review/code/deepcheck/audit/patch/status/readme`）**必须先 `cd` 进对应 worktree**——产物在 worktree 内 `.icode_output_N/`，主工作区找不到；且在错误 checkout 会命中**错误的最新工单**
- 定位：`git worktree list` 找到对应 worktree → `cd <worktree>` → 调步骤命令；或读 metadata `worktree_path`
- 已在 worktree 内再新建工单 → 不再嵌套，**原地建普通工单且 `worktree_path` 不写（null）**——本工单非 worktree 隔离工单（不触发回流提醒/remove 关联；避免与既有 worktree 工单共享工作树时被误当隔离工单，导致回流/清理互相干扰）。产物在当前 checkout 的 `.icode_output_N` 内，续跑仍在当前 checkout（cwd 契约照常，勿在主仓跑——物理产物在 worktree 内，主仓找不到）

---

## 3. metadata 字段族

| 字段 | 类型/默认 | 语义 |
|------|-----------|------|
| `worktree_path` | string / `null` | 本工单所在 worktree 绝对路径；**非 null = 在 worktree 内**（status 列/audit 回流提醒/readme 状态段均读它）；回流 remove 后随 worktree 消失 |
| `worktree_branch` | string / `null` | 本工单分支 `icode/<slug>`（= `created_branch`） |
| `wt_degraded` | bool / `false` | worktree 创建失败降级原地标记 |
| `cross_project_refs` | array / `[]` | 跨工程 worktree 引用：A 工单转工单到关联工程 B 时追加 `{project_id, ticket_id, worktree_path}` 指向 B 工单及其 worktree。**回填时序**：B 工单创建并写入自身 metadata（含 `worktree_path`/`ticket_id`）后，回填 A 的 `cross_project_refs`——先写占位（转出时记 `{project_id, ticket_id: "待B侧回填", worktree_path: null}`），B 落盘后回填补全，防 A 侧在 B 尚未创建时无值可写 |

---

## 4. 完成后回流（icode 不 commit，用户手动执行）

icode 自身 Git 红线不变：**禁止 `commit` / `push` / `reset --hard` / `push --force`**；`git merge` 属用户手动回流范畴。工单完成时输出**二选一**清理指引：

```bash
# 方案 ①（推荐：先提交再合并回流，最干净）
# 在 worktree 内（icode 已出改动清单，用户审阅后自行提交）
# ⚠️ commit 前确认 .icode_output/ 未被 git 跟踪（未 gitignore 时 git add -A 会把产物目录误提交）
git add -A && git commit -m "<本工单改动>"        # 用户执行，icode 不 commit
# 在主工作区执行
git switch master                                # 或目标基分支
# ⚠️ merge 前主工作区状态：带未提交改动且与 merge 文件重叠 → git 拒绝并中止（保护，先 stash 再 merge）；
#    不重叠 → 允许 merge，未提交改动保留
git merge icode/<ticket-slug>                    # 或走 PR
git worktree remove ../<repo>-wt-<ticket-slug>   # ⚠️ 顺序：先 remove 再 branch -d
git branch -d icode/<ticket-slug>                # worktree 已删、分支未被占用才可删

# 方案 ②（不提交：手动带出改动）
# 在主工作区手动复制/应用 worktree 内改动文件 → 确认已带出后
git worktree remove --force ../<repo>-wt-<ticket-slug>   # --force 仅限改动已另行保存后，用户自行斟酌
```

- **顺序陷阱（两重保护）**：`git branch -d` 有两道检查——① 分支仍 checkout 于 worktree 时被拒 → **先 `worktree remove` 再 `branch -d`**；② 分支未完全合并时被拒（`没有完全合并`）→ 方案① merge 后自然满足；只 commit 不 merge（想暂留分支）则 branch -d 被拒是 git 正常保护——保留分支等以后合并，或用户自行 `git branch -D`（icode 不执行 `-D`）
- **严禁**未处理改动就 remove（会失败——失败是保护，绝不由 icode 自动 `--force`）
- **回流前产物留档**：07_readme 交付报告与产物都在 worktree 内，remove 后随之消失（全局索引仅留核心字段）——需留档先复制出 worktree 再 remove
- **改动涉及 submodule**：submodule 内改动需**在 worktree 内 submodule 里单独 commit**（主仓 `git add -A && git commit` 只更新 gitlink，不带 submodule 内部改动）
- 未完成工单：worktree 保留，`git worktree list` 可随时看到，`cd` 回去续跑

---

## 5. 防误删护栏

1. worktree 创建/清理**必须用户确认**（写操作影响 `.git`）
2. **永不自动 remove** / **永不自动 `--force` remove**
3. 精确分支名 `icode/<slug>` 操作，不触碰其他工单的分支/worktree
4. `worktree_path` 溯源：判断工单归属 checkout 一律读它，不用猜测
5. `log` 同级目录扫描天然排除 worktree 兄弟（`.git` 是普通文件，`test -d` 判据自动区分），勿误收为姐妹工程

---

## 6. 空间自查（防僵尸 worktree）

忘清 worktree = 一份完整工作树拷贝 + 各自独立构建产物（磁盘近似翻 N 倍）。

```bash
git worktree list                              # 看所有 worktree 与分支（首行是主仓）
git worktree list --porcelain | grep -c '^worktree '   # 数量；实际额外 worktree 数 = 计数 − 1（首行是主仓自身）
du -sh <各 worktree 路径>                      # 空间占用
```

- **`git worktree prune` 对目录仍在的僵尸无效**（只清目录已删的残留），真正清理只能 `git worktree remove <path>`
- 维护纪律：做完即合并即 remove，勿堆积僵尸 worktree

---

## 7. 常见坑速查

1. **`.git` 是普通文件**（非目录/symlink）= git worktree 成员，`git status` 正常，勿误判为损坏
2. **project_id 归主仓**（F1）：worktree 内 `git rev-parse --show-toplevel` 返回 worktree 根，project_id 必须归一到主仓根（`git worktree list --porcelain` 首行，勿用 `$2` 字段避免含空格路径截断）——否则 limit 主存 / project_docs / device_config 读不到
3. **验证基线落后**：worktree 分支落后主仓时（`git rev-list --count <branch>..<目标基分支>` > 0 = 落后），验证前建议先 `git merge <目标基分支>` 进 worktree 分支再终审（L3 提示）
4. **机器校验落点**：产物集机器校验（`status --validate` / 终检）必须在对应 worktree 内执行，主仓跑会误报缺失
5. **并发写竞态**：多 worktree = 多会话并行，index.json / limit 主存写入按「读最新 → 合并本会话改动 → 原子写」契约，勿在旧快照覆盖
6. **步骤3 merge ≠ git merge**：`/icode merge` 是文档定稿，与 `git merge` 回流无关
