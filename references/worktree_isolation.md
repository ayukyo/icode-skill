# worktree_isolation.md — git worktree 多需求隔离（运行时规范）

> 新建工单入口执行本规范。各步骤引用本文件时必须 **Read 完整内容**再执行（不得凭概述/记忆）。
> 核心：**一个需求 = 一个分支 `icode/<ticket-slug>` + 一个兄弟目录 worktree**，主工作区保持干净，多需求并行互不污染。

---

## 1. 决策与创建（新建工单入口）

只对**新建工单**触发（`/icode init/log/start/plan/fast` 判定为「新建」时）；续跑不触发（见 §2 cwd 契约）。

**① 参数触发（opt-in · 两种形式之一触发，AI 不弹问）**：

**触发形式（满足其一即触发，AI 不再询问）**：
1. **flag 形式**：用户消息命令位置出现**独立 token** `--worktree`（**只接受双短横独立 token**：`--worktree`，**不接受** `-worktree`（单短横）/ `--worktree=true` / `--worktree true` / 缩写变体——拒绝 AI 自主解析变体导致误触；与其它独立 flag token 共存如 `--listen`/`--test`）
2. **自然语言意图声明**：用户在消息正文里显式声明意图，常见措辞如「用 worktree 隔离做」「走 worktree」「独立分支做」「在 worktree 里做」

**不触发（防误触，AI 必须做语境识别）**：
- 消息**正文叙述/引用**（反引号包裹、代码块、问题解释、文档片段、日志转贴）中提到 `--worktree` / `worktree` 等字眼 ≠ 触发——语境属"讨论参数"而非"下达命令"

**反向声明（后置优先）**：
- 同一消息中同时出现**正向声明**（如「用 worktree 隔离做」）与**反向声明**（如「别用 worktree」「不要 worktree 隔离」「普通做就行」「算了不用 worktree」）时，AI 取**后置声明**作为最终意图——最后一句即最终意图（对话通用理解）

**语境识别失败降级**：
- 语境模糊难以判定时**不弹问**，按"未触发"处理（默认原地）+ L1 触发回显暴露给用户即时纠错（用户看到 `▶ worktree 隔离：未启用` 可主动澄清"用 worktree"补触发）

**不弹问原则**：
- 识别不触发即默认原地（**不主动询问"要不要 worktree？"**），符合 opt-in 默认语义
- **不得自创理由另行弹问**（如"这个需求跨模块、要不要 worktree 隔离？"）——是否用 worktree = 用户消息意图决定，不由 AI 判断；AI 自作主张弹问 = 违规
- **唯一例外**：limit 「worktree 强制禁止」红线命中时，AI 必须**提示一次**"本工程 limit 禁止 worktree，本工单回退原地建"+ 回退原地（**违规时阻止**，不同于 opt-in 弹问；详见 [steps/limit.md](../steps/limit.md) §7 + 真源说明）

**触发回显（强制 L1，区分判定态与执行态）**：
- **判定态·触发**（创建前）：AI 在回复顶部输出 `▶ worktree 隔离：即将启用 → 准备创建 ../<repo>-wt-<ticket-slug>/（分支 icode/<ticket-slug>）` —— 路径与分支名**动态回填实际值**（用 LLM 本会话内的 ticket-slug），**不用 `<ticket-slug>` 占位符**（避免用户困惑尖括号）
- **判定态·未触发**：`▶ worktree 隔离：未启用（默认，原地建工单）`
- **执行态·成功**（创建完成后）：`▶ worktree 隔离：✓ 已创建 ../<repo>-wt-<ticket-slug>/（分支 icode/<ticket-slug>）`
- **执行态·失败**（降级原地 + `wt_degraded=true`）：`▶ worktree 隔离：⚠ 创建失败，降级原地（wt_degraded=true，原因：<错误>）`
- 四态均为**强制**，让用户即时确认意图识别与执行结果（防误触/漏触静默发生；连续两态让"判定→执行"过程透明）

**历史事故回顾（设计意图）**：旧版「硬门询问」曾是入口必弹问，目的是防 AI 自作主张跳过 worktree；改为 opt-in 后由用户消息意图显式表达，从源头消除「AI 自弹问 / AI 自跳过」的两端自决问题，更直接

**② 创建（带 `--worktree` 时）**：创建**前**展示路径 + 分支名作为**告知**（**非再次询问**；用户触发意图即一次性同意；写操作前最后公示，避免用户对 worktree 路径不可见；语义沿用旧版"展示"措辞，但解读从"等确认"改为"公示告知"）：

**`<ticket-slug>` 占位符语义（**关键，避免 AI 误用**）**：
- **定义**：由 AI 在判定·触发之后、执行·创建之前**自行提炼**（基于当前需求文本；命名规则见下方「命名」段）
- **与 ticket_id 区别**：ticket_id = `{工程名}-{N}`（步骤8 索引写入后回填，**带工程名前缀+目录号 N**）；`<ticket-slug>` 是**纯英文短横线 slug**（不带前缀、不带 N），早于 ticket_id 生成
- **回显与创建共用一处**：判定·触发回显中"准备创建 ../<repo>-wt-<ticket-slug>/（分支 icode/<ticket-slug>）"→ 执行·创建 `git worktree add -b "icode/<ticket-slug>" "../<repo>-wt-<ticket-slug>"` → **两者必须用同一值**（一处提炼两处用）
- **冲突处理**：与 `git worktree list` 已存在的路径/分支冲突 → 追加 `-2` / `-3`（见下方「命名」段）；提炼后立即用 `git worktree list` 检查冲突，命中即重提炼
- **AI 必须自己提炼、勿向用户索取**；勿用占位符字符串直接执行创建
- **回显中勿字面输出尖括号**：先提炼再回填，**绝不**让用户看到 `<slug>` 字面

