# 步骤：提交后收敛（/icode worktree --close）

**命令**: `/icode worktree --close [--ticket <ticket_id>]`
- 默认（无参）：用户已自行完成 commit/push/merge 后，关闭本工单本地 checkout 并记录在线基线（close 本义即「用户已提交后的收敛」，无额外参数）
- `--ticket <ticket_id>`：**显式身份解析**（变更类命令推荐）——`python3 tools/icode_control.py resolve-ticket --ticket <ticket_id> --workspace <工程根>` 解析目标工单目录；命中多个候选 → **拒绝猜测报错退出**（exit 4，输出候选清单）；未命中 → 索引兜底 / path_gone 报告
**产出**: 工单 metadata 更新（`active_checkout` → `checkout_history` 置 `submitted`、`submitted_baseline`、`migration=null`、`close_state` 分阶段推进）+ 归档 manifest/事件留痕 + 安全清理 checkout；不产出新的业务实现文件
**会话**: 主会话

> close **不替用户 commit/push/merge**，只做「用户已提交后的本地收敛」——核验在线证据 → 状态收敛 → 安全清理。`/icode worktree --close` 之后工单仍可从归档正常检索和复用（归档产物不随 checkout 删除）。
>
> **关闭分阶段状态（vNext，schema v3）**：close 按 `close_state` 分阶段推进（`close_planned → archived → roots_verified → checkouts_removed → branches_removed → closed`，真源 [mcp/workflow-gate/gates.json](../mcp/workflow-gate/gates.json) `state_machine.close_phases`）。`close_planned/archived` 在源 `{ICODE_OUT_DIR}` 执行；`archived` 成功后命令返回 `control_root=<archive_path>`，**后续所有阶段必须改用该归档根作为 `--dir`**。这样 checkout 被 remove 后仍能留痕并续跑。legacy 工单只读；需先显式迁移至 vNext 才能启动 close，禁止直写 close_state。

## 本步骤 L1/L2 检查项声明

| 级别 | 检查项 | 触发后行为 |
|---|---|---|
| **L1·致命** | 无最新工单目录（`.ico_output_N/` 不存在或 metadata 缺失） | 报错退出，提示先 `/icode init` / `/icode start` 创建工单 |
| **L1·致命** | 统一拓扑门禁 verdict=blocked（双活动实现根 / 子仓逃逸 / 未完成迁移 / cwd 不符） | 报错退出（[references/worktree_isolation.md §3.8](../references/worktree_isolation.md)） |
| **L1·致命** | 前置证据不满足（§2 在线证据核验失败，如预期提交不可从**契约 `target_remote_ref`** 到达 / 活动 checkout 仍含未保存唯一修改 / 契约仓库未登记） | 报错退出，报告缺失证据，**用户说"已提交"不是跳过 git 证据校验的理由** |
| **L1·致命** | 产物源不完整，或 `archive_path` 不可写/无法迁移到保留根 | 报错退出；06_audit 只准备目标，close 的 `archived` 阶段负责复制与完整性验证 |
| **L2·关键** | 目标在线 ref 在 close 期间又前进（commit 变化） | 重新解析 + 重新校验包含性（§6），不得把分支名当稳定 commit |

## 定位

**何时 close**：worktree 工单的改动已由用户提交并进入权威分支（commit/push/merge 任一完成），本地 checkout 不再需要。close 把 `active_checkout` 收敛为 `submitted`，清理 checkout，记录 `submitted_baseline`，让工单进入「可检索复用的完成态」而不再占用活动实现根。

**何时不用 close**：
- 改动未提交 / 未进入权威分支 → 不 close（走回流方案①手动 commit+merge，见 [references/worktree_isolation.md §4](../references/worktree_isolation.md)）
- 原地工单（无 worktree）→ 无需 close（无 checkout 可收敛）
- 未完成工单（`status != completed`）→ 不 close

**close 后要补充修改**：先 `/icode worktree --reopen` 恢复活动 checkout，再 `/icode patch`（禁止复活旧目录）。

## 前置校验

1. **身份解析**：带 `--ticket` 时按命令行说明走 `resolve-ticket`（多义即拒绝）；无参时按 [references/dir_and_metadata.md「检测最新目录」段](../references/dir_and_metadata.md) 确定 `ICODE_OUT_DIR`
2. 调用统一拓扑门禁（§3.8），verdict=blocked 报错退出
3. 读 `active_checkout`（缺失按 §3.7 推导）：非 null 才可 close；原地工单直接提示「无 checkout 可收敛」
4. **已 closed 终态检查**：metadata `close_state == "closed"` → 只回放摘要（保留产物路径 / 逐仓基线 / 未清理资源），**不重复执行任何核验与清理动作**，正常退出

