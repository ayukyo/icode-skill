# 步骤：worktree 迁移（/icode worktree --update）

**命令**: `/icode worktree --update [--to-ref <ref>]` / `/icode worktree --submit-check`
- 默认（无参）：目标基线 = 当前活动 checkout 所属仓库的远程跟踪最新（`@{u}` 的远程 ref，如 `refs/remotes/origin/master`）
- `--to-ref <ref>`：目标基线 = 用户显式指定的 ref（本地分支 / 远程分支 / commit）
- `--submit-check`：**交付前提交契约检查（G3）**——逐仓枚举提交目标与精确 push 命令，只读输出、不执行任何 push（见下「G3 交付前 submit-check」段）
**产出**: 工单 metadata 更新（`active_checkout`/`checkout_history`/`migration`/`sub_worktrees`/`submission_contracts`/`submission_audit`）+ 新建 checkout + 可选清理旧 checkout；不产出新的工单产物文件
**会话**: 主会话

> 迁移是**同一工单**的物理 checkout 变更，不是新建工单。工单身份（`ticket_id`/产物历史/`requirement`/`patch_count`/verdict 字段族/决策锚点）在迁移前后保持不变。

## 本步骤 L1/L2 检查项声明

| 级别 | 检查项 | 触发后行为 |
|---|---|---|
| **L1·致命** | 无最新工单目录（`.ico_output_N/` 不存在或 metadata 缺失） | 报错退出，提示先 `/icode init` / `/icode start` 创建工单 |
| **L1·致命** | 统一拓扑门禁 verdict=blocked（双活动实现根 / 子仓逃逸 / 未完成迁移 / cwd 不符） | 报错退出，输出冲突路径与各自 dirty/commit 情况（[references/worktree_isolation.md §3.8](../references/worktree_isolation.md)） |
| **L1·致命** | 迁移预检命中任一硬停条件（§2 停止条件，如双 checkout 各有不相同未提交修改 / 无法唯一识别当前活动根 / 子仓存在无法安全移植的唯一提交） | 报错退出并报告问题，**不得猜测处理** |
| **L1·致命** | 迁移事务正处于 `switching` 且 metadata/index 半提交（只写了一侧） | 按「失败恢复 §14.2」先恢复再继续，禁止直接新建 checkout |
| **L2·关键** | 目标 ref 在迁移期间又前进（commit 变化） | 重新解析 + 重新进行冲突/包含性校验，不得把分支名当稳定 commit |

## 定位

**何时需要迁移**（触发条件，任一）：
- 当前工单已启用 worktree，上游目标分支产生新提交，现 checkout 不适合直接继续
- 当前 checkout 基线错误，需要迁移到用户指定 ref
- 历史上已人工创建替代 checkout（`git worktree list` 可见同工单候选），需要纳入标准拓扑

**何时不需要**：worktree 分支已通过 `git pull` / `git merge <远程分支>` 跟上上游的（活动根未变，只是分支前进），无需迁移。

**换基线必须走本命令**，禁止靠临时字段（如自定义 `latest_worktree`/`current_code_root`）或人工约定静默改指针。权威字段唯一：定位代码实施位置一律读 `active_checkout`（[references/worktree_isolation.md §3.5](../references/worktree_isolation.md)）。

## 前置校验

1. 按 [references/dir_and_metadata.md「检测最新目录」段](../references/dir_and_metadata.md) 确定 `ICODE_OUT_DIR`
2. 调用统一拓扑门禁（[references/worktree_isolation.md §3.8](../references/worktree_isolation.md)），verdict=blocked 报错退出
3. 读 `active_checkout`（缺失按 §3.7 用 `worktree_path` 推导）——非 null 才可迁移；原地工单（无 worktree）报错提示「原地工单无 checkout 可迁移」

## §2 迁移前预检（只读，任一硬停条件命中即报错退出）