```bash
git rev-parse --is-inside-work-tree             # 前置：必须在 git 仓库（失败→原地降级）
git rev-parse --verify HEAD >/dev/null 2>&1     # 前置：仓库必须有提交（无 HEAD 不能建 worktree）
test -f "$(git rev-parse --show-toplevel)/.git" && { echo "已在 worktree 内→原地"; WT_SKIP=1; }  # 主仓才建
git worktree list                               # 只读：确认目标路径/分支名未占用
# 基线 ref：worktree 分支基于「主仓当前分支的远程跟踪」创建并自动设置 upstream（git 行为：以远程 ref 为起点 -b 建分支即设 tracking）
WT_BASE_REF=$(git rev-parse --symbolic-full-name @{u} 2>/dev/null)   # 如 refs/remotes/origin/master；无 upstream 时为空
WT_MAIN_BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null)        # 主仓当前分支名；detached HEAD 时输出 "HEAD"
WT_BASE_AVAIL=0
[ -n "$WT_BASE_REF" ] && git rev-parse --verify "$WT_BASE_REF" >/dev/null 2>&1 && WT_BASE_AVAIL=1   # 远程 ref 本地存在才可用
# —— G1 提交契约闸门（修改型工单默认；只读工单显式声明 WT_READONLY=1 跳过、不写契约）——
# 修改型工单（要改代码/文档，需最终提交）任一不满足 → L1 阻断，回退原地建工单（不静默 L3 降级）：
#   detached HEAD（无命名分支）/ 无 upstream（@{u} 为空）/ 远程 ref 本地不可解析（须先 git fetch）
#   ——提交目标不明确时不得基于本地 HEAD 建分支（历史事故：无 upstream 误推同名远端 ticket 分支）
if [ -z "$WT_SKIP" ] && [ "$WT_READONLY" != "1" ]; then
  if [ "$WT_MAIN_BRANCH" = "HEAD" ] || [ -z "$WT_BASE_REF" ] || [ "$WT_BASE_AVAIL" != "1" ]; then
    echo "▶ worktree 隔离：⚠ L1 阻断——主仓提交目标不明确（detached HEAD / 无远程跟踪 @{u} / 远程 ref 不可解析），请先 git switch <命名分支> 并 git branch --set-upstream-to=<remote>/<目标分支>，或声明只读工单"
    WT_SKIP=1
  fi
fi
if [ -z "$WT_SKIP" ]; then
  git worktree add -b "icode/<ticket-slug>" "../<repo>-wt-<ticket-slug>" "$WT_BASE_REF"   # 基于远程基线 + 自动 tracking
  # G1 显式设置 upstream（不依赖隐式 tracking）：指向创建时冻结的目标 remote ref
  git -C "../<repo>-wt-<ticket-slug>" branch --set-upstream-to="${WT_BASE_REF#refs/remotes/}" "icode/<ticket-slug>"
  # G1 逐项比对（全部通过才把 tracking_verified 置 true，契约落 metadata 见 ③）：
  #   HEAD 可解析 / 当前分支 == icode/<ticket-slug> / @{u} == WT_BASE_REF / remote 名集合与主仓一致
  git -C "../<repo>-wt-<ticket-slug>" rev-parse --verify HEAD >/dev/null 2>&1 \
    && [ "$(git -C "../<repo>-wt-<ticket-slug>" rev-parse --abbrev-ref HEAD)" = "icode/<ticket-slug>" ] \
    && [ "$(git -C "../<repo>-wt-<ticket-slug>" rev-parse --symbolic-full-name @{u} 2>/dev/null)" = "$WT_BASE_REF" ] \
    && [ "$(git -C "../<repo>-wt-<ticket-slug>" remote)" = "$(git remote)" ] \
    && WT_TRACKING_VERIFIED=1 || WT_TRACKING_VERIFIED=0
  [ "$WT_TRACKING_VERIFIED" != "1" ] && echo "▶ worktree 隔离：⚠ G1 逐项比对失败 → tracking_verified=false（进入 code 前按 §3.8 ⑩ L1 阻断，不得自动改契约）"
fi
```

- **命名**：目录 `<repo目录名>-wt-<ticket-slug>`（主工作区同级兄弟，勿嵌套在主工作区路径内）；分支 `icode/<ticket-slug>`；slug 英文短横线 ≤30 字符小写，冲突时追加序号 `-2`
- **基线 = 主仓当前分支的远程跟踪（`@{u}`）**：worktree 分支基于远程最新基线创建（不是本地 HEAD），自动设置 upstream——`git status` 正确显示 ahead/behind、`git pull` 可跟上上游、后续 merge 目标明确，从源头减少"基于过时基线修改"（与 §3.5 `active_checkout.base_ref` 记录一致）。**主工作区未提交改动不会带入 worktree**（隔离目的）；worktree 创建不要求主工作区干净。主仓本地已提交未推送的内容同样不进入 worktree（隔离语义，非故障）
- **含 submodule**：worktree 内 submodule 目录默认**为空**（gitlink 在、内容未检出）——首次 `git submodule update --init`，各 worktree 各自 init，别当丢失
- **repo 多仓库工程**：repo 根非 git 仓库 → 触发判定自动原地降级；子仓库内 cwd 命中 git-root → 该子仓库可单独 worktree 化；**super-repo 做了 worktree 但业务代码在业务子仓时，worktree 不覆盖子仓（子仓有自己的 .git 在原路径）——实际修改子仓须建子仓隔离 checkout，见本段「⑤ 业务子仓隔离」**

**③ 创建后**：
- `cd` 进 worktree → 按 SKILL.md「创建新目录」逻辑在 worktree 内生成 `.icode_output/.icode_output_N`（worktree 内无旧产物 → 通常恒为 `_1`；编号规则不变，不重排），本工单全部产物在 worktree 内
- **校验 worktree 内 `.icode_output/` 应为空**——非空 = 该工程 `.icode_output` 未 gitignore（worktree 带入主仓旧产物）→ 提示「建议配置 `.gitignore` 排除 `.icode_output/`」，L3 不阻断
- metadata 写入（**时序**：worktree 路径在入口已确定，由本工单**首次创建 `.ico_metadata.json` 时**落盘——B7 创建 worktree 在 mkdir 前、metadata 尚不存在，故不在此处写，而在入口步骤生成 metadata 时带上）：`worktree_path`（worktree 绝对路径，非 null）/ `worktree_branch`（`icode/<ticket-slug>`），见 §3；降级时同批写 `wt_degraded=true`。**worktree 工单同批构造 `active_checkout`**（见 §3.5）：`{path: <worktree 绝对路径>, branch: <worktree_branch>, base_ref: <创建基线 ref（`@{u}` 原样或 null）>, base_commit: <创建基线 commit（`git rev-parse <基线>`，无基线用当前 HEAD commit）>, activated_at: <运行时时间戳>, state: "active"}`——base_ref/base_commit 是后续迁移预检（[steps/worktree.md](../steps/worktree.md) §6.2）的目标基线依据
- **修改型工单同批写 `submission_contracts`**（G1 冻结，见 §3.5.5）：G1 逐项比对通过（`WT_TRACKING_VERIFIED=1`）后构造 super 仓库契约一项——`repo_role="super"`、`repo_path`=worktree 绝对路径、`source_repo_path`=主工作区根、`worktree_branch`=`icode/<ticket-slug>`、`source_branch`/`source_upstream`=`WT_MAIN_BRANCH`/`WT_BASE_REF`、`remote_name`/`remote_url`=`git remote`/规范化 URL、`target_remote_ref`=`WT_BASE_REF`、`target_push_ref`=`refs/heads/<远端分支名>`（`WT_BASE_REF` 的远端分支部分，如 `refs/remotes/origin/master` → `master`）、`target_commit_at_create`=`git rev-parse "$WT_BASE_REF"`、`push_refspec`=`HEAD:refs/heads/<远端分支名>`、`tracking_verified` 按 G1 比对实际、`created_at`=运行时时间戳。只读工单（`WT_READONLY=1`）不写契约（留 `[]`）

**④ 失败降级**：创建失败（**无 HEAD（仓库无提交）**/路径冲突/无写权限/FS 不支持/命名冲突修正后仍失败）→ 原地建工单 + metadata 记 `wt_degraded=true` + 报告说明原因（L3 警告，不阻断）。

