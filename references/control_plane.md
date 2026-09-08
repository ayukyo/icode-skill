# 控制面契约（工单 schema v3 / vNext）

> 本文件是工单控制面的**共享契约真源**：各步骤文档只在状态写回点引用一句话，规则细节全部收拢于此。
> 机器真源三件套：状态机与门禁分级 = `mcp/workflow-gate/gates.json`「state_machine」段；数据契约 = `schemas/ticket-metadata.schema.json`（v3）/`ticket-event.schema.json`/`ticket-index.schema.json`；执行器 = `tools/icode_control.py`（stdlib 零依赖）。

## 1. vNext 判定与 legacy 适配

- **vNext 工单**：metadata 顶层含 `"schema_version": 3`。新工单一律出生即 vNext（模板已内置）。
- **legacy 工单**：无 schema_version 字段的历史工单。控制面对其**只读适配**：`validate` 以宽松模式报告（不阻断只读审计），`transition/event/close-phase/index-write/snapshot` 拒绝变更（exit 3），提示先迁移。
- **迁移**：`python3 tools/icode_control.py migration --dir <out_dir>` 先 dry-run 出三分类报告（auto / needs_human / unmigratable），`--apply` 仅在 needs_human 为空时执行（备份 `.pre-v3.bak` + 追加 `migration_applied` 事件）。**禁止把 legacy 工单静默伪装成 vNext 合规工单**——needs_human 非空必须人工修正后迁移。
- **vNext metadata 核心字段禁止任意新增顶层 key**；专用/实验字段一律进 `extensions.<namespace>`（如 `extensions.agent`、`extensions.experimental`）。违例由 validate 的 `metadata_schema` 门禁报告。

## 2. 工单身份解析

| 场景 | 方式 | 行为 |
|---|---|---|
| 常规步骤（读/交互） | 现行「检测最新目录」 | 保持不变 |
| **变更类命令**（close / 索引写入等） | `resolve-ticket --ticket <ticket_id> --workspace <工程根>` | 扫描 `.icode_output` 命中 1 个目录 → 解析；0 个 → 全局索引兜底；**>1 个 → 拒绝猜测（exit 4，输出候选清单）** |
| 索引兜底失败 | — | 报 path_gone / ticket_id 不一致，提示用 `/icode list` 定位归档 |
| 只读便利 | `resolve-ticket --latest --workspace <工程根>` | 输出带 warning：变更类命令须显式解析 |

新工单必须先确定非空 `ticket_id`，再用 `create`原子写入 metadata 和 `ticket_created` 事件：

```bash
python3 tools/icode_control.py create --dir <out_dir> --ticket-id <id> \
  --requirement '<原始需求>' --birth <init|plan|log|debug-init|debug-log> \
  --metadata-json '<其他已登记字段对象>' --request-id <key>
```

`create` 必须是新工单空目录中的第一项写操作，并校验标准目录形状、normal/debug 域匹配及目录所有权。`--metadata-json` 不得注入 identity/status/completed_steps/indexed/delivery/verification/close 等控制字段。`plan/log` 出生时分别记为 `init_in_progress/log_in_progress`，产物与门禁完成后再经 `transition` 到完成态；禁止把 `plan_done/log_done` 伪装成出生态跳过门禁。

## 3. 状态流转（transition 强制）

- **每个状态写回点必须经**：`python3 tools/icode_control.py transition --dir <out_dir> --to <status>`，**禁止绕过直写 metadata.status**。validate 会比对 `metadata.status` 与最后一条 `state_changed` 事件，不一致报 `status_event_consistency`。
- 状态机真源见 gates.json `state_machine.transitions`（含重入：`*_done → 同步 *_in_progress` 允许，跨步跳跃拒绝）。
- **fail-closed 门禁分级**：
  - to **完成态**（`log_done/plan_done/review_done/plan_finalized/code_done/deepcheck_done/completed`）→ 依次运行三个 gate linter（vNext 一律 `--strict`），任一失败**状态不前移**，错误含 gate_id/期望/实际/修复入口。
  - to **进行态**（`*_in_progress`）→ 仅状态机合法性判定，不跑 linter。
  - debug 孪生工单（`metadata.debug=true`）走 `.debug` 隔离域，不跑通用门禁。
- **completed 强制显式交付结论**：`--delivery-verdict ∈ {verified, verification_pending, blocked, not_applicable}`。宿主验证通过不得自动映射为 verified（交付证据分层）。
- **幂等**：`--request-id <幂等键>`——同键同操作的重放返回「已应用」摘要（exit 0）；同键改变操作或 payload 报 `idempotency_conflict`，不猜测。
- `--skip-gates` 只在显式 `ICODE_CONTROL_TEST_MODE=1` 时生效，生产命令携带该参数会 fail-closed。

## 4. 事件链（.ico_events.jsonl）