**必须只读确认**：
1. 当前工单身份和 `artifact_root`
2. 当前 `active_checkout` 的路径、分支、HEAD 和工作区状态
3. 全局索引是否指向同一工单
4. `git worktree list` 中是否已有同工单候选 checkout
5. 受影响业务子仓集合（读 `03_plan_final.md` code_files/§5 + `metadata.sub_worktrees` + 实际 diff 联合核对，见 §6「未涉及子仓」）
6. 每个子仓的当前分支、HEAD、dirty 状态和未推送提交
7. 目标 ref 是否存在及对应 commit（默认 → 解析远程 ref；`--to-ref` → 解析用户 ref）
8. 旧 checkout 是否包含未归档的唯一产物
9. 是否已有未完成迁移事务（`metadata.migration` 非 null 且 state ∉ {done, failed}）
10. **冻结新目标提交契约（G1 迁移）**：修改型工单（`submission_contracts` 非空）迁移前须先确认新目标可冻结契约——新目标 ref 的 upstream/remote URL/目标 commit 可解析；**detached / 无 upstream / remote URL 不可识别 → L1 阻断**（不静默基于本地 HEAD 迁移），迁移后逐仓重建契约并比对（见「执行流程」阶段 2/3）

**停止条件（命中任一即停止并报告，不猜测处理）**：
- 无法唯一识别当前活动根（双 active / 无 active）
- 两个 checkout 都有不相同的未提交修改
- 目标 ref 不明确（默认最新基线但远程 ref 不可解析）
- 某个子仓存在未提交或未推送的唯一提交，且无法安全移植
- 工单 metadata 与全局索引指向不同 ticket
- 已存在未知来源的 checkout 占用目标路径或分支

## 执行流程（11 阶段状态机）

**迁移事务初始化**：预检通过后写 `metadata.migration = {id: "migrate-<ticket_id>-<序号>", state: "preparing", from_checkout: <旧 active_checkout.path>, to_checkout: <目标新 checkout 路径>, target_ref: <目标 ref>, started_at: <date +%Y-%m-%dT%H:%M:%S>, last_completed_phase: "inspect", subrepo_results: [], error: null}`。**每完成一阶段更新 `last_completed_phase`**——这是中断恢复的锚点（幂等性见 §5）。

按顺序执行以下阶段，**任一阶段失败 → 置 `migration.state=failed` + `error`，旧活动根保持有效，不切换**：

| # | 阶段 | 动作 | 完成标记 |
|---|---|---|---|
| 1 | `inspect` | 形成旧拓扑快照（旧 checkout / 子仓 HEAD / dirty / 未推送提交清单）+ 迁移计划（§6 拓扑清单） | last_completed_phase=inspect |
| 2 | `prepare_super` | 基于目标 ref 创建新 super checkout：`git worktree add -b "icode/<ticket-slug>-<序号>" <新路径> <目标ref>`（自动 tracking），写 `metadata.migration.to_checkout`；新 checkout 状态为 `preparing`（**未获得活动权**）。**G1 契约重建**：显式 `git -C <新路径> branch --set-upstream-to=<remote>/<目标分支> icode/<ticket-slug>-<序号>` + 逐项比对（HEAD 可解析 / 当前分支 / `@{u}` == 新目标 ref / remote 与主仓一致），更新 `submission_contracts` 中 super 仓库契约项（新 `worktree_branch`/`target_remote_ref`/`target_push_ref`/`target_commit_at_create`/`tracking_verified`） | prepare_super |
| 3 | `prepare_subrepos` | 为受影响子仓创建对应隔离 checkout（命令同 [references/worktree_isolation.md §1「⑤ 业务子仓隔离」](../references/worktree_isolation.md)，基于子仓远程基线）；每个结果记入 `subrepo_results`。**G1 契约重建**：每个子仓同样显式 set-upstream-to + 比对，更新契约项（迁移不改子仓目标分支时仅重验 `tracking_verified`） | prepare_subrepos |
| 4 | `transfer_changes` | 按 §4 内容转移策略把旧 checkout 改动转移到新 checkout（明确 commit 移植 / 可审计补丁 / 仅产物则锚定 artifact_root / 两边各有独立改动则停止交用户） | transfer_changes |
| 5 | `verify_content` | 对比旧、新 checkout 的预期改动集合（`git diff --stat` + 逐文件核对），确认改动已完整转移 | verify_content |
| 6 | `verify_build_source` | 确认后续编译命令的源码路径全部位于新 checkout（构建目录/二进制路径不引用旧 checkout） | verify_build_source |
| 7 | `verify_artifacts` | 确认 `artifact_root` 可访问且不随旧 checkout 清理而丢失（产物不在旧 checkout 内，或已迁移/重新锚定） | verify_artifacts |
| 8 | `switch` | **原子切换**（§3）：metadata + 全局 index 同步更新，新 checkout 成为唯一 `active`；`migration.state=switching` | switch |
| 9 | `mark_old` | 旧 checkout 写入 `checkout_history` 置 `superseded`（`superseded_at=<ts>`，`removed_at=null`） | mark_old |
| 10 | `cleanup` | **经用户授权**后清理旧 checkout 和分支（`git worktree remove` → `git branch -d`，先子仓后 super；禁止默认 `--force`/`-D`，见 I-5） | cleanup_pending |
| 11 | `finalize` | 记录迁移结果，`migration.state=done`；清理后旧 checkout 的 `removed_at=<ts>` | done |