## §2 前置证据核验（close 前必须全部确认，G4）

1. 用户明确声明修改已提交或在线分支已更新（声明是**启动检查的授权**，不是跳过证据校验的理由）
2. **逐仓读取提交契约**（`submission_contracts`，缺失按 [references/worktree_isolation.md](../references/worktree_isolation.md) §3.7 推导，见 §3.5.5「兼容」）：close 的目标真源 = 每个契约仓库的 `target_remote_ref`——**不临时猜测**目标分支（G4 目标真源，见 [references/worktree_isolation.md](../references/worktree_isolation.md) §3.10）
3. **本工单预期提交确实可从精确 `target_remote_ref` 到达**（逐仓，super + 子仓一视同仁）：`git fetch <remote_name>` 或 `git ls-remote <remote_url> <target_push_ref>` 获取**在线目标 SHA**（不用本地缓存 ref 防过期）→ `git merge-base --is-ancestor <ticket 分支 HEAD> <target_remote_ref>`（退出码 0 = 可达；只读命令）。不可达 → 报告差异并让用户确认实际落地 commit（可选指定）；**不得跳过**
4. 活动 super checkout 与各子仓不存在未保存的唯一修改（`git status --porcelain` + 未推送提交检查）
5. ICode 归档源产物完整可读，`archive_path` 已指向可写的保留根；此时仅是归档准备，完整复制与 manifest/hash 验证在 `archived` 阶段执行
6. 全局 index 中工单身份与本地 metadata 一致
7. **检查 remote 上是否出现本工单同名意外分支**（`git ls-remote <remote_url> refs/heads/icode/<ticket-slug>*`，与本工单契约目标分支不同者）→ 发现则**报告但不自动删除**（历史事故：ticket 分支无 upstream 被误推到新建同名远端分支）

## 执行流程（分阶段推进，每阶段完成即 close-phase 留痕）

> **阶段语义表**（vNext 工单执行；每阶段完成即调 `close-phase` 记录，失败/中断重跑从 `close_state` 锚定续跑）：

| 阶段 | 动作（对应原步骤 1-11） | 完成即留痕 |
|---|---|---|
| `close_planned` | §2 前置证据核验全部通过 + **冻结拓扑快照**（active_checkout/sub_worktrees/分支 HEAD）+ **输出身份计划**（见下「§7 身份计划」） | ✓ |
| `archived` | 执行 [worktree_isolation.md「产物归档」](../references/worktree_isolation.md) 的小文件复制；再运行 `python3 tools/icode_control.py archive-manifest --dir {ICODE_OUT_DIR} --archive-dir <archive_path> --write`，要求必需控制文件齐全、源/归档 SHA-256 一致、三 linter 归档前后等价通过；最后在源根记录本阶段，控制面会把最新 metadata/事件链交接至归档根并返回 `control_root` | ✓ |
| `roots_verified` | **从本阶段起 `close-phase --dir <control_root>`**。逐仓校验在线目标 ref 包含预期提交（G4：§2 第 3 项，super + 每个契约子仓；任一仓库不可达 → 停止 close，报告差异）+ 检查意外同名远端分支（§2 第 7 项，发现**报告不自动删除**） | ✓ |
| `checkouts_removed` | 收敛 + 清理 checkout（原步骤 4-7+9，**经用户确认后**）：活动 checkout 置 `submitted`（`active_checkout` 移入 `checkout_history`，`active_checkout=null`）→ 记录 `submitted_baselines`（逐仓 `{repo_path, target_remote_ref, commit}`，super 兼容写 `submitted_baseline`）→ 清理子仓/super checkout + `git worktree prune` | ✓ |
| `branches_removed` | 删除分支（原步骤 8）：已合并分支仅用安全删除 `git branch -d`；被 Git 拒绝（未完全合并）时**保留分支并报告**，不自动 `-D` | ✓ |
| `closed` | 更新 metadata + index 最终态（原步骤 10-11）：`checkout_history` 已清理项 `removed_at=<ts>`、`submitted_baselines`、`submission_audit`（G4 审计结果）、`active_checkout=null`；输出保留产物路径 / 逐仓基线 / 意外远端分支报告 / 未清理资源清单 | ✓ |

> 注：`submitted_baselines`/`submission_audit`/`removed_at`/`active_checkout` 等非 status 字段也禁止直写，统一用 `metadata-update --set-json/--append-json --request-id <key>` 原子写回并留 `metadata_updated` 事件；`close_state` 本身由 `close-phase` 独占推进。`archived` 之后这些写回和最终 `index-write --ticket-dir <control_root>` 都必须针对归档根；索引 writer 保留原 `project_path/out_dir` 身份三元组。

