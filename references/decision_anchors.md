# 决策锚点机制（v2.8，步骤间思考传递）

> 本文件是 icode 决策锚点机制的共享规则。解决"步骤间只传产物文件，AI 思考推理不传"的痛点--下游步骤读锚点获取上游关键决策摘要，**不用重读 00_init/01_plan 全文**。锚点不是产物备份，是"思考推理的精炼传递"。

## 设计目标

icode 步骤间靠产物文件（00_init.md / 01_plan.md 等）传递信息，但产物的"思考推理"（为什么这么决策、4 维度设计态、已知偏离、未解决风险）散落在正文里，下游要重读全文才能获取。决策锚点把这些**关键决策摘要**抽出来，下游启动时读锚点即得，省 token + 不丢上下文。

## 锚点文件

`{ICODE_OUT_DIR}/.decision_anchors.json`（工单目录内，与 `.ico_metadata.json` 平级，跟随工单生命周期）

```json
{
  "requirement_digest": "需求一句话（≤100 token，从 00_init/plan 提炼）",
  "key_decisions": [
    {"id": "ADR-1", "title": "决策标题", "reason": "理由一句话（≤30 token）", "source": "plan §4"}
  ],
  "design_4dims": {
    "root_cause": "根因一句话（log 工单）/ N/A（init 工单）",
    "logic": "逻辑 5 类要点一句话",
    "concurrency": "竞态死锁设计一句话",
    "logging": "日志反映设计一句话"
  },
  "deviations": [
    {"plan_said": "计划说法", "actual_done": "实际做法", "reason": "偏离原因"}
  ],
  "open_risks": ["未解决风险1", "未解决风险2"],
  "updated_at": "步骤X 完成时间（运行时取系统时间，禁写死）",
  "updated_by": "init/plan/code/deepcheck/audit"
}
```

## 写时机（L3 自动，AI 主动提炼）

| 步骤完成 | 写什么 | 来源 |
|---|---|---|
| init（步骤0）| `requirement_digest` + `key_decisions`（待决策倾向）+ `design_4dims`（init §7）| `00_init.md` |
| plan（步骤1）| 刷新 `requirement_digest` + `key_decisions`（ADR 摘要）+ `design_4dims`（plan §4.5 4 维度设计态）| `01_plan.md` |
| code（步骤4）| 追加 `deviations`（同步 `code_deviations`）+ 刷新 `open_risks` | metadata + `04_code_review_fix.md` |
| deepcheck（步骤5）| 刷新 `open_risks`（deepcheck 残留风险）| `05_deepcheck.md` |
| audit（步骤6）| 最终刷新 `deviations` + `open_risks` | `06_audit.md` |

**写规则**：增量刷新（保留上游写的字段，只刷新本步骤负责的字段）；`updated_at`/`updated_by` 每次写时更新。

## 读时机（L4 自动，下游步骤启动时）

| 下游步骤 | 读谁的锚点 | 用途 |
|---|---|---|
| plan（步骤1）| init 写的锚点 | 获取 init 思考 + 待决策倾向，不用重读 `00_init.md` 全文 |
| review（步骤2）| plan 写的锚点 | 审查时对照设计决策（ADR 摘要 + 4 维度设计态）|
| code（步骤4）| plan 写的锚点 | 编码对照设计决策 + 4 维度设计态 |
| deepcheck（步骤5）| plan/code 锚点 | 复检对照设计 + 已知偏离（`deviations`）|
| audit（步骤6）| 全链路锚点（最后一次 updated_by）| 终审看决策演进 + 偏离汇总 + 残留风险 |

**读规则**：启动时 Read `.decision_anchors.json`（存在则读，不存在跳过走原流程）；读到的摘要进思考块作上下文，**不替代产物**（如需细节仍 Read 产物对应章节）。

## 开关（L0 一次性配置）

`metadata.anchors_enabled`（可选，默认 `true`，向后兼容旧 metadata）：
- `true`：各步骤完成后写锚点 + 下游启动读锚点
- `false`：跳过写/读（旧工单或用户关闭）

## 与产物文件的关系

- 锚点是**摘要**，不替代产物。下游仍可按需 Read 产物全文（如 plan §4 ADR 完整对比）
- 锚点是**思考传递**，传递"为什么这么决策"的推理，不是产物内容复制
- 锚点**不写工程产物**（只在 `.icode_output_N/` 内，不污染工程；建议 `.gitignore` 已含 `.icode_output/`）

## 边界处理

- 锚点文件不存在（旧工单 / `anchors_enabled=false`）-> 下游跳过读，走原流程（重读产物）
- 锚点字段缺失 -> 视为空，不阻塞
- 锚点与产物矛盾 -> **以产物为准**（锚点是摘要可能过时，产物是权威；下游发现矛盾时以产物为准并提示刷新锚点）

## 反偷懒

- **禁止锚点复制产物全文**：锚点是精炼摘要，每字段 ≤200 token；超量 = 偷懒（未提炼）
- **禁止跳过写锚点**：`anchors_enabled=true` 时，各步骤完成后必须写（init/plan/code/deepcheck/audit 五处）
- **禁止锚点替代产物**：产物仍是权威，锚点是补充；下游不得"只读锚点不读产物"做关键决策
- **禁止锚点写工程元数据**：锚点字段只含决策摘要，不含 ticket_id/步骤号等 icode 内部元数据

## 与既有机制的关系

- **与 .ico_metadata.json 互补**：metadata 存流程态（status/completed_steps），锚点存决策态（为什么这么决策）
- **与历史检索复用正交**：历史检索是跨工单借鉴，锚点是工单内步骤间传递
- **与 verdict 字段族正交**：verdict 是工单方向结论（供历史检索），锚点是工单内决策演进（供下游步骤）