**⑤ 业务子仓隔离（repo 多仓库工程，worktree 工单进入 code 前）**：
- **问题**：repo 管理工程 = super-repo + 多个业务子仓（各自**独立 git 仓库**，经 `.repo/manifest.xml` 管理，常按业务域分组到 `<业务域分组目录>/<模块名>` 嵌套路径）。git worktree 只隔离 **super-repo** checkout；业务子仓有自己的 `.git` **在原工程路径**，不在 worktree 内，super-repo worktree 内对应相对路径为空（super-repo 不跟踪子仓内容）。若直接操作 worktree 内子仓路径 → 命中原工程路径子仓 → **污染原工程、多需求并行改同一子仓冲突**
- **识别时机**：worktree 工单进入 code（步骤4）前，读 `03_plan_final.md` 的 code_files/§5 符号清单确定**实际修改的业务子仓集**；不涉及子仓修改（只改 super-repo）→ 跳过本段，无需隔离
- **隔离命令**（对每个受影响原子仓，把 checkout 放进 super-worktree **同名相对路径**，保持路径结构与原工程一致，worktree 内访问该路径 = 隔离子仓；子仓分支同样基于**子仓当前分支的远程跟踪**创建并自动设置 upstream）：
  ```bash
  SUB_UP=$(git -C "<主仓绝对路径>/<子仓相对路径>" rev-parse --symbolic-full-name @{u} 2>/dev/null)
  SUB_UP_AVAIL=0
  [ -n "$SUB_UP" ] && git -C "<主仓绝对路径>/<子仓相对路径>" rev-parse --verify "$SUB_UP" >/dev/null 2>&1 && SUB_UP_AVAIL=1
  if [ "$SUB_UP_AVAIL" = "1" ]; then
    git -C "<主仓绝对路径>/<子仓相对路径>" worktree add -b "icode/<ticket-slug>-<子仓slug>" "<主仓绝对路径>-wt-<ticket-slug>/<子仓相对路径>" "$SUB_UP"
  else
    git -C "<主仓绝对路径>/<子仓相对路径>" worktree add -b "icode/<ticket-slug>-<子仓slug>" "<主仓绝对路径>-wt-<ticket-slug>/<子仓相对路径>"
  fi
  # G1 子仓提交契约（与 super 一视同仁，见 §3.5.5）：修改型工单的子仓 detached / 无 upstream → L1 阻断进入 code，
  #   不得静默基于本地 HEAD——提交目标不明确时先为子仓设置 upstream（历史事故：ticket 分支无 upstream 被误推到新建同名远端分支）
  SUB_MAIN_BRANCH=$(git -C "<主仓绝对路径>/<子仓相对路径>" rev-parse --abbrev-ref HEAD 2>/dev/null)
  if [ "$SUB_UP_AVAIL" != "1" ] || [ "$SUB_MAIN_BRANCH" = "HEAD" ]; then
    echo "▶ worktree 隔离：⚠ L1 阻断——子仓 <子仓相对路径> 提交目标不明确（detached / 无远程跟踪 @{u}），请先为子仓 git branch --set-upstream-to=<remote>/<目标分支> 或声明只读"
  else
    # 创建后显式 set-upstream-to + 逐项比对（tracking_verified 按实际写入子仓契约）
    git -C "<主仓绝对路径>-wt-<ticket-slug>/<子仓相对路径>" branch --set-upstream-to="${SUB_UP#refs/remotes/}" "icode/<ticket-slug>-<子仓slug>"
    git -C "<主仓绝对路径>-wt-<ticket-slug>/<子仓相对路径>" rev-parse --verify HEAD >/dev/null 2>&1 \
      && [ "$(git -C "<主仓绝对路径>-wt-<ticket-slug>/<子仓相对路径>" rev-parse --abbrev-ref HEAD)" = "icode/<ticket-slug>-<子仓slug>" ] \
      && [ "$(git -C "<主仓绝对路径>-wt-<ticket-slug>/<子仓相对路径>" rev-parse --symbolic-full-name @{u} 2>/dev/null)" = "$SUB_UP" ] \
      && [ "$(git -C "<主仓绝对路径>-wt-<ticket-slug>/<子仓相对路径>" remote)" = "$(git -C "<主仓绝对路径>/<子仓相对路径>" remote)" ] \
      && SUB_TRACKING_VERIFIED=1 || SUB_TRACKING_VERIFIED=0
    [ "$SUB_TRACKING_VERIFIED" != "1" ] && echo "▶ worktree 隔离：⚠ G1 子仓逐项比对失败 → tracking_verified=false（进入 code 前按 §3.8 ⑩ L1 阻断）"
  fi
  ```
  子仓须有 HEAD（repo 子仓均有）；`<子仓slug>` = 子仓目录名短横线小写，冲突追加 `-2`；目标路径须为空（super-repo worktree 内该相对路径未被写入）
- **门禁（硬门，防直接改原路径）**：进入 code 前若涉及业务子仓修改，**必须**已为这些子仓建立隔离 checkout（`git worktree list -C <原子仓>` 确认隔离路径已存在）——**未隔离即改子仓 = 直接改原工程路径，不合规**。禁止在 worktree 内把子仓文件映射/软链到原路径（破坏隔离）。历史事故：AI 曾靠模型智能自行加门禁提示而非由 icode 规范保证——本段把该经验固化为规范，AI 不得再自行裁量
- **metadata**：首次建子仓隔离时写 `sub_worktrees` 数组追加 `{sub_path, worktree_path, branch}`（见 §3），便于续跑定位 + 回流回收；**修改型工单同步往 `submission_contracts` 追加该子仓契约一项**（G1 冻结，`repo_role="sub"`、`repo_path`=子仓隔离 checkout 路径、`source_repo_path`=原子仓路径、`target_remote_ref`/`target_push_ref`=子仓上游远端分支、`tracking_verified` 按 G1 比对实际，结构见 §3.5.5）
- **续跑**：子仓隔离 checkout 在 super-worktree 内，续跑 `cd` 进 super-worktree 后子仓文件即位于 worktree 内对应相对路径，正常操作（cwd 契约照常，见 §2）
- **回流回收**：super-worktree remove 前，先逐个 `git -C <原子仓> worktree remove <子仓隔离路径>`（再 `git -C <原子仓> branch -d icode/<ticket-slug>-<子仓slug>`），再 remove super-worktree（见 §4）

---

## 2. cwd 契约（续跑硬性前提）

- 续跑 worktree 工单（`review/code/deepcheck/audit/patch/status/readme`）**必须先 `cd` 进对应 worktree**——产物在 worktree 内 `.icode_output_N/`，主工作区找不到；且在错误 checkout 会命中**错误的最新工单**
- 定位：`git worktree list` 找到对应 worktree → `cd <worktree>` → 调步骤命令；或读 metadata `active_checkout`（缺失按 §3.7 用 `worktree_path` 推导）
- **业务子仓续跑**：含子仓隔离的 worktree 工单，`cd` 进 super-worktree 后业务子仓文件即位于 worktree 内对应相对路径（见 §1「⑤ 业务子仓隔离」），正常操作；勿 cd 回原工程路径的子仓改代码（污染）
- 已在 worktree 内再新建工单 → 不再嵌套，**原地建普通工单且 `worktree_path` 不写（null）**——本工单非 worktree 隔离工单（不触发回流提醒/remove 关联；避免与既有 worktree 工单共享工作树时被误当隔离工单，导致回流/清理互相干扰）。产物在当前 checkout 的 `.icode_output_N` 内，续跑仍在当前 checkout（cwd 契约照常，勿在主仓跑——物理产物在 worktree 内，主仓找不到）

