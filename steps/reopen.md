# 步骤：显式恢复（/icode worktree --reopen）

**命令**: `/icode worktree --reopen [--to-ref <ref>]`
- 默认（无参）：在**最新在线基线**（远程跟踪 `@{u}` 的 ref）上创建新的活动 checkout
- `--to-ref <ref>`：在用户显式指定的 ref 上创建新的活动 checkout（可选扩展）
**产出**: 工单 metadata 更新（`active_checkout`/`checkout_history`/`sub_worktrees`/`migration=null`）+ 新建 checkout；不新建 ticket、不清空 patch 历史
**会话**: 主会话

## 本步骤 L1/L2 检查项声明

| 级别 | 检查项 | 触发后行为 |
|---|---|---|
| **L1·致命** | 无最新工单目录（`.ico_output_N/` 不存在或 metadata 缺失） | 报错退出，提示先 `/icode init` / `/icode start` 创建工单 |
| **L1·致命** | 工单**未 close**（`submitted_baseline` 为 null 或缺失） | 报错退出，提示：未 close 的工单直接 `/icode patch` 在当前活动根继续，或 `/icode worktree --update` 换基线——不需要 reopen |
| **L1·致命** | 统一拓扑门禁 verdict=blocked | 报错退出（[references/worktree_isolation.md §3.8](../references/worktree_isolation.md)） |
| **L1·致命** | 目标基线 ref 不可解析（默认最新基线但远程 ref 缺失 / 本地无该 ref） | 报错退出，提示先 `git fetch` 或改用显式 ref |
| **L2·关键** | 已存在未 close 的旧活动 checkout（close 后用户又手动复活了目录） | 警告 + 提示先 `/icode worktree --update` 收敛拓扑，不自动覆盖 |

## 定位

`status=completed` 且**已 close**（`submitted_baseline` 非 null）的工单，后续出现补充修改时，**必须显式 reopen**：在最新在线基线上创建新的活动 checkout。这是 close 后的唯一恢复通道，**禁止偷偷复活旧目录**（在已关闭的 checkout 上继续改 = 基线过时 + 拓扑违规）。

reopen 与相关命令的边界：
- **未 close 的 completed 工单**：`patch` 可直接在当前唯一活动根继续（[steps/08_patch.md](08_patch.md)「completed 工单分流」）
- **未 close 但基线过时**：走 `/icode worktree --update`（换基线），不是 reopen
- **已 close 且要恢复**：先 `/icode worktree --reopen`，再 `/icode patch` 在新 checkout 打补丁

## 前置校验

1. 按 [references/dir_and_metadata.md「检测最新目录」段](../references/dir_and_metadata.md) 确定 `ICODE_OUT_DIR`
2. 读 metadata：`status == "completed"` 且 `submitted_baseline` 非 null（已 close），否则按 L1 表报错
3. 调用统一拓扑门禁（§3.8），verdict=blocked 报错退出
4. 解析目标基线：默认 → `git rev-parse --symbolic-full-name @{u}` 的远程 ref（本地不可解析则报错）；`--to-ref <ref>`（可选扩展）→ 用户指定 ref

## 执行流程

1. **解析基线**：目标基线 ref + commit（`git rev-parse <ref>`）；记录实际解析到的 commit（分支名不当作稳定 commit）
2. **创建新 checkout**：`git worktree add -b "icode/<ticket-slug>-reopen-<N>" <新路径> <基线ref>`（`<N>` 为 reopen 代数序号，如已有 `-reopen-1` 则用 `-reopen-2`；基于远程基线创建自动 tracking，命令见 [references/worktree_isolation.md §1「② 创建」](../references/worktree_isolation.md)）
3. **受影响业务子仓**：若工单涉及业务子仓修改，为受影响子仓创建隔离 checkout（同 §1「⑤ 业务子仓隔离」，基于子仓远程基线），写 `metadata.sub_worktrees`
4. **校验新 checkout**：`git -C <新路径> rev-parse --verify HEAD` + 分支核对 + 基线 = 解析到的目标 commit
5. **原子切换**（临时文件写入 → 校验 → 原子替换）：
   - 旧 checkout（若有）写入 `checkout_history`（`state=submitted` 或 `removed`，依据旧目录是否还存在）
   - `active_checkout` = 新 checkout（`base_ref`/`base_commit` = 解析基线，`activated_at=<ts>`，`state=active`）
   - `migration` = null（reopen 不是迁移事务，是追加一代）
   - 同步更新全局 `index.json` 对应条目（metadata + index 同步，不得只写其一；写前重读合并契约见 [references/dir_and_metadata.md「全局索引写入」](../references/dir_and_metadata.md)）
6. **记录恢复原因**：把 reopen 触发背景（用户为什么恢复本工单、本次要补什么）追加到 `08_patch.md` 对应 Patch 段触发背景，或记入工单历史（`patch_history`/决策锚点 `patch_summary`）——**checkout_history 中本代 checkout 不携带原因，原因记入工单历史**
7. **输出确认**：`✅ 已 reopen {ticket_id}：新活动 checkout {路径}（分支 {分支}，基线 {commit 前 12 位}）；后续 /icode patch 将在此新 checkout 上进行`

## 幂等性

重复执行 reopen：检测到已存在 `active_checkout` 且状态 `active` → 报告「本工单已有活动 checkout，无需 reopen」（不创建第二个）；用户确需再换基线 → 先 `/icode worktree --update`。

## 反偷懒

- **禁止复活旧目录**：不在已 close 的旧 checkout 上继续 patch
- **禁止覆盖未确认状态**：close 后旧 checkout 目录若仍存在，reopen 不自动删除（留用户确认）；`checkout_history` 如实记录旧目录存在状态
- **禁止清空 patch 历史**：reopen 不重置 `patch_count`/`patch_history`（补丁历史是工单身份的一部分，见 I-3）
- **禁止真实项目术语**：示例/输出用通用占位符

## MCP 推荐

reopen 是结构化状态机执行，不需要 spawn 子代理；仅用 sequential-thinking（强制思考前置：恢复必要性 → 基线选择 → 子仓影响 → 风险，≥4 步）。其余 MCP 不推荐。

**强制约束**：🟢/🟢*/⚪ 语义 + 双保险机制（执行步骤内嵌 + thinking_core gate）详见 [SKILL.md「MCP 调用覆盖强制化」](../SKILL.md) + [references/mcp_per_step.md「双保险机制」](../references/mcp_per_step.md)。
