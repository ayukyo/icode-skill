# 步骤：显式恢复（/icode worktree --reopen）

**命令**: `/icode worktree --reopen [--ticket <ticket_id>] [--target <ref>]`
- 默认（无参）：在**最新在线基线**（远程跟踪 `@{u}` 的 ref）上创建新的活动 checkout
- `--target <ref>`：在用户显式指定的 ref 上创建新的活动 checkout（可选扩展）
**产出**: 归档控制根 metadata 原子更新（`active_checkout`/`checkout_history`/`sub_worktrees`/`submission_contracts`/`migration=null`/`close_state=null`）+ `ticket_reopened` 事件 + 新 checkout 内 `.icode_output/.active_ticket.json` 指针；不新建 ticket、不清空 patch 历史
**会话**: 主会话

## 本步骤 L1/L2 检查项声明

| 级别 | 检查项 | 触发后行为 |
|---|---|---|
| **L1·致命** | 未能通过 `--ticket`/全局索引唯一解析可读 `archive_path` | 报错退出；多义时要求显式指定 ticket，不猜测 |
| **L1·致命** | 工单**未 close**（`close_state != "closed"`） | 报错退出，提示：未 close 的工单直接 `/icode patch` 在当前活动根继续，或 `/icode worktree --update` 换基线——不需要 reopen |
| **L1·致命** | 统一拓扑门禁 verdict=blocked | 报错退出（[references/worktree_isolation.md §3.8](../references/worktree_isolation.md)） |
| **L1·致命** | 目标基线 ref 不可解析（默认最新基线但远程 ref 缺失 / 本地无该 ref） | 报错退出，提示先 `git fetch` 或改用显式 ref |
| **L2·关键** | 已存在未 close 的旧活动 checkout（close 后用户又手动复活了目录） | 警告 + 提示先 `/icode worktree --update` 收敛拓扑，不自动覆盖 |

## 定位

`status=completed` 且 `close_state="closed"` 的工单，后续出现补充修改时，**必须显式 reopen**：在最新在线基线上创建新的活动 checkout。这是 close 后的唯一恢复通道，**禁止偷偷复活旧目录**（在已关闭的 checkout 上继续改 = 基线过时 + 拓扑违规）。`submitted_baseline(s)` 是提交/基线证据，不是关闭状态判据。

reopen 与相关命令的边界：
- **未 close 的 completed 工单**：`patch` 可直接在当前唯一活动根继续（[steps/08_patch.md](08_patch.md)「completed 工单分流」）
- **未 close 但基线过时**：走 `/icode worktree --update`（换基线），不是 reopen
- **已 close 且要恢复**：先 `/icode worktree --reopen`，再 `/icode patch` 在新 checkout 打补丁

## 前置校验

1. 调 `python3 tools/icode_control.py resolve-ticket --ticket <ticket_id> --workspace <原工程根>`；原 checkout 已消失时，resolver 必须经全局索引回退到可读 `archive_path`，返回路径即 `ICODE_OUT_DIR/control_root`。无 `--ticket` 且候选不唯一时先询问用户，不以“最新目录”猜测。
2. 读归档 metadata：`status == "completed"` 且 `close_state == "closed"`，并校验 `archive_manifest.json`；否则按 L1 表报错。`submitted_baseline(s)` 缺失不改变 closed 判定，但默认基线无法由提交证据恢复时必须从 `submission_contracts.target_remote_ref` 得到唯一目标，否则要求用户显式 `--target`
3. 调用统一拓扑门禁（§3.8），verdict=blocked 报错退出
4. 解析目标基线：默认 → **复用已提交契约**（读 `submission_contracts` 中 super 仓库契约的 `target_remote_ref`，缺失按 [references/worktree_isolation.md](../references/worktree_isolation.md) §3.7 推导）的远程 ref（本地不可解析则报错）——**不用当前环境临时推断目标**；`--target <ref>`（可选扩展）→ 用户指定 ref，且**仅此时更新契约**（默认 reopen 不改变契约目标）

## 执行流程