---

## 3. metadata 字段族

| 字段 | 类型/默认 | 语义 |
|------|-----------|------|
| `worktree_path` | string / `null` | 本工单所在 worktree 绝对路径；**非 null = 在 worktree 内**。**活动 checkout 判定统一读 `active_checkout`（缺失按 §3.7 用本字段内存推导）**——status 列/audit 回流提醒/readme 状态段均读推导后的活动根；回流 remove 后随 worktree 消失 |
| `worktree_branch` | string / `null` | 本工单分支 `icode/<ticket-slug>`（= `created_branch`） |
| `wt_degraded` | bool / `false` | worktree 创建失败降级原地标记 |
| `sub_worktrees` | array / `[]` | 业务子仓隔离 checkout 记录（repo 多仓库工程，仅涉及子仓修改的 worktree 工单）：数组元素 `{sub_path, worktree_path, branch}`——`sub_path`=子仓相对 super-repo 的路径、`worktree_path`=子仓隔离 checkout 绝对路径（在 super-worktree 内同名相对路径）、`branch`=`icode/<ticket-slug>-<子仓slug>`。首次建子仓隔离时追加，回流回收时清。见 §1「⑤ 业务子仓隔离」 |
| `cross_project_refs` | array / `[]` | 跨工程 worktree 引用：A 工单转工单到关联工程 B 时追加 `{project_id, ticket_id, worktree_path}` 指向 B 工单及其 worktree。**回填时序**：B 工单创建并写入自身 metadata（含 `worktree_path`/`ticket_id`）后，回填 A 的 `cross_project_refs`——先写占位（转出时记 `{project_id, ticket_id: "待B侧回填", worktree_path: null}`），B 落盘后回填补全，防 A 侧在 B 尚未创建时无值可写 |
| `artifact_root` | string / `null` | **产物权威根**（绝对路径）：`.ico_metadata.json` 和各步骤产物的权威存储位置。默认推导 = 当前 `.ico_metadata.json` 所在工单目录；仅显式迁移产物时才写。所有步骤**读写 ICode 产物一律走它**（见 §3.5） |
| `active_checkout` | object / `null` | **活动实现根**：当前允许修改、编译、测试和提交代码的**唯一** checkout（对象结构见 §3.5）。null = 无活动 checkout（原地工单 / 已 close） |
| `checkout_history` | array / `[]` | **checkout 历史**：曾服务于本工单的 checkout 及其迁移/关闭状态（元素结构见 §3.5） |
| `migration` | object / `null` | **迁移事务记录**：进行中或已结束的迁移事务（对象结构见 §3.5）。null = 无迁移事务 |
| `submitted_baseline` | string / `null` | **提交后在线基线**（兼容旧字段）：close 后记录用户提交到达的目标 commit（见 [steps/close.md](../steps/close.md)）；逐仓化后的新真源是 `submitted_baselines`（见下） |
| `submitted_baselines` | array / `[]` | **提交后逐仓在线基线**（新真源）：close（G4）通过后逐仓记录 `{repo_path, target_remote_ref, commit}`（见 §3.5.5）。**写 `submitted_baselines` 时同步维护 `submitted_baseline`**（super repo 的 commit，兼容旧读者）；旧工单只有单字段时读单字段推导 |
| `submission_contracts` | array / `[]` | **提交契约**（提案「worktree-upstream-push-guard」落地）：每个产生代码/文档改动的 git 仓库（super repo 与业务子仓**一视同仁**）的提交目标冻结清单（元素结构见 §3.5.5）。G1 创建时逐仓冻结，G2/G3/G4 据此校验；**只读工单可不写**（无提交目标） |
| `submission_audit` | object / `null` | **提交契约审计缓存**：G2/G3/G4 最近一次审计结果 `{last_checked_at, verdict, repos}`（`verdict` ∈ `pass`/`behind`/`blocked`/`unknown`；`behind`= 存在落后仓库需先 fetch/merge/rebase；`repos` = 已核验仓库相对清单）。仅缓存展示用，**判定永远实时重跑**，不因缓存通过而跳过 |

> 生命周期/提交契约字段是**可选字段**（缺失按 §3.7 兼容推导），只由**迁移 / close / reopen / schema 迁移**写回，新增工单模板不预写（避免全空字段膨胀）。`worktree_path`/`worktree_branch`/`sub_worktrees` 保留为**兼容旧字段**（阶段演进见 §3.7 与 §3.9）。

---

## 3.5 生命周期数据模型（迁移 / 关闭 / 重开公共真源）

**核心不变量 I-1：单活动实现根**——任意稳定时刻，一个工单的活动 checkout 数量**必须等于 1 或 0**（0 仅限原地工单或已 close）。禁止同时存在两个 `state=active` 的 checkout。

**例外**只允许出现在受控迁移事务中，此时必须满足：

```text
旧 checkout state == active
新 checkout state == preparing
```

新 checkout 完成全部校验前，旧 checkout 始终是唯一活动根。**禁止**把两者同时标记为 `active`。

**核心不变量 I-2：权威字段唯一**——所有步骤定位代码实施位置时只能读取统一标准字段 `active_checkout`，不得再引入 `active_implementation_root`、`latest_worktree`、`current_code_root` 等语义相近的临时字段（反偷懒 #32 依据，见 [anti_laziness.md](../references/anti_laziness.md)）。

**核心不变量 I-3：工单身份不随 checkout 迁移**——以下身份在迁移前后保持不变：

- `ticket_id`；
- 工单产物历史；
- `requirement` 与需求摘要；
- `patch_count` 与 `patch_history`；
- verdict 字段族；
- 决策锚点。

迁移是**同一工单**的物理 checkout 变更，不是新建工单。

**核心不变量 I-4：迁移提交点唯一**——只有新 super checkout、所有受影响子仓 checkout、产物访问、分支、基线和工作区状态**全部通过校验后**，才能一次性切换活动指针。

- 在切换前失败：回滚新 checkout，旧 checkout 继续有效；
- 在切换后中断：通过迁移日志恢复清理，**不得**重新把旧 checkout 自动设为活动。

**核心不变量 I-5：破坏性清理后置**——任何旧 checkout 的 remove、旧分支删除或本地中间文件丢弃，都只能发生在：

1. 新活动根已校验；
2. metadata 与全局索引已原子更新；
3. 旧根无唯一未提交代码；
4. ICode 产物已有有效保留位置；
5. 用户已明确授权对应清理动作。

不得自动使用 `git worktree remove --force`、`git branch -D`、`git reset --hard` 或等价破坏性命令。

### 3.5.1 路径职责分离

| 概念 | 字段 | 含义 | 使用场景 |
|---|---|---|---|
| 工单产物根 | `artifact_root` | `.ico_metadata.json` 和各步骤产物的权威存储位置 | **读写 ICode 产物**（plan/review/audit 产物、归档） |
| 活动实现根 | `active_checkout` | 当前允许修改、编译、测试和提交代码的**唯一** checkout | **修改、编译和检查代码**（code/patch/deepcheck） |
| checkout 历史 | `checkout_history` | 曾服务于本工单的 checkout 及其迁移、关闭状态 | **清理、审计和追溯**（close/reopen/status） |