> 注：清理业务子仓 checkout 前先确认已 merge 回原子仓；`git worktree remove` 含未提交改动时 remove 失败是保护，**禁止自动 `--force`**（I-5）。

## §7 身份计划（identity plan，close_planned 阶段输出）

close 开始（`close_planned`）时输出**可机读身份计划**，作为「预期关闭结果」锚，与最终 `closed` 状态比对：

```text
identity plan:
  ticket: <ticket_id>  source_out_dir: <ICODE_OUT_DIR>  status: completed
  active_checkout: <path> → 置 submitted（移入 checkout_history）
  per-repo: {repo_path, target_remote_ref, expected_commit, action: [置submitted + 清理checkout + 安全删分支]}
  cleanup: [<子仓隔离路径>, <super checkout 路径>]  branches: [<ticket-slug-* 分支>]
  control_root: <ICODE_OUT_DIR> → <archive_path>（archived 阶段交接）
```

若最终 `closed` 状态与身份计划不一致（如某分支被 Git 拒绝保留、某 checkout 未能清理）→ 留在输出报告的「未清理资源清单」，**不伪造已收敛**。

## 关闭后状态

close 完成后必须满足：

```text
active implementation checkout count == 0
ticket status == completed
submitted baselines (per-repo) complete == true   # 每个契约仓库都有 {repo_path, target_remote_ref, commit}
submitted baseline commit (super) != null         # 兼容旧字段
artifact archive exists == true
unresolved local changes == false
```

若用户希望后续继续维护同一工单 → 显式 `/icode worktree --reopen` 或 `/icode worktree --update` 创建新的活动 checkout，**不偷偷复活旧目录**。

## §6 目标在线分支变化

close 操作开始后目标 ref 又前进时：记录实际解析到的 commit；提交状态收敛前重新解析一次；commit 改变则重新进行必要的冲突和包含性校验；**不得把分支名当成稳定 commit 使用**。

## 幂等性

重复执行 close：
- **已 closed**（`close_state == "closed"`，或 legacy 判定：`submitted_baseline` 非 null / `submitted_baselines` 非空 且 `active_checkout` null）→ 只回放摘要（保留产物路径 / 逐仓基线 / 未清理资源清单），报告「本工单已关闭，基线 {commit}；如需恢复请 /icode worktree --reopen」，**不重复执行任何核验与清理动作**
- **分阶段幂等（vNext）**：`close-phase` 对当前阶段重放返回「已应用」摘要（不重复执行）；`archived` 重放会补做未完成的控制根交接自愈；之后从归档 `control_root` 的 `close_state` 锚定下一阶段，checkout 已 remove 也不影响续跑
- **禁止跨阶段跳转**：close-phase 收到非相邻阶段 → fail-closed 拒绝（门禁 `close_phase_order`）
- **并发保护**：close 属变更类命令，必须用显式身份解析（`--ticket`/`resolve-ticket`）+ `close-phase` 锁内写（`.icontrol.lock`），杜绝多会话重复清理

## 反偷懒

- **禁止跳过在线证据核验**：用户说"已提交"≠ 跳过 `merge-base --is-ancestor` 校验（防未提交就 close 丢改动）
- **禁止临时猜测 close 目标**：close 目标分支必须来自契约 `target_remote_ref`（G4 目标真源），不得用当前环境临时推断或猜分支名
- **禁止漏子仓**：close 证据核验必须逐仓（super + 每个契约子仓），不能只查 super repo 或 `code_files`
- **禁止默认破坏性命令**：不得自动 `git worktree remove --force` / `git branch -D` / `git reset --hard`（I-5）；清理动作须用户授权
- **禁止删未归档唯一产物**：产物未归档时拒绝 remove
- **禁止删未提交唯一代码**：存在未保存唯一修改时 close 停止，报告差异
- **禁止伪造关闭**：`submitted_baselines[].commit` 必须是实际核验的在线 commit，不是用户口头声称的分支名
- **禁止真实项目术语**：示例/输出用通用占位符

## MCP 推荐

close 为 **L1（短决策记录）**：结构化状态机执行（判断+git/归档操作），不需要 spawn 子代理，不调用 sequential-thinking；决策字段（证据核验 → 清理顺序 → 残留处理 → 风险评估）记入 `.decision_anchors.json`（见 [references/decision_anchors.md](../references/decision_anchors.md)「L1 决策记录契约」）。其余 MCP 不推荐。

**强制约束**：🟢/🟢*/⚪ 语义 + 双保险机制（执行步骤内嵌 + thinking_core gate）详见 [SKILL.md「MCP 调用覆盖强制化」](../SKILL.md) + [references/mcp_per_step.md「双保险机制」](../references/mcp_per_step.md)。
