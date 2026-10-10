# Crosscheck 独立复评模式

> 本文件是 `/icode crosscheck` 的隔离、轮次和恢复真源。Crosscheck 是“正式工单的外部只读评审记录”，不是正式工单，也不是 debug 工单。

## 1. 原工单零回写

对目标工单和实现根执行严格零写入：不得更新 metadata、事件链、snapshot、索引、命中次数、anchors、status/verdict/delivery_verdict、patch_history、verification_runs、Agent 记录或源码。不得调用 `icode_control.py` 的任何 writer；可复用只读身份解析和纯候选捕获/比较接口。

Crosscheck 的全部新文件只能位于目标项目：

```text
<project_root>/.icode_output/.crosscheck/.icode_output_N/
```

目录内禁止 `.ico_metadata.json`，因此 list/status/index、正常 latest、工作流状态机和 UI 都不会把它识别为工单。

## 2. 目录身份

- `.crosscheck` 的 `N` 独立递增，不占正式工单 `.icode_output_N` 或 `.debug/.icode_output_N`。
- 同一 `canonical project_root + target_ticket_id` 只能对应一个 crosscheck 容器。
- 容器身份记录在 `crosscheck_manifest.json`；出现两个匹配容器、缺 manifest 的编号目录或身份不一致时 fail-closed。
- 目录和关键 JSON 不得是符号链接；写入使用目录锁、临时文件、fsync 和原子 replace。
- Crosscheck 不写全局索引；项目整体备份可以原样复制 `.crosscheck` 树，但不得为其生成 `backup_path` 索引条目。

## 3. 目标解析

接受 ticket id、工单目录、metadata 路径、工单内任意产物路径，以及当前会话绑定工单。无参数不等于 latest：只有 cwd 所属工单或 `.active_ticket.json` 可给出唯一当前身份，否则报错。

目标要求：

- metadata 可读且 ticket_id 非空；
- `status=completed`；
- 非 debug、非 crosscheck 容器；
- metadata 的 `project_path` 或工单所属项目根真实存在。

Legacy completed 工单允许只读复评，不迁移、不补 schema、不伪造事件。

## 4. 多轮状态

一个容器可追加任意轮复评。round 从 1 连续递增：

```text
in_progress/fresh_review
  -> in_progress/history_compare
  -> completed/finalized
  -> 下一次调用创建 round N+1
```

异常终态还有 `blocked/finalized` 与 `stale_input/finalized`。中断时：

- 输入快照不变：重新 start 返回同一 round，`resumed=true`；
- 输入快照已变：旧 round 标 `stale_input`，开始下一 round；
- 已 completed：总是追加下一 round，即使当前输入与上一轮相同。

完成轮的 fresh/final/Markdown 哈希写入 manifest；后续禁止覆盖。累计报告可由完成轮重建。

## 5. 输入快照与漂移

每轮 start 冻结：目标工单目录内普通文件/符号链接清单及 SHA-256、metadata 声明的 code_files、活动实现根、Git root/branch/HEAD/过滤后的 status、patch_count，以及有 Git 时的全源码 candidate。锁文件和 crosscheck 自身输出不参与摘要。候选内容检测不局限必审种子，关联头文件的相同 untracked 状态也会按内容识别漂移；漂移检测不自动扩大必审范围。已启用候选的工单始终使用当前受控 `candidate_tracking.base`，显式改变 base 即使源码字节相同也使旧结果过期，新轮绑定当前候选。未启用候选的旧工单延续首轮冻结 OID，同内容线性提交须机器证明，不因每次 HEAD 重新设 base 而机械过期；候选只保存在独立 round，不启用原工单门禁。

finish 必须重新采集并比较源码候选和活动轮输入产物边界。不同即 `stale_input`：本轮内容保留作为过时草稿，但不得生成“有效完成”结论；重新运行开始下一轮。历史完成轮不因目标后续合法变化而改写，validate 只校验当时记录和不可变文件哈希。

`freshness` 只读派生可选复评状态：没有记录或有效结论为 `not_run`，完成且源码等价为 `current` 并附 `pass|changes_recommended`，源码漂移为 `stale`。先验 manifest snapshot 身份与 fresh/final/worklist/Markdown/report 不可变完整性，再严格检查 snapshot_identity_ok 与候选比较，stale 不能掩盖篡改。无候选的旧轮附 `legacy_untracked`，不伪造 current；后续 verification/report 输入变化仅说明 `input_boundary_changed`，不把源码结论误判过期。查询不建目录、锁或轮次，不写原工单，不自动重跑，也不进入交付主门禁。

## 6. 防确认偏差门

每轮分两份 JSON：

新轮还带独立 `crosscheck_round_N.worklist.json`，按 [inspection_worklist.md](inspection_worklist.md)登记本轮 fresh Read、验证 finding 位置/原文/hash。start 可用内部 `--related/--scope/--baselines-json` 传入真实影响面；恢复轮不改变边界。freeze 同时冻结清单 hash，未审完仅 blocked；原工单零回写不变，旧轮只读兼容。

1. `crosscheck_round_N.fresh.json`：不读旧 crosscheck 后的独立判断，finding status 只能为 `new`。
2. `crosscheck_round_N.json`：fresh 经 freeze 固化后，才读取上一完成轮并标注生命周期。

freeze 前工具不返回 previous round；freeze 记录 fresh SHA-256 后才返回上一轮路径。final 必须覆盖 fresh 与上一轮全部 finding id；无法复核也要显式写 `not_rechecked`，不能静默删除。

## 7. Finding 与结论

严重度：`blocker | major | minor | suggestion`。

生命周期：`new | still_present | resolved | superseded | regressed | not_rechecked`。

轮次 verdict：

- `pass`：无 findings；
- `pass_with_suggestions`：只有非阻断优化建议；
- `changes_recommended`：建议修改设计、代码、测试或文档；
- `blocked`：证据不足、目标不可评或存在阻断问题。

建议必须有 evidence、analysis、recommendation 和 requires_change；证据不足必须写 evidence boundary，不得编造执行结果。

## 8. 与现有 ICODE 能力的边界

- `review`：会进入原工单 review 状态，主要审计划；crosscheck 不进入。
- `deepcheck/audit`：属于原工单流程并可修代码；crosscheck 只给建议。
- `verify`：写 verification_runs；crosscheck 只审既有证据。
- `patch`：真正实施追加修改；crosscheck 不自动调用。
- `--debug`：同样项目内隔离，但 debug 有独立 metadata/status；crosscheck 连工单都不是。
- Agent Runtime/UI：不暴露 crosscheck 动作，防止误当可写步骤。用户在 Codex、Claude Code 或 CodeBuddy 中自行换模型/Agent 后从 help 调用。

## 9. 恢复与校验命令

```bash
python3 tools/icode_crosscheck.py start [--workspace <root>] [--ticket <id> | <path>]
python3 tools/icode_crosscheck.py freeze --dir <crosscheck_dir> --round <N>
python3 tools/icode_crosscheck.py finish --dir <crosscheck_dir> --round <N>
python3 tools/icode_crosscheck.py validate --dir <crosscheck_dir>
python3 tools/icode_crosscheck.py freshness <ticket_dir> [--workspace <root>]
python3 tools/icode_crosscheck.py freshness --dir <crosscheck_dir>
```

任何失败都保留已有文件，不删除旧轮、不猜测身份、不修改原工单。需要修复时由用户显式进入 `/icode patch`。