不要求三者永远物理相同。若不同，必须明确记录关系（写 `artifact_root`/`checkout_history`），且所有步骤按职责选择路径——**禁止**把产物读写和代码修改混用同一路径。

### 3.5.2 `active_checkout` 对象

```json
{
  "path": "<checkout 绝对路径>",
  "branch": "icode/<ticket-slug>",
  "base_ref": "refs/remotes/origin/<目标基线>",
  "base_commit": "<基线 commit>",
  "activated_at": "<运行时时间戳>",
  "state": "active"
}
```

- 新建 worktree 工单首次落盘 metadata 时构造（`path` = worktree 路径、`branch` = worktree_branch、`base_ref`/`base_commit` = 创建时的上游基线）；原地工单 `active_checkout` 保持 `null`（代码位置 = 当前工程 checkout，无需显式建模）
- 迁移完成后由新 checkout 替换；close 后置为 `submitted` 语义（见 §3.6）或随清理移除

### 3.5.3 `checkout_history` 元素

```json
{
  "path": "<checkout 绝对路径>",
  "branch": "icode/<ticket-slug>",
  "base_commit": "<基线 commit>",
  "state": "superseded | submitted | removed | abandoned",
  "superseded_at": "<运行时时间戳>",
  "removed_at": null
}
```

`superseded_at`/`removed_at` 在对应动作发生时写，未发生时保持 `null`。`active_checkout` 本身不重复出现在 `checkout_history`（历史只记录**已结束职责**的 checkout）。

### 3.5.4 `migration` 事务对象

```json
{
  "id": "<迁移 id，如 migrate-<工单>-<序号>>",
  "state": "preparing | switching | committed | cleanup_pending | done | failed",
  "from_checkout": "<旧 checkout 绝对路径>",
  "to_checkout": "<新 checkout 绝对路径>",
  "target_ref": "refs/remotes/origin/<目标基线>",
  "started_at": "<运行时时间戳>",
  "last_completed_phase": "<已完成阶段名>",
  "subrepo_results": [],
  "error": null
}
```

该记录必须支持进程中断后的幂等恢复（见 [steps/worktree.md](../steps/worktree.md)「幂等性」）。`migration.state` 生命周期：`preparing → switching → committed → cleanup_pending → done`；任一步失败置 `failed`（旧活动根保持有效）。

### 3.5.5 `submission_contracts` 提交契约（G1-G4 公共真源）

**为什么需要**：历史事故证明「基于主仓 `@{u}` 创建分支 + 自动 tracking」≈「提交目标明确」是错误假设——ticket 分支可能无 upstream、`@{u}` 指向可能与主仓不一致、同一 remote 上目标分支与 ticket 同名分支可能被误建、子仓 detached HEAD 时 ticket 分支指针与实际工作提交脱节。提交目标必须**逐仓显式冻结**并在创建/执行前/交付前/close 四道机器闸门校验，ICode 仍不替用户 commit/push，只负责冻结、验证与清楚回显。

**适用对象**：super repo 与每个产生代码/文档改动的业务子仓**一视同仁**（含只改文档不改代码的 super repo——文档提交同样需要契约）。只读工单（明确不改任何仓库）可不写契约，`submission_contracts` 留 `[]`。

**元素结构**（提案 §5 落地，字段名与语义对齐提案）：

```json
{
  "repo_role": "super | sub",
  "repo_path": "<checkout 绝对路径>",
  "source_repo_path": "<主工作区对应仓库根绝对路径>",
  "worktree_branch": "icode/<ticket-slug>[-<子仓slug>]",
  "source_branch": "<主工作区当前分支，如 master>",
  "source_upstream": "refs/remotes/<remote>/<分支>",
  "remote_name": "<remote 名，如 origin>",
  "remote_url": "<规范化 URL>",
  "target_remote_ref": "refs/remotes/<remote>/<目标分支>",
  "target_push_ref": "refs/heads/<目标分支>",
  "target_commit_at_create": "<创建时目标 commit>",
  "push_refspec": "HEAD:refs/heads/<目标分支>",
  "tracking_verified": true,
  "created_at": "<创建时间戳>"
}
```

- **`target_remote_ref` vs `target_push_ref` 分字段，不得混用**：`target_remote_ref`（远程跟踪 ref）用于 fetch / 包含性比较 / upstream 比对；`target_push_ref`（远端分支 ref）用于回显精确 push 命令 `git push <remote> HEAD:refs/heads/<目标分支>`。
- **`remote_url` 规范化**：去除无关尾斜杠；按项目策略将等价 SSH/HTTPS 地址归一后比较（`git remote get-url` 输出先归一；归一实现见 [scripts/submission_guard.py](../scripts/submission_guard.py) `normalize-url`——去空白/尾斜杠/`.git` 后缀，SSH scp-like `git@host:path` 与 `ssh://user@host/path`/HTTPS 等价归一）。**不能只比 remote 名称**（多 remote 指向同一服务器但分支不同时，必须 remote URL + target ref 双字段识别）。
- **`tracking_verified`**：G1 创建后完成逐项比对才置 `true`；比对失败或未执行保持 `false`——`false` 时 G2 按 L1 阻断（见 §3.8 ⑩）。
- **`worktree_branch` = 当前 checkout 实际分支**，须与 `active_checkout.branch` / `sub_worktrees[].branch` 一致；`repo_path` = 对应 checkout 路径。

**写入时机**：
- **G1 创建**（新建 worktree 工单 §1「② 创建」+ 子仓隔离「⑤」）：逐仓冻结契约；只读工单跳过
- **G1 迁移**（`/icode worktree --update`）：迁移前冻结新目标，迁移后逐仓重建契约并比对（见 [steps/worktree.md](../steps/worktree.md)）
- **reopen**：默认复用已提交契约（不临时推断；显式 `--to-ref` 才更新契约，见 [steps/reopen.md](../steps/reopen.md)）
- **close（G4）**：审计后更新 `submission_audit`，写 `submitted_baselines`，不删除契约（契约是工单提交历史的一部分）

**兼容（旧工单无契约）**：按 §3.7 从 `active_checkout`/`sub_worktrees`/主工作区 repo 推导候选契约；**无 upstream / 多候选 remote / detached HEAD / 分支漂移任一时标 `needs_user_confirm`，不得自动选**；候选唯一且机器证据完整才允许一次性写入 `migration_source="legacy_inference"`——迁移器机器实现见 [scripts/submission_guard.py](../scripts/submission_guard.py) `migrate-legacy`（写前自动备份原 metadata、写后立即跑 G2、未通过则回滚写入、已有契约幂等跳过；`needs_user_confirm` 以退出码 2 + reason 报告，不写契约）。

**`submission_audit` 结构**：

```json
{
  "last_checked_at": "<最近一次 G2/G3/G4 时间戳>",
  "verdict": "pass | behind | blocked | unknown",
  "repos": ["<已核验仓库 repo_role:repo_path>"]
}
```

`verdict` 仅缓存展示，**判定永远实时重跑**；任一仓库 blocked → 总 verdict 为 `blocked`。

