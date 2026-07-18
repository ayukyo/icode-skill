# /icode status - 工单状态查询 + verdict 标注

**命令**:
- `/icode status`：只读查当前工单状态
- `/icode status --verdict <ticket_id> <verified|disproved|superseded> "<reason>" [--correct "<正确方向>"] [--source <machine_test|review|user|auto_signal>] [--superseded-by <ticket_id>] [--premise-dep <module>:<commit>[:<path>]]...`：手动标注工单方向结论（双写 metadata + 全局索引）
- `/icode status --scan-verdict`：批量扫描 unknown 完成态工单的证伪信号，提示标注

**产出**: 默认无（只读）；`--verdict` 写 `{ICODE_OUT_DIR}/.ico_metadata.json` + `~/.claude/icode_data/index.json`（不写工程内源码文件）；`--scan-verdict` 只读 + 输出标注建议
**会话**: 主会话

## 定位

会话中断后恢复时，用户不知道当前在哪个步骤。此命令默认纯读 metadata 输出摘要帮助快速定位；`--verdict` 给历史工单标方向结论（防误导新需求，详见 SKILL.md「verdict 字段族」+「注入形式·按 verdict 分流」）；`--scan-verdict` 批量识别可能被证伪但未标注的旧工单（治本，比一个个手动标高效）。

## 模式一：默认只读查询（`/icode status`）

1. 执行目录管理中的「检测最新目录」逻辑，确定 `ICODE_OUT_DIR`
2. 读取 `.ico_metadata.json`
3. 输出状态摘要：

```
最新工单: .icode_output_N (ticket_id)
需求: {requirement}
状态: {status}（{status中文说明}）
模式: {mode 字段读取方式：直接读 metadata.mode 字段，缺失或空值视为 "full"（默认）；fast 模式下显示「fast（精简：review 1轮无对抗 + deepcheck 仅 Reverse）」}
方向结论: {verdict 字段读取方式：直接读 metadata.verdict 字段，缺失视为 "unknown"；显示 verdict + verdict_reason 摘要（若有）}
已完成: {completed_steps 链路，如 log -> 1 -> 2 -> 3 -> 4}
下一步: {根据续跑判定规则推断，如 "/icode deepcheck (步骤5复检)"}
代码文件: {code_files 列表，无则"未编码"}
索引工单: {全局索引 tickets 数} 条（stale: {stale=true 条数} 条 / disproved: {verdict=disproved 条数} 条）（用 `json.load` 全量解析 `~/.claude/icode_data/index.json` 的 `tickets` 数组取长度并统计 stale=true / verdict=disproved 数，禁止按行截断--「前 50 行」仅适用于 project_docs 章节）
```

**`status` -> 中文说明映射**（与 SKILL.md「status 字段枚举」表一致）：

| status | 中文说明 |
|--------|----------|
| `log_in_progress` / `log_done` | 日志根因分析中 / 完成 |
| `init_in_progress` | 步骤0 需求初稿讨论中 |
| `plan_done` | 步骤1 计划完成 |
| `review_in_progress` / `review_done` | 步骤2 审查中 / 完成 |
| `plan_finalized` | 步骤3 定稿完成 |
| `code_in_progress` / `code_done` | 步骤4 编码中 / 完成 |
| `deepcheck_in_progress` / `deepcheck_done` | 步骤5 复检中 / 完成 |
| `completed` | 步骤6 终审完成（终态） |

4. 若无 `.icode_output_N` 目录，输出提示："未找到工单目录，请先运行 /icode start/init/log"

## 模式二：verdict 手动标注（`/icode status --verdict ...`）

**用途**：给历史工单标方向结论，让历史检索注入按 verdict 分流（disproved 反转避坑、superseded 注替代指针），防止已被证伪/取代的工单误导新需求。**典型场景**：某工单核心方案实机证伪已回退，标 `disproved` + 正确方向，后续新需求命中时反转注入避坑而非正面借鉴。