## §3 原子切换要求

`switch` 阶段采用「临时文件写入、校验、原子替换」同步更新：
- 工单 `.ico_metadata.json`
- 全局 `index.json` 对应条目
- 必要的决策锚点或迁移记录

若任一写入失败，**必须保持旧活动根有效**，`migration.state=failed`（可恢复失败态），不得留下"双活动"状态。切换后中断 → 通过迁移日志恢复清理，**不得重新把旧 checkout 自动设为活动**。

## §4 内容转移策略

ICode **不默认替用户 commit**。迁移时按以下策略判断：

| 旧 checkout 改动形态 | 处理 |
|---|---|
| 已提交改动 | 优先通过明确 commit 移植（新 checkout `git cherry-pick <commit>` 或 `git merge` 旧分支） |
| 未提交但可生成补丁 | 先保存可审计补丁（`git diff` > 补丁文件，记录到 `migration.subrepo_results`/迁移记录），再在新 checkout 应用 |
| 仅 ICode 产物 | 迁移或重新锚定 `artifact_root`，**不得当成业务代码移植** |
| 两边均有独立改动 | **停止自动迁移**，交由用户选择合并策略 |
| 用户明确表示已提交且本地中间代码可丢弃 | **仍需先核验**在线 commit 已包含预期改动，再允许清理 |

## §5 幂等性

重复执行迁移命令时，按 `migration.state` 分流（不重复建 checkout、不产生第三个 checkout）：
- `preparing`：从 `last_completed_phase` 继续
- `switching`：核对 metadata 与 index 后完成或回滚，不重复建 checkout
- `cleanup_pending`：只执行剩余安全清理
- `done`：报告已完成，不创建第三个 checkout
- `failed`：先报告失败原因和残留资源，由用户决定恢复或放弃

## §6 多业务子仓事务边界

**整体事务**：多子仓迁移不能把每个子仓视为互不相关的独立动作。迁移计划先形成拓扑清单（仓库角色 / 原 checkout / 目标 checkout / 原 HEAD / 目标基线 / dirty / 转移策略 / 状态，路径与 commit 用运行时实际值）。

**部分成功处理**：
- 任一子仓准备失败：**不切换活动根**
- 已创建的新 checkout 标为 `abandoned` 或保留在 `preparing` 以便恢复
- **不自动删除**含未提交修改的子仓 checkout
- 用户可选择重试、放弃新拓扑或手工解决冲突
- 只有所有必需仓库均通过校验后才整体提交活动根切换

**未涉及子仓**：未被本工单修改的子仓**不**为形式完整而创建独立 checkout。迁移集合必须来源于定稿计划、`code_files`、实际 diff 和当前 patch 记录的联合核对。

## 失败恢复（覆盖中断场景）