**核心不变量 I-6：提交目标唯一且冻结**——任意工作时刻，每个有改动的仓库的提交目标只能来自 `submission_contracts` 中该仓库的契约；执行前/交付前/close 不得临时猜测或改用当前环境推断的目标。

---

## 3.6 checkout 状态词表

**固定词表，禁止自由文本表达状态**（与 `status` 词表同样的强制约束；词表外值 = 不合规）：

| 状态 | 语义 |
|---|---|
| `preparing` | 已创建但尚未获得活动权（迁移中间态） |
| `active` | 唯一允许实施代码的 checkout |
| `superseded` | 已被替代，等待安全清理 |
| `submitted` | 对应修改已进入用户指定的权威分支（close 后） |
| `removed` | checkout 已移除，仅保留历史记录 |
| `abandoned` | 迁移失败后废弃，未成为活动根 |

**映射关系**：
- `active_checkout.state` 恒为 `active`（或迁移中间态 `preparing`，见 §3.5 例外）
- `checkout_history[].state` 取值 `superseded` / `submitted` / `removed` / `abandoned`
- 旧字段兼容映射（内存推导）：`worktree_path` 非 null 且无新字段 → 视为有一个 `active` checkout；回流 remove 后 → 视同 `removed`

**三处状态词表命令必须同步**：本表 / `SKILL.md`「目录管理·生命周期」段 / `steps/status.md` 拓扑摘要段。改词表时三处同改。

---

## 3.7 旧字段兼容推导（只读内存推导，不写回）

旧工单缺新字段时，按以下规则**在内存中推导**（所有只读命令/检查器用推导值，**不落盘**）：

| 新字段 | 推导规则 |
|---|---|
| `artifact_root` | 当前 `.ico_metadata.json` 所在工单目录 |
| `active_checkout` | `worktree_path` 非 null 且路径有效 → 从它构造 `{path, branch=worktree_branch, state="active"}`；否则用当前工程 checkout（原地工单可视为当前 checkout，仅本地推导） |
| `checkout_history` | 用旧 `worktree_path` 初始化一条 `{path, branch, state="removed"(若已删除)/"active"(本地推导)}` |
| `migration` | `null` |
| `submitted_baseline` | `null` |
| `submitted_baselines` | `submitted_baseline` 非 null → 构造一条 `{repo_path: <active_checkout 推导路径>, target_remote_ref: <当前 upstream 推导>, commit: <submitted_baseline>}`；否则 `[]` |
| `submission_contracts` | 旧工单无此字段 → 从 `active_checkout`/`sub_worktrees`/对应主工作区 repo 推导候选契约（规则见 §3.5.5「兼容」）；**推导值仅作 G2/G3/G4 只读校验基线，不落盘**；无 upstream / 多候选 remote / detached / 分支漂移任一 → 候选标 `needs_user_confirm`，不得自动选 |
| `submission_audit` | `null`（无审计缓存） |

**写回条件**：只有执行明确的**迁移 / close / reopen / schema 迁移**操作时才写回新字段（含旧工单契约的 `migration_source="legacy_inference"` 一次性迁移——执行 [scripts/submission_guard.py](../scripts/submission_guard.py) `migrate-legacy`）。**禁止**只读命令（status/list/检索注入）因推导而意外改旧工单 metadata。字段缺失不能成为工单无法续跑的原因（推导保证向后兼容，见优化需求 §10.1）。

**旧字段演进策略**（对应优化需求 §10.2，分阶段）：
1. **第一阶段**：新增标准字段（`artifact_root`/`active_checkout`/`checkout_history`/`migration`/`submitted_baseline`/`submitted_baselines`/`submission_contracts`/`submission_audit`），保留并双读旧字段（`worktree_path`/`worktree_branch`/`sub_worktrees`）
2. **第二阶段**：写新字段，同时按兼容规则维护旧字段（迁移/close/reopen 写新字段时同步维护旧字段指向）
3. **第三阶段**：所有步骤改为只依赖新字段，旧字段仅用于迁移
4. **后续大版本**：评估是否移除旧字段

同一阶段内必须明确真源。**禁止**某些步骤读 `worktree_path`、另一些步骤读新的 `active_checkout` 却没有一致性校验——本文件的「§3.8 统一拓扑门禁」第⑧步（metadata/index 一致性）就是该校验的机器落点。

---

## 3.8 统一拓扑门禁（共享检查器）

**以下入口进入实际工作前必须调用同一个共享检查器，禁止各自微改或绕过**：
`/icode code` / `/icode patch` / `/icode deepcheck` / `/icode audit` / `/icode readme` / `/icode status --validate` / 带部署或实机测试的 patch 分支（`--listen`/`--test`）。

**检查内容**（伪代码，各步骤入口引用本段；重复执行迁移命令时跳过第 5 步或按 [steps/worktree.md](../steps/worktree.md)「幂等性」处理）：

```text
输入：ICODE_OUT_DIR / metadata / cwd
① 读 metadata，按 §3.7 内存推导 artifact_root / active_checkout / checkout_history / migration
② artifact_root：test -d 存在 且 含 .ico_metadata.json
③ active_checkout（非 null 时）：test -d {active_checkout.path} 且为 git checkout（.git 为目录或普通文件）
   + git -C {path} rev-parse --abbrev-ref HEAD == active_checkout.branch
   + git -C {path} rev-parse --verify HEAD 可解析
④ 单活动根：checkout_history 中 state=active 的元素数 + (active_checkout 非 null ? 1 : 0) == 1
   ——双活动根（两个 active）属 L1 阻断，禁止自动选择"较新的那个"
⑤ 无未完成迁移：migration 为 null，或 state ∈ {done, failed(已报告且用户裁决)}；进行中迁移 → 本入口是否允许恢复（patch/status 可恢复，code/audit 阻断）
⑥ 子仓拓扑：sub_worktrees 每项的 worktree_path 都位于 active_checkout 拓扑内（super-worktree 内同名相对路径）
   ——子仓隔离路径逃逸到原工程 = 未隔离即改子仓，L1 阻断
⑦ cwd 相符：worktree 工单（active_checkout 非 null）实际 cwd 必须在 active_checkout 内（cwd 契约的机器校验延伸）
⑧ 一致性：metadata.active_checkout 与全局 index 对应条目对活动 checkout 的记录一致（不一致 → repairable，仅可执行无歧义、可逆、幂等的 metadata 修复）
⑨ 来源约束：编译/测试/部署记录不得引用 superseded checkout（构建目录、二进制路径）
⑩ 提交契约（G2，仅当 submission_contracts 非空；只读工单无契约 → 跳过本项）——对**每个契约仓库**（super + 子仓一视同仁）逐一校验；机器实现见 [scripts/submission_guard.py](../scripts/submission_guard.py) `g2-check`（退出码 0=pass / 2=blocked）：
   - 当前分支非 detached（`git -C {repo_path} rev-parse --abbrev-ref HEAD` ≠ `HEAD`）
   - 当前分支 == contract.worktree_branch
   - `@{u}` 可解析（非空）且 == contract.target_remote_ref（upstream drift）
   - 规范化 remote URL == contract.remote_url（不能只比 remote 名称；等价 SSH/HTTPS 归一后比较）
   - HEAD 与 target ref 均可解析（target ref 不可解析 → 须先 `git fetch`）
   - 仓库在 submission_contracts 中已登记（`git -C {repo_path} status --porcelain` 有改动或 HEAD 有本工单 ticket commit 的仓库必须出现在契约里——**未登记仓库 = 提交目标未冻结，L1**；此项依赖需求改动清单 `code_files`，由**调用入口（04_code/拓扑检查器）结合 code_files 判定**，脚本 `g2-check` 负责契约内逐项校验）
   - `tracking_verified == true`（G1 未通过逐项比对时不得进入 code）
   任一 detached / missing upstream / upstream drift / remote mismatch / 未登记仓库 / tracking_verified=false → L1，**不得自动猜测并修正目标**；只有可由冻结契约**无歧义恢复**的 tracking 丢失才标 `repairable`，修复后重跑本闸门

判定：
- 全部通过 → verdict = pass，允许继续
- 仅存在无歧义、可逆、幂等的 metadata 修复（缺字段可推导、单字段笔误、可由契约无歧义恢复的 tracking 丢失）→ verdict = repairable，仅执行修复动作后继续
- 双 active / 迁移中断且不可恢复 / 子仓逃逸 / cwd 不符 / 来源引用 superseded / 契约违约（detached / upstream drift / remote mismatch / 未登记仓库）→ verdict = blocked，停止实际修改、编译、部署或清理，输出问题清单要求用户裁决
```