**参数**：
- `<ticket_id>`：必填，目标工单 id（如 `myproject-5-a3f2`）。可用 `/icode status` 查当前工单，或在 `~/.claude/icode_data/index.json` 按 ticket_id 查任意历史工单
- `<verified|disproved|superseded>`：必填，verdict 值
- `"<reason>"`：必填，`verdict_reason`（≤150 token）。`disproved` 时填证伪原因（如"某接口实机发现语义是重置而非冻结，方案从根上不可行"）
- `--correct "<正确方向>"`：可选，`correct_direction`（≤150 token）。`disproved`/`superseded` 时建议填（反转注入避坑的核心载体），如"改用上报抑制机制替代暂停数据流"
- `--source <machine_test|review|user|auto_signal>`：可选，`verdict_source`，默认 `user`
- `--superseded-by <ticket_id>`：可选，`superseded` 时填替代工单 id
- `--premise-dep <module>:<commit>[:<path>]`：可选，可多次。`disproved`/`superseded` 时填证伪前提依赖的外部模块（支持硬复活）。`module`=模块名、`commit`=证伪当时的 commit SHA（`git rev-parse HEAD` 只读）、`path`=该模块代码路径（`git -C` 定位用，可省略）。填后 `--scan-verdict` 能检测该模块 commit 变化，变了置 `verdict_review_needed=true` 降级注入（防漏过后来又可行的方向）

**执行流程**：
1. **定位工单**：按 `<ticket_id>` 在 `~/.claude/icode_data/index.json` 查找条目（`json.load` 全量解析，找不到则报错退出）；由条目的 `project_path` + `out_dir` 定位工单目录的 `.ico_metadata.json`
2. **校验产物存在**：`test -d {project_path}/{out_dir}` 失败则报错（工单产物已删，无法标注）
3. **双写 verdict 字段**（metadata + index 同步，原子写回）：
   - 读 metadata `.ico_metadata.json` + index 对应条目
   - 写 `verdict` / `verdict_reason` / `correct_direction` / `verdict_source` / `verdict_at`（运行时取系统时间 `date +%Y-%m-%dT%H:%M:%S`，禁写死，同 `last_used_at` 约定）
   - `superseded` 时额外写 `superseded_by`（来自 `--superseded-by` 参数）
   - 若有 `--premise-dep` 参数：写 `verdict_premise_deps`（数组，每条 `{module, commit, path}`）+ 初始化 `verdict_review_needed=false`（首次标注未检测前为 false，后续由 `--scan-verdict` 或检索命中被动检测改写）
   - **幂等覆盖**：已有 verdict 也覆盖（刷新 `verdict_at`），不报错；verdict 变化时记录新 verdict_at
   - 写回 metadata + index.json（两处同步，不得只写其一）
4. **输出确认**：`✅ 已标注 {ticket_id} verdict={verdict}（source={source}）；后续检索命中将按 verdict 分流注入（disproved 反转避坑 / superseded 注替代指针 / verified 正常借鉴）`

**反偷懒**：
- **禁止无 reason 标 disproved**：`reason` 是反转注入避坑的依据，必填
- **禁止标 disproved/superseded 不标 correct_direction**：`correct_direction` 缺失则降级注入 ADR+⛔警告，价值打折，应尽量补全（`--correct`）
- **禁止编造 verdict**：verdict 须基于实证（实机验证/审查结论/用户确认），不得猜测；`verdict_source` 须如实标

## 模式三：批量识别证伪信号（`/icode status --scan-verdict`）

**用途**：扫描所有 `verdict=unknown` 的完成态工单（`status=completed`）的 `00_init.md` 末轮对话摘要 + `06_audit.md`，识别含证伪信号的，提示用户标注。**同时扫 `disproved`/`superseded` 工单的 `verdict_premise_deps`**，检测证伪前提依赖的模块 commit 是否变化，变了置 `verdict_review_needed=true`（硬复活检测，防漏过后来又可行的方向）。解决"旧工单没标 verdict 但可能有坑"+"已标 disproved 但依赖更新可能又可行"的批量治理。**只读 + 提示，不自动写 verdict**（NLP 判方向不可靠，必须用户确认后用 `--verdict` 标注；但 `verdict_review_needed` 是客观 commit 比对，可自动写）。