- 每工单一本哈希链日志：`previous_event_hash` = 上一行 `event_hash`，首事件前驱为全 0；`event_hash` = sha256(去 hash 字段 canonical JSON)。validate 校验整链（断链/篡改报 `event_chain`）。
- 事件类型（schema 枚举）：`ticket_created` / `step_started` / `artifact_written` / `gate_checked` / `state_changed` / `metadata_updated` / `verification_recorded` / `snapshot_written` / `close_phase` / `ticket_reopened` / `requirement_delta` / `agent_spawned` / `agent_result` / `doc_module_status` / `index_updated` / `migration_applied` / `idempotent_hit` / `external_note`。
- 追加：`python3 tools/icode_control.py event --dir <out_dir> --type <type> [--payload '<json>'] [--request-id <key>]`。该通用入口只接受业务审计事实；`ticket_created/state_changed/metadata_updated/verification_recorded/snapshot_written/close_phase/ticket_reopened/index_updated/migration_applied/idempotent_hit` 为专用命令独占事件，通用 event 一律拒绝伪造。
- 事件是**追加式审计事实**，不是状态本身；状态以 metadata.status + 最后一条 state_changed 事件为准。

### 4.1 普通 metadata 单一 writer

- 已登记业务字段统一调用 `python3 tools/icode_control.py metadata-update --dir <out_dir> [--set-json '<object>'] [--append-json '<field-to-array>'] [--request-id <key>]`；数组追加必须用 `--append-json`，避免读旧数组后覆盖并发增量。
- `status/completed_steps/delivery_verdict`、`claims`、`verification_runs`、`close_state`、`indexed` 等控制字段分别由 `transition`、`record-claim`、`record-verification`、`close-phase`、`index-write` 独占，`metadata-update` 拒绝改写；未登记顶层字段拒绝，实验数据放 `extensions.<namespace>`。
- 每次 metadata 事务事件自动记录整个写后对象的 `metadata_hash_after`。`validate` 比对当前 metadata 与最近一次受控写 hash，因此即使只绕过工具直写一个非状态字段也会 fail-closed。旧事件链没有该字段时保持可读兼容，下一次受控写后开始强校验。
- 进入关闭流程后只允许经该命令更新拓扑、提交、迁移、归档账本字段；`archived` 后只能写归档 `control_root`；`closed` 后只允许专用 `reopen` 解冻。

## 5. 全局索引单一 writer

- 索引写入唯一入口：`python3 tools/icode_control.py index-write --ticket-dir <out_dir>`（写前重读合并 → 整文件 schema 校验 → 临时文件+fsync+原子 rename → 写后唯一性验证）。**禁止任何步骤直接改 index.json**。
- 检索命中、stale 等只存在于索引的字段经 `index-update --ticket-id <id> [--increment-hit] [--set-json '<json>']` 更新；`ticket_id/project_path/out_dir` 三元组禁止由该命令改写。
- 防线：锁覆盖整个读-合并-写窗口；同一**精确编号** `out_dir` 不同 `ticket_id` 拒绝写入；同 ticket_id 合并时保留未知扩展字段；debug 孪生拒绝入索引；成功后原子写 `metadata.indexed=true` + `index_updated` 事件。`archived` 交接后允许以 `archive_path` 作 `--ticket-dir`，但必须已有唯一原索引条目，writer 保留原 `project_path/out_dir` 身份三元组。
- 索引采用混合代际契约：无 `control_schema_version` 的 legacy 条目只做最小可读校验，避免旧 `.icode_output` 粗粒度路径、旧枚举或旧字段类型让所有新工单被连坐；新写或同 ticket 接管的条目固定 `control_schema_version=3`，其 `out_dir` 必须是编号相对路径，status/verdict/delivery_verdict 等字段严格校验。同 ticket 接管时，已登记字段由 metadata 重新投影，未知扩展字段保留。根 `version` 的 legacy 数字字符串可读，成功写入后规范化为整数 `1`。
- `validate` 默认核对 `~/.claude/icode_data/index.json`；测试或离线审计必须用 `--index <path>` 显式绑定同一索引，避免读到另一套全局状态。

## 6. 关闭分阶段（close_state）

- 阶段序列（gates.json `state_machine.close_phases`）：`close_planned → archived → roots_verified → checkouts_removed → branches_removed → closed`。
- 任一 `close_state` 非空后，普通 `transition`、通用 `event`、`record-verification` 和会追加事件的 `snapshot` 均冻结；只读 `snapshot --verify` 仍允许。不得在关闭流程中并行重开 review/code。关闭后修改必须先按 `/icode worktree --reopen` 建立新的活动 checkout。
- 记录：`python3 tools/icode_control.py close-phase --dir <out_dir> --phase <phase>`；仅 `status=completed` 且事件链有完成证据时可用；每阶段**幂等**，**禁止跨阶段跳转**。
- `archived` 前：先把必需小型控制产物复制到 `archive_path`，再运行 `archive-manifest --dir <out_dir> --archive-dir <archive_path> --write`。工具比对源/归档 hash，对顶层大文件留指针+hash，并复跑三类 linter；缺文件、hash 不同或门禁不等价时不生成 complete manifest，`close-phase --phase archived` 也会拒绝。
- **控制根交接**：`close-phase --dir <source_out_dir> --phase archived` 成功后，工具把包含 archived 事件的最新 metadata/事件链同步到 `archive_path`，刷新 manifest hash，并返回 `control_root`。`roots_verified` 起必须以该归档根作 `--dir`；源 checkout 消失不再阻断关闭留痕。
- 中断恢复：`archived` 交接未完时可在源根重放同阶段自愈；交接后从归档根 metadata.close_state 锚定下一阶段。执行细节见 steps/close.md。