1. **解析基线**：目标基线 ref + commit（`git rev-parse <ref>`）；记录实际解析到的 commit（分支名不当作稳定 commit）
2. **创建新 checkout**：`git worktree add -b "icode/<ticket-slug>-reopen-<N>" <新路径> <基线ref>`（`<N>` 为 reopen 代数序号，如已有 `-reopen-1` 则用 `-reopen-2`；基于远程基线创建自动 tracking，命令见 [references/worktree_isolation.md §1「② 创建」](../references/worktree_isolation.md)）。**G1 契约重建**：显式 `git -C <新路径> branch --set-upstream-to=<remote>/<目标分支> icode/<ticket-slug>-reopen-<N>` + 逐项比对（HEAD 可解析 / 当前分支 / `@{u}` == 契约 `target_remote_ref` / remote 与主仓一致），更新 `submission_contracts` 中 super 仓库契约项（新 `worktree_branch`/`target_commit_at_create`/`tracking_verified`；目标分支不变时 `target_remote_ref`/`target_push_ref` 保持不变）
3. **受影响业务子仓**：若工单涉及业务子仓修改，为受影响子仓创建隔离 checkout（同 §1「⑤ 业务子仓隔离」，基于子仓远程基线），写 `metadata.sub_worktrees`
4. **校验新 checkout**：`git -C <新路径> rev-parse --verify HEAD` + 分支核对 + 基线 = 解析到的目标 commit
5. **受控原子切换**：组装不含 status/close/delivery 控制字段的 `--metadata-json`：
   - 旧 checkout（若有）写入 `checkout_history`（`state=submitted` 或 `removed`，依据旧目录是否还存在）
   - `active_checkout` = 新 checkout（`base_ref`/`base_commit` = 解析基线，`activated_at=<ts>`，`state=active`）
   - 调 `python3 tools/icode_control.py reopen --dir {control_root} --metadata-json '<json>' --reason '<恢复原因>' --request-id '<本次唯一键>'`。工具会复验 checkout Git 根/分支/HEAD，原子将 `close_state` 从 `closed` 解冻为 null、`migration=null`、`delivery_verdict=verification_pending`，追加 `ticket_reopened`，并在新 checkout 写身份指针
   - 调 `python3 tools/icode_control.py index-write --ticket-dir {control_root}` 同步全局索引；禁止手工直改 `index.json`
6. **记录恢复原因**：把 reopen 触发背景（用户为什么恢复本工单、本次要补什么）追加到 `08_patch.md` 对应 Patch 段触发背景，或记入工单历史（`patch_history`/决策锚点 `patch_summary`）——**checkout_history 中本代 checkout 不携带原因，原因记入工单历史**
7. **输出确认**：`✅ 已 reopen {ticket_id}：代码根={新 checkout}，产物/事件根={control_root}，基线={commit 前 12 位}；后续 /icode patch 从 .active_ticket.json 找回工单`

> **重开后双根契约**：在 `active_checkout.path` 修改/验证代码，但 `08_patch.md`、metadata、trace 和事件继续写 `artifact_root=control_root`。新 checkout 里只有 `.active_ticket.json` 定位指针，不复制、不“复活”旧工单目录。第二次 close 的 archived 前，先运行 `archive-manifest --dir {control_root} --archive-dir {control_root} --write` 就地重建完整性基线。

## 幂等性

重复执行 reopen：使用同 `request_id` 和同一 checkout 身份时返回 `already_applied`，并自愈指针/manifest，不创建第二个 checkout。已有不同 `active_checkout` 则拒绝；用户确需再换基线 → 先 `/icode worktree --update`。

## 反偷懒

- **禁止复活旧目录**：不在已 close 的旧 checkout 上继续 patch
- **禁止覆盖未确认状态**：close 后旧 checkout 目录若仍存在，reopen 不自动删除（留用户确认）；`checkout_history` 如实记录旧目录存在状态
- **禁止清空 patch 历史**：reopen 不重置 `patch_count`/`patch_history`（补丁历史是工单身份的一部分，见 I-3）
- **禁止临时推断契约目标**：默认 reopen 复用已提交契约 `target_remote_ref`（G1 冻结），不得用当前环境临时推断；仅显式 `--target` 才更新契约目标
- **禁止真实项目术语**：示例/输出用通用占位符

## MCP 推荐

reopen 为 **L1（短决策记录）**：结构化状态机执行（判断+git 操作），不需要 spawn 子代理，不调用 sequential-thinking；决策字段（恢复必要性 → 基线选择 → 子仓影响 → 风险评估）记入 `.decision_anchors.json`（见 [references/decision_anchors.md](../references/decision_anchors.md)「L1 决策记录契约」）。其余 MCP 不推荐。

**强制约束**：🟢/🟢*/⚪ 语义 + 双保险机制（执行步骤内嵌 + thinking_core gate）详见 [SKILL.md「MCP 调用覆盖强制化」](../SKILL.md) + [references/mcp_per_step.md「双保险机制」](../references/mcp_per_step.md)。