| 场景 | 恢复动作 |
|---|---|
| §14.1 创建新 checkout 后进程中断 | 旧根仍 `active`、新根 `preparing`；重跑复用已有新根不重复创建；可安全放弃时新根标 `abandoned` 后按授权清理 |
| §14.2 metadata 已切换、index 未切换 | 通过迁移事务 id 和临时写入记录识别半提交；校验新根后补写 index；新根无效则回滚 metadata 到旧根；**不允许两个文件各自长期保持不同真相** |
| §14.3 子仓部分迁移 | 不进入代码实施；输出成功/失败子仓清单；重试只补未完成项；全部完成后再切换 |
| §14.4 旧 checkout 含未提交修改 | 默认保留旧 checkout；报告差异、未跟踪文件和未推送提交；只有用户明确选择移植/保存/丢弃后才继续清理；「在线已有类似文件」不能替代逐项包含性验证 |
| §14.5 目标在线分支变化 | 关闭或迁移操作开始后目标 ref 又前进时，记录实际解析到的 commit；在提交活动切换前重新解析一次；commit 改变则重新进行必要的冲突和包含性校验；**不得把分支名当成稳定 commit 使用** |

## G3 交付前 submit-check（/icode worktree --submit-check）

交付前（audit 末尾同样嵌入）运行**只读**提交契约检查，输出逐仓表格（真源见 [references/worktree_isolation.md §3.10](../references/worktree_isolation.md)）：

| Repo | Branch | Upstream | Remote URL | Target(remote branch) | Ahead/Behind | Dirty | Verdict |
|---|---|---|---|---|---|---|---|

规则要点：
1. **枚举 super repo + 全部 `submission_contracts` 子仓**（不能只枚举 `code_files`——super 文档提交必须进清单）
2. 有变更或含本工单 ticket commit 的仓库 → 显示精确安全命令 `git push <remote_name> HEAD:refs/heads/<target-branch>`（target 来自契约 `target_push_ref`）
3. upstream 未经契约验证（`tracking_verified=false` / G2 ⑩ 未过）→ **不给出普通 `git push` 指令**，提示先修复或由用户显式确认目标
4. target 比本地前进 → 提示先 fetch/merge/rebase，**由用户决定，ICode 不自动改历史**；判定前先 `git fetch <remote> <target-branch>` 取在线状态（防本地 fetch 过时误报，与 G4 规则 1 一致），fetch 失败降级本地 ref 并标注 `(本地缓存)`；存在落后仓库时总 verdict 显示 `behind`（⚠️，rc=0，先 fetch/merge/rebase）
5. 明确显示 "remote server"（Remote URL）与 "remote branch"（Target）两列，避免「同一服务器 = 同名远端分支」歧义
6. 任一仓库 L1 → **总 verdict = blocked**，不宣称"可以提交"
7. ICode 红线不变：只检查与回显指引，**不 commit / 不 push**

## 反偷懒

- **禁止双活动根**：任何时刻只允许一个 `state=active`；迁移完成前新 checkout 恒为 `preparing`
- **禁止默认破坏性命令**：不得自动 `git worktree remove --force` / `git branch -D` / `git reset --hard`（I-5 破坏性清理后置）
- **禁止用临时字段改指针**：不引入 `active_implementation_root`/`latest_worktree`/`current_code_root` 等语义相近字段（I-2 权威字段唯一）
- **禁止迁移后不重建契约**：新 checkout 的 upstream 必须经 G1 契约重建与逐项比对（`tracking_verified=true`）才能获得活动权，**不得依赖隐式 tracking**（历史事故：ticket 分支无 upstream 被误推同名远端分支）
- **禁止跳过来源校验**：迁移未完成前不在新 checkout 修改/编译/部署并当活动实现证据
- **禁止伪造迁移结果**：`last_completed_phase`/`migration.state` 必须如实记录，未执行的阶段不得标记完成
- **禁止真实项目术语**：本步骤输出、示例、迁移日志一律使用通用占位符（路径/分支/commit 用 `<...>` 或虚构值）

## MCP 推荐

迁移是结构化状态机执行（判断+git 操作），不需要 spawn 子代理；仅用 sequential-thinking（强制思考前置：现状拓扑 → 目标拓扑 → 转移策略 → 风险评估，≥4 步）。其余 MCP 不推荐。

**强制约束**：🟢/🟢*/⚪ 语义 + 双保险机制（执行步骤内嵌 + thinking_core gate）详见 [SKILL.md「MCP 调用覆盖强制化」](../SKILL.md) + [references/mcp_per_step.md「双保险机制」](../references/mcp_per_step.md)。