### 6.1 受控 reopen

Git checkout 和逐仓提交契约由 `steps/reopen.md` 先创建/校验；然后在归档 `control_root` 调用 `reopen --metadata-json '{"active_checkout":...}' --reason ... --request-id ...`。工具校验新 checkout 分支和 HEAD，原子追加 `ticket_reopened`、将 `close_state` 从 `closed` 解冻为 null，并把旧 `delivery_verdict` 降为 `verification_pending`。禁止直接清空 close_state；那会与事件链冲突。重开后产物仍写归档控制根，代码只在 `active_checkout.path` 修改；下次关闭前用 `archive-manifest --dir <control_root> --archive-dir <control_root> --write` 就地重建 manifest。

## 7. 快照（ticket_snapshot.json）

- 每个关键步骤收尾可生成：`python3 tools/icode_control.py snapshot --dir <out_dir>` → 写入 `<out_dir>/ticket_snapshot.json`（当前阶段 / 下一合法动作 / 基线计数 / 未决项 / 链尾 hash）。
- 校验：`snapshot --verify` 比对 snapshot 与 metadata 状态、链尾 hash 与事件链完整性（过期/篡改即 fail）。

## 8. 实机验证记录

`record-verification` 在一个事务中追加 `verification_runs` 和 `verification_recorded` 事件，不修改 `patch_history/status/completed_steps/delivery_verdict`。`--evidence` 必填；复用构建时 `--build-source reused` 还必须提供 `--artifact-identity`。

需要分层验收时，在 metadata 声明 `verification_contract={required,required_layers,required_consumers,required_scenarios}`，并用 `record-verification --layer --consumer --scenario --baseline` 逐单元记录。只有 `required=true` 才启用 verified 门禁；每个必需单元取最新记录，必须 `outcome=pass` 且 evidence/baseline 非空。合同缺失或 `required=false` 不会给纯 host/历史任务强加真实环境要求。

`record-claim` 在一个事务中追加 `claims` 和 `claim_recorded` 事件。`kind` 仅允许 `fact/inference/unobserved/refuted`；所有 claim 必须写明 `source` 和“该证据不能证明什么”的 `boundary`，`fact/refuted` 还必须至少有一条 `--evidence`。普通 `metadata-update` 与通用 `event` 均不得伪造 claim。

## 9. 故障边界（控制面不可用时）

`tools/icode_control.py` 缺失、损坏或执行环境异常时，vNext 的状态、普通 metadata、事件、验证记录、关闭阶段和全局索引写入一律 **fail-closed**：停止变更，报告故障与修复入口。只读诊断可继续。禁止通过手工直写 metadata/index 绕过门禁；`control_plane_degraded=true` 仅作为历史遗留检测标记。

## 10. 子命令速查

| 子命令 | 用途 | 关键参数 |
|---|---|---|
| create | 原子创建工单+出生事件 | `--dir --ticket-id --requirement --birth [--metadata-json]` |
| resolve-ticket | 身份解析（多义即拒绝） | `--ticket` / `--dir` / `--latest` + `--workspace` |
| validate | 工单整体校验 | `--dir`（`--skip-linters` 仅限夹具） |
| transition | 状态流转 | `--dir --to [--delivery-verdict] [--request-id]` |
| event | 追加事件 | `--dir --type [--payload] [--request-id]` |
| index-write | 索引单一 writer | `--ticket-dir` |
| index-update | 更新索引独有字段 | `--ticket-id [--increment-hit] [--set-json]` |
| migration | legacy→v3 迁移 | `--dir [--apply]` |
| record-verification | 原子记录验证 | `--dir --kind --outcome --evidence [--layer --consumer --scenario --baseline]` |
| record-claim | 原子记录证据结论 | `--dir --kind --statement --source --boundary [--evidence ...]` |
| archive-manifest | 生成/校验归档 hash 清单 | `--dir --archive-dir [--write]` |
| close-phase | 关闭阶段记录 | `--dir --phase [--request-id]` |
| reopen | 在归档控制根解冻 closed 工单 | `--dir --metadata-json --reason [--request-id]` |
| snapshot | 快照生成/校验 | `--dir [--verify]` |

退出码：0 成功；1 违例/fail-closed；2 参数错误；3 legacy 拒绝（提示迁移）；4 身份多义。
