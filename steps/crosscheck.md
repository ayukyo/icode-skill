# 步骤 crosscheck — 已完成工单独立复评（零回写、可多轮）

**命令**：`/icode crosscheck [--ticket <ticket_id> | <工单目录或工单内产物路径>]`
**产出**：`<项目根>/.icode_output/.crosscheck/.icode_output_N/`
**定位**：高级独立步骤；不属于步骤 0~6，不进入工单状态机、索引、UI 或 Agent Runtime 动作列表。

> 开始前完整读取 [thinking_core.md](../references/thinking_core.md)、[anti_laziness.md](../references/anti_laziness.md)、[evidence_and_verification.md](../references/evidence_and_verification.md) 和 [crosscheck_mode.md](../references/crosscheck_mode.md)。目录、轮次、冻结和零回写规则以 `crosscheck_mode.md` 为真源。

## 目的

一个 AI Agent 完成 ICODE 全流程后，用户可自行换 Agent 或模型，再调用本步骤从头检查工单设计、代码修改和验证证据，发现遗漏并给出“是否需要修改/优化”的建议。命令不提供 `--model`，也不负责切换模型。

Crosscheck **只评审、不修复**。需要采纳建议时，由用户后续显式调用 `/icode patch`；本步骤不得自动转 patch。

## L1/L2 检查项声明

- **L1**：目标不能唯一解析、目标不是正式 `completed` 工单、项目根不可用、同目标有多个 crosscheck 容器、隔离路径异常、fresh 被改写、输入漂移。
- **L2**：关键源码/构建/设备证据不可读时，保留明确 evidence boundary 并把结论降为 `blocked` 或带建议通过；不得用旧 audit 的“通过”代替本轮复核。

## 执行步骤

### 1. 解析并开始/恢复轮次

根据用户形式执行：

```bash
python3 tools/icode_crosscheck.py start --workspace <project_root>
python3 tools/icode_crosscheck.py start --workspace <project_root> --ticket <ticket_id>
python3 tools/icode_crosscheck.py start --workspace <project_root> <ticket_path_or_artifact>
```

- 当前工单会话无参数时，优先使用会话已经绑定的 `ICODE_OUT_DIR` 作为路径参数；其次才允许 cwd 所属工单或 `.active_ticket.json`。禁止用 latest 猜测。
- 保存返回的 `crosscheck_dir`、`round`、`fresh_output`。
- `resumed=true` 表示继续未完成轮次；不要另建目录或跳号。

### 2. 首次独立评审（禁止先看旧 crosscheck）

在写完 fresh 文件并执行 freeze 前，**不得读取既往 crosscheck 的 `crosscheck_round_*.json/.md`、`findings.json` 或 `crosscheck_report.md`**。可以且必须读取目标工单自己的需求、计划、审查、定稿、代码复检、deepcheck、audit、patch、verification 和证据账本。

至少覆盖：

1. 需求与设计闭环：需求点、范围、ADR、接口、状态/数据流、异常恢复。
2. 实现一致性：定稿与真实 diff/code_files 的逐项映射，遗漏、越界和隐式行为变化。
3. 代码风险：边界、错误返回、并发/资源生命周期、安全、性能、可维护性。
4. 兼容影响：调用方、被调用方、协议/配置/数据兼容、Codex/Claude Code/CodeBuddy 或目标项目宿主。
5. 验证可信度：测试是否真实执行、覆盖是否对应风险、实机/部署/消费证据边界，禁止把历史结果当本轮结果。
6. 文档与交付：用户可见行为、回滚/恢复、残余债务和建议优先级。

将独立结论按 [crosscheck-round.schema.json](../schemas/crosscheck-round.schema.json) 写到 `fresh_output`。此时所有 finding 的 `status` 必须为 `new`；没有问题时 `findings=[]` 且 verdict 可为 `pass`。

### 3. 冻结 fresh，随后才比较历史

```bash
python3 tools/icode_crosscheck.py freeze --dir <crosscheck_dir> --round <N>
```

freeze 成功后 fresh 文件不可再改。仅此时才能读取返回的 `previous_round`（首轮为 null），比较前后变化并写 `final_output`：

- 新发现：`new`
- 仍存在：`still_present`
- 已修复：`resolved`
- 被新结论取代：`superseded`
- 修复后复发/变坏：`regressed`
- 本轮证据不足无法复查：`not_rechecked`

最终 findings 必须覆盖 fresh 与上一完成轮的 finding id，不得静默丢项。

### 4. 完成本轮

```bash
python3 tools/icode_crosscheck.py finish --dir <crosscheck_dir> --round <N>
python3 tools/icode_crosscheck.py validate --dir <crosscheck_dir>
```

finish 会重采目标快照。若工单产物、代码文件或 Git 基线变化，本轮标记 `stale_input` 并返回非零；保留本轮文件，重新调用 `/icode crosscheck` 开始下一轮。输入稳定时生成轮次 Markdown、累计 `findings.json` 和 `crosscheck_report.md`。

### 5. 向用户报告

只报告：目标 ticket、round、verdict、blocker/major/minor/suggestion 数量、证据边界、建议修改项和报告绝对路径。明确“未修改原工单与代码；如需采纳，请显式调用 `/icode patch`”。不得把 `pass_with_suggestions` 说成无条件通过。

## 反偷懒

- 禁止只复述 `06_audit.md`；必须重新核对设计和代码。
- 禁止 freeze 前读取旧 crosscheck，避免确认偏差。
- 禁止修改目标工单、源码、metadata、事件、index、patch_history 或 verification_runs。
- 禁止创建 `.ico_metadata.json` 把 crosscheck 伪装成工单。
- 禁止因同一基线已评审过而拒绝新一轮复评。
- 禁止自动调用 patch、commit、push、deploy、close 或删除任何目录。