**执行流程**：
1. Read `~/.claude/icode_data/index.json`，`json.load` 全量解析 `tickets` 数组
2. **筛选两类对象**（均要求 `status="completed"` 且 `stale=false`；未完成态/stale 跳过）：
   - **A·unknown 候选**：`verdict` 缺失或为 `"unknown"`（找证伪信号，提示标 disproved）
   - **B·disproved/superseded 候选**：`verdict` 为 `"disproved"`/`"superseded"` 且 `verdict_premise_deps` 非空（检测证伪前提依赖变化，硬复活）
3. **对 A·unknown 候选**：
   - Read 其 `{project_path}/{out_dir}/00_init.md` 的「对话摘要」章节末轮（若存在）
   - Read 其 `{project_path}/{out_dir}/06_audit.md` 的「结论」段（若存在）
   - **信号词匹配**（粗筛，零 LLM）：扫末轮/结论是否含证伪信号词：`回退|从根上不可行|方案被推翻|已回退|不可行|证伪|放弃|废弃|改为|改用|替代方案|实机发现|与预期不符|方案失败|重置`
4. **对 B·disproved/superseded 候选（复活检测）**：
   - 读其 `verdict_premise_deps`（空数组跳过--无硬复活能力，靠软复活）
   - 对每个 dep，取当前 commit：`git -C {dep.path} rev-parse HEAD`（只读，stale 白名单内；`dep.path` 目录不存在则记 `path_gone`，视为变化）
   - 若 `dep.commit != 当前 HEAD`：证伪前提依赖已变化，置 `verdict_review_needed=true` 写回 index.json（客观 commit 比对，**可自动写**，不写 verdict 本身）
   - 若所有 `dep.commit == 当前 HEAD`：`verdict_review_needed=false`（证伪前提依赖未变，保持硬反转+证伪前提断言）
5. **汇总输出**（按信号/变化命中数排序）：

```
=== 扫描结果 ===

⚠️ A·疑似证伪（unknown 完成态，建议标 disproved）：
  1. {ticket_id}（{requirement_summary 摘要}）
     信号：末轮「{匹配的信号词 + 上下文片段}」
     建议命令：/icode status --verdict {ticket_id} disproved "{证伪原因}" --correct "{正确方向}" --premise-dep {module}:{commit}:{path}
  2. ...

🔁 B·证伪前提待重新评估（disproved/superseded，依赖已变化，已置 verdict_review_needed=true）：
  1. {ticket_id}（disproved）
     证伪依赖：{module}@{旧commit}（当前 {新commit}，已变化）
     后续命中将降级走 unknown 对抗质疑（不硬避坑）
     建议：重新评估证伪前提是否仍成立
       - 仍成立：/icode status --verdict {ticket_id} disproved "..." --premise-dep {module}:{新commit}:{path}  # 刷新依赖 commit
       - 已失效：/icode status --verdict {ticket_id} unknown  # 复活为 unknown（方向可重新考虑）
  2. ...

✅ A·无证伪信号（unknown 保持/标 verified）：{K 个}
✅ B·证伪前提未变（disproved/superseded 保持硬反转）：{L 个}

ℹ️ 跳过：未完成态 X / stale Y / 无 premise_deps（靠软复活）Z
```

6. **不自动写 verdict**：只输出建议命令，用户确认后手动执行 `--verdict` 标注；但 `verdict_review_needed` 是客观 commit 比对，步骤4 已自动写
7. 若无对象（无 unknown 完成态 + 无 disproved/superseded with premise_deps），输出"无可扫描对象"

**反偷懒**：
- **禁止自动判定 verdict**：信号词只是提示，必须用户确认后用 `--verdict` 标注（防误判--"回退"等词在正常上下文也出现）
- **禁止跳过 06_audit.md**：有些证伪写在终审结论而非 00_init 末轮，两处都要扫
- **禁止只扫当前工程**：`--scan-verdict` 扫全局索引所有 unknown 完成态工单（跨工程批量治理）

## 不需要强制思考前置

默认只读模式与 `--scan-verdict`（只读+提示）不产出代码/计划/审查文件，**不需要强制思考前置**，不需要 Read references。`--verdict` 标注模式是结构化字段写入（非思考/审查/编码），同样不需要强制思考前置，但须遵守本文件「反偷懒」约束。