**不得把拓扑冲突只记成 L3 警告后继续编码**：存在两个活动实现根 = **L1 阻断**（见 SKILL.md「强制阻断边界矩阵」）。

---

## 3.9 生命周期命令入口

| 命令 | 语义 | 真源 |
|---|---|---|
| `/icode worktree --update [--to-ref <ref>]` | 受控迁移：把活动实现根从旧 checkout 迁移到基于最新/指定基线的**新** checkout（11 阶段状态机 + 中断恢复 + 幂等） | [steps/worktree.md](../steps/worktree.md) |
| `/icode worktree --close` | 用户已完成提交后的本地收敛：核验在线证据 → 安全关闭 checkout（不替用户 commit/push） | [steps/close.md](../steps/close.md) |
| `/icode worktree --reopen [--to-ref <ref>]` | 完成态工单补充修改的显式恢复：在最新在线基线上追加一代 checkout（不新建 ticket、不清 patch 历史） | [steps/reopen.md](../steps/reopen.md) |
| `/icode worktree --submit-check` | **交付前提交契约检查（G3）**：逐仓枚举提交目标与精确 push 命令，只读输出、不执行任何 push（见 §3.10） | [steps/worktree.md](../steps/worktree.md) |

- 迁移不得继续隐藏在 `/icode patch` 的临时操作中——换基线必须走 `/icode worktree --update`
- `completed` 但**未 close** 的工单：`patch` 可在当前唯一活动根继续（现有行为）
- **已 close** 的工单：必须先 `/icode worktree --reopen` 再 patch（禁止偷偷复活旧目录）

---

## 3.10 G3 / G4 提交闸门（交付前与 close 公共真源）

四道机器闸门中 G1（创建，§1）与 G2（执行前，§3.8 ⑩）已定义；G3（交付前）/ G4（close 后）定义如下，`/icode worktree --submit-check`、`/icode audit` 末尾、`/icode worktree --close` 统一引用本段，**禁止各自微改或绕过**。

### G3 交付前 submit-check（只读，不执行任何 push）

`/icode worktree --submit-check` 与 `/icode audit` 末尾都运行同一检查；机器实现见 [scripts/submission_guard.py](../scripts/submission_guard.py) `submit-check`（逐仓表格 + 精确 push 命令 + behind 判定，退出码 0=pass 或 behind（提示性，先 fetch/merge/rebase）/ 2=blocked）。输出逐仓表格：

| Repo | Branch | Upstream | Remote URL | Target(remote branch) | Ahead/Behind | Dirty | Verdict |
|---|---|---|---|---|---|---|---|

规则：
1. **枚举 super repo 与全部 `submission_contracts` 子仓，不能只枚举 `code_files`**——super repo 的文档提交必须进入清单（提案 §7 目标不变量 8：不能只检查业务代码仓漏掉 super 文档提交）
2. 对**有变更或含本工单 ticket commit** 的仓库，显示精确安全命令：`git push <remote_name> HEAD:refs/heads/<target-branch>`（`target-branch` 来自契约 `target_push_ref` 的远端分支部分）
3. **upstream 未经契约验证时（`tracking_verified=false` 或 G2 ⑩ 未过）不给出普通 `git push` 指令**——只提示先跑 G2 修复或由用户显式确认目标
4. target 比本地**前进**（`git rev-list --count <本地 ticket 分支 HEAD>..<target_remote_ref>` > 0 = target 领先本地 → 本地落后）→ 提示先 fetch/merge/rebase，**由用户决定，ICode 不自动改历史**；禁止把旧基线误报为可直接 push。**判定前先 `git fetch <remote> <target-branch>` 取在线状态**（防本地 fetch 过时误报 +0/-0，与 G4 规则 1「不用本地缓存 ref」一致）；fetch 失败（远程不可达）→ 降级本地缓存 ref，Target 列标注 `(本地缓存)`
5. **明确显示 "remote server"（Remote URL）与 "remote branch"（Target）两列**，避免「同一服务器 = 同名远端分支」的自然语言歧义
6. 任一仓库 L1（detached / 缺 upstream / upstream drift / remote mismatch / 未登记 / tracking_verified=false）→ **总 verdict = blocked**，不宣称"可以提交"
7. ICode 红线不变：只检查与回显指引，**不 commit / 不 push / 不 reset --hard / 不 push --force**

### G4 push 后 / close 闸门

用户声明已 push 后，`/icode worktree --close` 对**每个契约仓库**逐一执行（super + 子仓）：

1. `git fetch <remote_name>` 或 `git ls-remote <remote_url> <target_push_ref>` 获取**在线目标 SHA**（不用本地缓存 ref，防本地 fetch 过时）
2. 验证本工单提交均可从**精确 `target_remote_ref`** 到达：`git merge-base --is-ancestor <ticket 分支 HEAD> <target_remote_ref>`（exit 0 = 可达）；不可达 → 报告差异并让用户确认实际落地 commit，**不得跳过**
3. 验证没有未提交唯一修改和未推送 ticket commit（`git status --porcelain` + `git log origin..ticket` 检查）
4. 检查 remote 上是否出现**本工单同名意外分支**（`git ls-remote <remote_url> refs/heads/icode/<ticket-slug>*` 与本工单契约目标分支不同者）→ 发现则**报告但不自动删除**（历史事故：ticket 分支无 upstream 被误推到新建同名远端分支）
5. 所有仓库通过后才允许 close 清理 worktree；逐仓写 `submitted_baselines`（`{repo_path, target_remote_ref, commit}`），super 仓库 commit 同步写 `submitted_baseline`（兼容旧字段）

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
# ⚠️ 若有业务子仓隔离（metadata.sub_worktrees 非空）：子仓改动先 commit + merge 回原子仓，再逐个 remove 子仓 worktree
for sub in metadata.sub_worktrees:               # 每个子仓隔离 checkout
  (在子仓隔离 checkout 内) git add -A && git commit -m "<子仓改动>"   # 用户执行
  (在主工作区对应原子仓) git merge icode/<ticket-slug>-<子仓slug>
  git -C "<原子仓路径>" worktree remove "<子仓隔离路径>"   # ⚠️ 先 remove 子仓 再 remove super-worktree
  git -C "<原子仓路径>" branch -d icode/<ticket-slug>-<子仓slug>
