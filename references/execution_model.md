# ICODE 执行模型（行为树理念的轻量落地）

> 机器真源：`mcp/workflow-gate/gates.json` 的 `execution_model`。
> 唯一写入口：`tools/icode_control.py` 的 `step / artifact / operation`；`policy / trace` 只读。
> 本模型不引入 BehaviorTree.CPP、XML、全局黑板或常驻 tick。`.ico_events.jsonl` 仍是唯一执行账本。

## 1. 五项能力及边界

1. **Step Ports**：每个已登记步骤声明 `inputs / outputs`。开始时验证必填输入；成功终结前验证必填输出。端口只保存存在性、路径、大小和摘要，不把正文复制进事件链。
2. **Reactive 边界复检**：开始时冻结 `protected=true` 的输入摘要；在写入、等待返回、外部副作用、状态转换前重新计算。漂移即返回路由并 fail-closed，不在同一 attempt 静默刷新基线。
3. **统一轨迹**：步骤、边界 gate、产物、长动作和状态转换共用哈希事件链；`trace` 从同一来源生成时间线，不维护第二份运行状态。
4. **副作用感知 Retry/Fallback**：失败分类与动作类别共同决定 `retry / fallback / repair_then_retry / verify_receipt / block / human_decision`。只有只读瞬时失败可自动重试；写、外部动作和硬件动作禁止盲重放。
5. **长动作回执**：开始记录输入摘要，结束必须记录 outcome、duration、evidence、after_check 和下一策略。显式终结覆盖所有成功/失败路径，不能依赖稍后可能不会执行的 halt/cleanup。

## 2. 每个已登记步骤的固定顺序

执行步骤前先读取本文件和对应 step 合同。`<attempt>`、`<request>` 都是本次真实值，不得字面照抄；每条事件使用独立 request 幂等键。

```bash
python3 tools/icode_control.py step --dir <out_dir> --step <step> --phase start --request <request>
```

从 JSON 输出取得 `attempt`，后续始终带回。步骤发生写入、等待或副作用前，按合同 `required_checks` 调用：

```bash
python3 tools/icode_control.py step --dir <out_dir> --step <step> --phase check \
  --attempt <attempt> --boundary <before_write|after_wait|before_side_effect|before_transition> \
  --request <request>
```

- `result=pass`：继续。
- `result=blocked`：按 `route` 回流；先把旧 attempt 以 `blocked` 终结，再重新 `start` 形成新基线。
- 计划内修改了受保护输入（例如 deepcheck/patch 主动改代码）也必须终结旧 attempt 并重新开始复检；禁止提供“强制接受新摘要”的旁路。

每个落盘输出都记录实际文件摘要；路径必须命中该步骤的 outputs 端口：

```bash
python3 tools/icode_control.py artifact --dir <out_dir> --step <step> \
  --attempt <attempt> --scope <ticket|workspace|external> --path <path> --request <request>
```

步骤全部结束后写终结回执。`success` 会强制检查 required checks 与 required outputs；不完整时拒绝伪成功：

```bash
python3 tools/icode_control.py step --dir <out_dir> --step <step> --phase finish \
  --attempt <attempt> --outcome <success|failure|degraded|blocked|skipped> \
  --evidence <short-ref> --request <request>
```

若该步骤随后推进 metadata 状态，顺序必须是：`before_transition check → step finish → transition`。已 start 的步骤没有 `success/degraded` 回执时，完成态 transition 会阻断；从未接入执行事件的历史工单保持兼容。

## 3. 长动作与执行回执

构建、测试、远端读取、Git fetch/merge、设备监听/部署、文档转换、媒体解析等耗时或可中断动作使用 operation 回执。动作类别：

| class | 典型动作 | 自动重试 |
|---|---|---|
| `read_only` | 远端查询、fetch、只读日志拉取 | 仅 `retryable_transport` 且预算未耗尽 |
| `managed_write` | 原子文件写、受控 metadata/index 写 | 否；先确认幂等键和写后状态 |
| `external_side_effect` | merge、部署、发消息、远端变更 | 否；先查远端/文件/设备回执 |
| `destructive_hardware` | 擦写、升级、故障注入、硬件破坏性动作 | 永不自动重试，必须人工决定 |

开始动作：

```bash
python3 tools/icode_control.py operation --dir <out_dir> --phase start \
  --name <stable-name> --opclass <class> --input <identity-description> \
  --request <request>
```

结束动作（成功和失败路径都必须执行）：

```bash
python3 tools/icode_control.py operation --dir <out_dir> --phase finish \
  --attempt <attempt> --outcome <outcome> [--failure <failure-class>] \
  --evidence <short-ref> --check <after-check> --request <request>
```

`managed_write / external_side_effect / destructive_hardware` 的 start 必须有 request。若同名有副作用动作只有 start、没有 finish，新 start 返回 `ambiguous_side_effect`；正确处理是先核对客观结果，再用原 attempt finish，禁止“报错就再执行一次”。

## 4. 失败分类与策略查询

失败分类真源为 `failure_policies`：

- `retryable_transport`：连接重置、临时超时等可恢复传输故障。
- `capability_unavailable`：工具/模型/设备能力缺失，走声明过的 fallback，并降低结论上限。
- `deterministic_failure`：语法、编译、断言等稳定复现失败，先修复再重试。
- `policy_schema_security`：权限、策略、schema、安全门禁，立即阻断。
- `ambiguous_side_effect`：动作可能已生效但回执未知，先验证真实状态。
- `destructive_risk`：可能破坏数据/设备，交给人决定。

执行任何 retry/fallback 前先查询机器策略：

```bash
python3 tools/icode_control.py policy --opclass <class> \
  --failure <failure-class> --attempts <completed-attempt-count>
```

返回的 `action / max_attempts / backoff_seconds / conclusion_ceiling / auto_retry` 是编排依据。`auto_retry=false` 代表只能验证、修复、降级或请求人类决策，不能自动再次执行原动作。

## 5. 轨迹与中断恢复

```bash
python3 tools/icode_control.py trace --dir <out_dir> --limit 50
```

输出统一的 step/gate/artifact/operation/state 时间线，以及 `open_steps / open_operations`。恢复会话时先看 trace：

- open step：从上一个已通过边界继续，先重新 check；输入漂移则回流。
- open read-only operation：按 policy 决定是否重试。
- open side-effect operation：先核对真实状态，再补 finish；禁止直接重放。
- 已 finish：使用回执，不重复执行；request 相同但 payload 不同会触发幂等冲突。

`snapshot` 会带最近 10 条执行轨迹与开放 attempt，但 snapshot 只是可重建视图；事件链仍是真源。

## 6. 安全与兼容

- 事件链只存摘要和简短证据引用；禁止写密钥、Cookie、设备凭据、完整日志或大段工具输出。
- `close_state` 非空后维持既有冻结规则，关闭/重开继续由 `close-phase / reopen` 专用事件负责，不用通用 operation 绕过。
- 新事件类型由专用命令独占，通用 `event` 不能伪造。
- 端口/策略目录损坏时 linter 与执行器都 fail-closed。
- 行为树只提供设计理念：可恢复顺序执行、边界重新判断、明确终态与回执；不把 ICODE 改造成持续运行的机器人控制器。