git worktree remove ../<repo>-wt-<ticket-slug>   # ⚠️ 顺序：先 remove 再 branch -d
git branch -d icode/<ticket-slug>                # worktree 已删、分支未被占用才可删

# 方案 ②（不提交：手动带出改动）
# 在主工作区手动复制/应用 worktree 内改动文件 → 确认已带出后
git worktree remove --force ../<repo>-wt-<ticket-slug>   # --force 仅限改动已另行保存后，用户自行斟酌
```

- **顺序陷阱（两重保护）**：`git branch -d` 有两道检查——① 分支仍 checkout 于 worktree 时被拒 → **先 `worktree remove` 再 `branch -d`**；② 分支未完全合并时被拒（`没有完全合并`）→ 方案① merge 后自然满足；只 commit 不 merge（想暂留分支）则 branch -d 被拒是 git 正常保护——保留分支等以后合并，或用户自行 `git branch -D`（icode 不执行 `-D`）
- **执行位置（git 2.34.1 实测，非"失败未生效"）**：`git worktree remove` **必须在主工作区执行**——在 worktree 内对自身执行 remove 会**成功删除目录**（exit 0、无报错，git 不阻止），删除后当前 shell 目录悬空、后续命令报「不能读取当前工作目录」；**切勿把"删空后报错"误认为"remove 失败、目录未受影响"**（错误认识会误导用户原地重试，实际目录已删）。`git branch -d` 的失败与 cwd **无关**：分支仍被某 worktree 检出于任意位置都报「error: 无法删除检出于 '<worktree>' 的分支」（顺序陷阱①的检出保护），先 remove 再 branch -d 即解。回流命令前先确认在主工作区执行
- **严禁**未处理改动就 remove（会失败——失败是保护，绝不由 icode 自动 `--force`）
- **回流前产物留档（自动归档）**：07_readme 交付报告与产物都在 worktree 内，remove 后随之消失——**06_audit 终审已完成自动归档**（见下方「产物归档」），remove 前无需人工复制；若工单未走 06_audit 而直接 remove，需留档仍须人工复制出 worktree 再 remove
- **改动涉及 submodule**：submodule 内改动需**在 worktree 内 submodule 里单独 commit**（主仓 `git add -A && git commit` 只更新 gitlink，不带 submodule 内部改动）
- **业务子仓隔离回流（repo 工程，非 submodule）**：子仓隔离 checkout 在 super-worktree 内，remove super-worktree 会连子仓 checkout 一并消失——子仓改动须**先在里面 commit + merge 回原子仓**（见方案①循环），再 remove；勿直接 remove 把未回流子仓改动丢掉。子仓改动不随 super-worktree 产物归档（已 merge 回原子仓即持久）
- 未完成工单：worktree 保留，`git worktree list` 可随时看到，`cd` 回去续跑

### 产物归档（自动，防 worktree remove 丢档）

**目的**：worktree 工单的 `.icode_output_N/` 全在 worktree 内，`git worktree remove` 后随 worktree 消失（全局索引仅留摘要，完整 ADR/根因/交付报告丢失，复用价值打折）。归档把**核心产物**复制到 remove 不丢的位置，供后续检索复用完整结论。

- **触发时机**：`06_audit` 终审标记 `status=completed` 时，若 `metadata.active_checkout` 非 null（缺失按 §3.7 用 `worktree_path` 推导）→ 自动归档（remove 前归档已完成，remove 是用户回流手动步）。原地工单不触发（产物本在主仓，不丢）。
- **归档目标**：`~/.claude/icode_data/worktree_archive/<project_id>/<ticket_id>/`（与全局索引同层，天然不随 worktree 走；独立目录不污染 project_docs/module_docs；`ticket_id` 唯一防冲突）
- **归档内容**（核心高价值产物，`cp` 只复制存在的）：`.ico_metadata.json` + `00_init.md` + `01_plan.md` + `03_plan_final.md` + `log_analysis.md`。**不归档**：中间审查 JSON（`review_round_*.json`）、`tb_source/` 等大目录、临时文件。
- **归档命令**：
  ```bash
  ARCHIVE_DIR="$HOME/.claude/icode_data/worktree_archive/<project_id>/<ticket_id>"
  mkdir -p "$ARCHIVE_DIR"
  cp "$ICODE_OUT_DIR/.ico_metadata.json" "$ICODE_OUT_DIR/00_init.md" "$ICODE_OUT_DIR/01_plan.md" "$ICODE_OUT_DIR/03_plan_final.md" "$ICODE_OUT_DIR/log_analysis.md" "$ARCHIVE_DIR/" 2>/dev/null
  ```
- **索引记录**：归档后写 `metadata.archive_path = "$ARCHIVE_DIR"`，并在刷新全局索引时同步写 index 条目 `archive_path`（metadata + index 同步，不得只写其一）。
- **检索回退（读档复用，archived 活跃态）**：`archive_path` 非 null 时该工单为 **archived 活跃历史工单**，不标 stale——后续检索命中时 `project_path` 已失效（worktree remove）但 `archive_path` 存在 → 从归档目录读 `01_plan.md`（ADR/风险）或 `log_analysis.md`（根因/结论）注入，走**历史参考**语义（作启发，未经当前代码实证，须 Grep/Read 验证，见 [dir_and_metadata.md](dir_and_metadata.md)「过时校验」校验方法第 1 步·归档工单分支），命中**正常续期 + 按 verdict 分流**，待遇与主仓工单一致（仅产物来源不同）。

---

## 5. 防误删护栏

1. worktree 创建/清理**必须用户确认**（写操作影响 `.git`）
2. **永不自动 remove** / **永不自动 `--force` remove**
3. 精确分支名 `icode/<ticket-slug>` 操作，不触碰其他工单的分支/worktree
4. 归属 checkout 溯源：判断工单归属 checkout 一律读 `active_checkout`（缺失按 §3.7 用 `worktree_path` 推导），不用猜测
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
7. **提交目标不冻结 = 误推风险**：worktree 分支必须满足「有唯一 upstream + upstream == 契约 `target_remote_ref` + remote URL 与主仓一致」；无 upstream 修改型工单创建时即 L1 阻断（G1），执行前 drift/detached 由 G2 ⑩ 拦截——**不得**因用户说"已提交"或"推过了"就跳过 `merge-base --is-ancestor` / `ls-remote` 证据校验（G3/G4）
8. **remote 名相同 ≠ 提交目标相同**：同一服务器上目标分支与 ticket 同名分支可能被误建——比较一律用「规范化 remote URL + target ref」双字段，不能只比 remote 名称；误建的同名远端分支由 G4 报告但不自动删除
