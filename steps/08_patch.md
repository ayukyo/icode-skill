# 步骤 8（独立步骤）— 追加修改 patch

**命令**: `/icode patch [问题描述或新需求...]`
**产出**: `{ICODE_OUT_DIR}/08_patch.md`（追加式，每次调用追加一个 `Patch N` 段）
**会话**: 主会话

## 本步骤 L1/L2 检查项声明

| 级别 | 检查项 | 触发后行为 |
|---|---|---|
| **L1·致命** | 无最新工单目录（`.icode_output/.icode_output_N/` 不存在或 metadata 缺失） | 报错退出，提示先 `/icode init` / `/icode start` 创建工单 |
| **L1·致命** | 最新工单处于入口态（`init_in_progress` / `log_done`，无 `01_plan.md`） | 报错退出，提示先 `/icode plan` / `/icode start` 进入主流程（patch 只作用于已有主流程产物的工单） |
| **L2·关键** | 阶段4 复检发现新引入问题且无法当场修复 | 警告 + 记入 metadata（`patch_history` 末条 `status="issues"`）+ 流程继续（user 可再跑 `/icode patch` 处理） |

## 定位

**patch 是主流程（步骤 1~6）之外的追加修改步骤**，解决两个场景：

1. **主流程完成后继续改**：步骤 6（`status=completed`）交付后，你测试发现问题 / 有新需求 → `/icode patch` 在既有工单上打补丁，**不新建工单、不重跑主流程**
2. **主流程中途追加改**：步骤 1~5 任一状态（如 `code_done` / `deepcheck_done`）发现问题想立刻修 → `/icode patch` 直接追加修改（提示"有未完成主流程步骤"，**不阻断**）

**不使用 patch 的场景**（走既有机制）：
- 步骤 2/5 中断态（`review_in_progress` / `deepcheck_in_progress`）→ 重跑 `/icode review` / `/icode deepcheck` 续跑（断点续跑机制）
- 步骤 4 编译失败（`code_compile_failed=true`）→ 重跑 `/icode code` 整体续跑
- 全新的、与当前工单无关的需求 → `/icode init` / `/icode start` 新建工单

**对状态机的影响**：patch **不改变** `status` 和 `completed_steps`（completed 保持 completed，中途状态保持原状态）。patch 是横向追加，不是纵向推进——靠 `patch_count` / `patch_history` 字段记录（见「强制操作」段），主流程推进逻辑（以 `completed_steps` 最大编号推进）完全不受影响。

**后续主流程步骤的配合**：patch 之后继续跑步骤 4/5/6 时，各步骤启动会 Read `08_patch.md` 把补丁纳入计划侧基准（code 在 patch 基础上实施 / deepcheck Reverse 不误判偏离 / audit 追溯矩阵纳入补丁）——详见 [SKILL.md「patch 与主流程步骤的配合」](../SKILL.md) + 各步骤文件「前置：patch 配合」段。review/merge 只动计划文档，不需要配合。

## 上下文控制铁律（解决"越问上下文越爆炸"）

> **patch 不靠会话记忆，靠磁盘产物**。同一会话内多轮提问、或跨会话/切换模型继续，**上下文一律从磁盘重载**，忽略与当前 patch 无关的历史对话内容：

1. **启动只读三个轻量源**（总 <3K token）：
   - `{ICODE_OUT_DIR}/.ico_metadata.json` —— 工单状态 + patch 历史计数
   - `{ICODE_OUT_DIR}/.decision_anchors.json` —— 关键决策摘要（缺失则跳过，见 [decision_anchors.md](../references/decision_anchors.md)）
   - `git status` + `git diff --stat` —— 当前代码现状（已改了什么）
2. **需要细节才定点读**：Read 产物（`00_init.md` / `03_plan_final.md` / `06_audit.md` 等）**只读与本次修改点相关的章节**（按锚点/标题定位），**绝不全文重读**（00_init/01_plan 全文动辄 300+ 行）
3. **新会话等价性**：每次 `/icode patch` 后本文件 + 锚点 + metadata 已落盘，即使你新开会话 / 切换模型，重跑 `/icode patch` 也能无损继续——**产物是唯一权威上下文**
4. **禁止把历史对话当依据**：判定现状只认磁盘（产物 + 代码 + git diff），不认"之前会话里我说过什么"；若发现磁盘现状与会话记忆矛盾，**以磁盘为准**并提示刷新

## 前置校验

> **读决策锚点**（启动时）：若 `metadata.anchors_enabled != false`，Read `{ICODE_OUT_DIR}/.decision_anchors.json`（不存在则跳过），获取既有决策摘要作上下文，不替代产物。详见 [references/decision_anchors.md](../references/decision_anchors.md)。

按 SKILL.md「检测最新目录」逻辑确定 `ICODE_OUT_DIR`（与 review/code/deepcheck/audit 相同）：

```bash
LAST=$(ls -d .icode_output/.icode_output_* 2>/dev/null | grep -oP '(?<=\.icode_output_)\d+' | sort -n | tail -1)
# 无 LAST → 报错退出，提示先 /icode init|start 创建工单
# 有 LAST → ICODE_OUT_DIR=".icode_output/.icode_output_${LAST}"
```

然后校验：

1. `{ICODE_OUT_DIR}/.ico_metadata.json` 存在，否则报错退出（非 icode 工单目录）
2. 读 `status` 字段：
   - `init_in_progress` / `log_done`（入口态，无 `01_plan.md`）→ **报错退出**，提示先 `/icode plan` / `/icode start`
   - `review_in_progress` / `deepcheck_in_progress` → **柔性提示**"当前有未完成的主流程步骤（步骤 2/5 中断态），建议先重跑 `/icode review` / `/icode deepcheck` 续跑"，**不阻断**，用户明确要 patch 则继续
   - 其余状态（`plan_done` 及以后 / `completed`）→ 直接进入执行流程
3. 读 `patch_count`（缺失视为 0），本次 `N = patch_count + 1`

## 执行流程（轻量四段式）

**强制思考前置**（不可跳过，按 [references/thinking_core.md](../references/thinking_core.md)「强制思考前置·统一契约」段执行）：本步骤子项（至少 4 步）= 现状重审要点 → 修改点清单 → 影响面预判 → 复检策略

**思考产出进 `08_patch.md` 对应段，不写工程产物**。

### 阶段 1 — 重新审视现状

> 目的：恢复"当前代码长什么样、工单做了什么、为什么这么做"的认知——**这是严谨性的第一道闸门**，禁止在没重审现状前直接改代码。

1. 读三个轻量源（见「上下文控制铁律」）
2. **必做**：`git status` + `git diff --stat`（无 git 仓库则跳过，用代码 Read 替代），确认：
   - 已修改/新增的文件清单（`code_files` 比对：metadata 记录的 vs git 实际的）
   - 未提交的改动范围
3. 按需定点读产物章节：`06_audit.md`（终审结论 + 补丁记录，若已有）+ `.decision_anchors.json`（决策摘要）+ 相关代码文件（本次修改点涉及的）
4. **输出「现状重审」摘要**（写进 `08_patch.md` Patch N 段）：当前状态、已改文件、与本 patch 相关的既有决策

### 阶段 2 — 增量计划（写 08_patch.md Patch N 段）

> 不重跑 `/icode plan`，但**必须有计划**——对本次修改点写「增量计划」，约束修改范围与影响面。

在 `{ICODE_OUT_DIR}/08_patch.md` **追加** `## Patch {N}` 段。文件不存在则新建，**头部说明固定如下**（首次创建时写入，之后不再重复）：

```markdown
# 追加修改记录（08_patch.md）

> 本文件记录主流程（步骤 1~6）之外的追加修改（`/icode patch` 调用）。
> 每次调用追加一个 `Patch N` 段，不覆盖历史。跨会话/切换模型靠本文件
> + `.decision_anchors.json` + `.ico_metadata.json` 重载上下文，不靠会话记忆。
```

每个 Patch 段包含：

```markdown
## Patch {N}（{date +%Y-%m-%d}）

### 1. 触发背景
（用户问题/新需求的原文或转述，保留用户原话）

### 2. 现状重审
（阶段1 摘要：当前状态 / 已改文件 / 相关既有决策）

### 3. 增量计划
（修改点清单：文件、符号、改动内容、预期影响；与既有主流程决策的关系——
  遵循既有决策还是修正既有决策，修正须说明理由）

### 4. 实施
（阶段3 落地：实际改动 file:line；与计划的偏离）

### 5. 验证
（阶段4 结果：编译/测试输出摘要 + 复检结论）

### 6. 收尾
（metadata patch_count/patch_history 追加 + 06_audit.md 补丁记录追加 + 锚点刷新）
```

**增量计划必须包含三链预扫**（对每个待改/新增符号，Edit 前逐个输出，任一条 0 命中即不合规——与 [04_code.md](04_code.md)「准入」段同一规则）：

1. **caller 链**：`grep -rn '<symbol>(' src/ include/`（调用方即影响面）
2. **import 链**：`grep -rn '<header\|from <module>\|import <pkg>'`（改签名/语义会被牵连）
3. **test 链**：`grep -rln '<symbol\|<module>.*test'`（受影响的测试文件）

**符号定位（serena 优先，同 v2.2 内嵌规则）**：工程有可索引源码且 serena 可用时，先 `find_symbol` + `find_referencing_symbols` 定位待改符号与调用点（语义匹配，比 grep 精准）再补三链 grep；不可用才降级纯 grep，降级说明进思考块。步骤末尾按反偷懒第 21 条自检门输出 `serena 调用: <工具 x N>` 或 `serena 降级: <原因>`。

### 阶段 3 — 实施（最小修改）

严格按增量计划实施，**复用 04_code 的硬性要求**（[04_code.md](04_code.md)「硬性要求」段，10 条全适用）：

1. 先读相关现有代码，对齐实际架构与代码风格
2. 只做增量计划内的修改——**禁止顺手重构、禁止夹带计划外改动**（最小侵入：git diff 应只有"新增+必要修改"）
3. 复用优先：新增工具/辅助函数前 grep 工程既有实现，有等价则复用
4. 风格对齐：命名/错误处理/日志/注释与同文件既有代码一致
5. 代码注释只写工程语义（功能/参数/边界/为什么），**不写 icode 工作流元数据**（同 04_code 第 7 条：禁止 `// patch 1 修复` 之类注释）
6. 跨文件修改：改函数签名/数据结构时同步更新所有引用方
7. 主动偏离记录：若发现增量计划不可行，**不得擅自改回**——偏离记入 metadata `code_deviations`（追加，`plan_said`=增量计划说法 / `actual_done` / `reason`），并同步写进 `08_patch.md` Patch N 段「实施」小节

### 阶段 4 — 复检（反向验证）

> 恢复"修改后必须验证"的严谨性——轻量版 deepcheck，聚焦**本次修改**，不重跑全量三阶段。

**强制思考前置**（不可跳过）：本步骤子项（至少 3 步）= 列本次修改点 → 逐点预判破坏面 → 定复检断言

1. **编译验证 + 测试验证**：与 [04_code.md](04_code.md)「强制操作」段同规则——编译最多 3 次；通过后探测并跑测试（`metadata.test_cmd` 存在则直接复用，缺失则按 04_code 探测规则）；退出码捕获防管道误判（重定向输出到临时文件再读，禁 `| tail` 后取 `$?`）
2. **反向验证清单**（每条须给 file:line 证据，写进 Patch N 段「验证」小节）：
   - **本次修改点**：改动是否全部落地？与原行为的差异是否符合预期？
   - **破坏面**：三链预扫命中的调用方/import 方/测试是否受影响？（新引入的破坏必须当场修复）
   - **竞态/边界**：本次改动是否引入新的空指针/竞态/边界遗漏？
   - **既有功能**：`git diff` 复核最小侵入——无关文件/无关改动 0 处？
3. 复检不通过（新引入问题且当场无法修复）→ 标 `patch_history` 末条 `status="issues"` + L2 警告，流程继续（不阻断；你可再跑 `/icode patch` 处理）
4. 复检通过 → `patch_history` 末条 `status="done"`

## 强制操作（完成后必须执行）

1. **更新元信息**（`.ico_metadata.json`）：
   - `patch_count` = N（本次序号）
   - `patch_history` **追加**一条：`{"patch_no": N, "summary": "一句话（≤100 token）", "files": ["相对项目根路径..."], "at": "date +%Y-%m-%dT%H:%M:%S", "status": "done"|"issues"}`
   - `code_files` **追加**本次新增/修改的文件（去重，保留历史）
   - 状态字段**不动**：`status` / `completed_steps` 保持原值（见「定位」段）
2. **追加更新 `06_audit.md`**：在文件末尾追加 `## 补丁记录（patch 追加）` 段，写：Patch N 摘要 + 终审结论是否需要修正（如补丁改变了 6.1 的结论/风险项，明确标注"原结论已过时，以补丁为准"）。**不覆盖原正文**——保证回读时能区分主流程结论与补丁演进
3. **刷新决策锚点**（`anchors_enabled != false` 时）：`.decision_anchors.json` 追加 `patch_summary`（本次 patch 一句话摘要）+ 刷新 `open_risks`（若复检有残留风险）。详见 [references/decision_anchors.md](../references/decision_anchors.md)
4. **输出**：`▶ 补丁 Patch {N} 完成（累计 {patch_count} 次追加修改）`

## 决策锚点（patch 完成后写）

若 `metadata.anchors_enabled != false`，按「强制操作」第 3 条刷新 `.decision_anchors.json`（追加 `patch_summary` + 刷新 `open_risks`），**保留**主流程已写字段（增量刷新，不覆盖）。详见 [references/decision_anchors.md](../references/decision_anchors.md)。

## MCP 推荐（v2.2 强证据二元化）

> **patch 是全场景开放步骤**——测试发现的问题可能涉及 UI 截图/视频证据、前端行为、第三方库、历史工单记忆等，**不预设任何 MCP 不适用**：除 sequential-thinking 必用外，其余全部 🟢*（满足强证据场景才必调，不满足自动降 ⚪ 无需声明）。各 MCP 的强证据场景见下方表。

| MCP | 推荐级别 | 用途（强证据场景） |
|-----|----------|------|
| sequential-thinking | 🟢 | 强制思考前置（执行前/阶段4 复检两次，至少 3 步） |
| serena | 🟢* | 符号定位 + 调用点追踪（阶段2 内嵌）——工程有可索引源码且需符号/引用查询时 |
| context7 | 🟢* | 涉及第三方库 API 时实时查库 |
| vision-bridge | 🟢* | 用户测试发现的问题带截图/视频证据（如 UI 异常图、设备视频），或 TB 缺陷源附件含媒体时 |
| playwright | 🟢* | 前端工程且补丁需浏览器行为验证时 |
| memory | 🟢* | 本工程历史工单数 ≥1 且新问题疑与历史工单/既有决策相关时 |
| cheap-research | 🟢* | **降本**：长产物压缩 / 现状摘要（阶段1 重审时可选，不接管决策） |

**强制约束（v2.2）**：🟢/🟢*/⚪ 语义 + 双保险机制详见 [SKILL.md「MCP 调用覆盖强制化」](../SKILL.md) + [references/mcp_per_step.md](../references/mcp_per_step.md)。

## 反偷懒机制

- **禁止跳过阶段1 直接改代码**（重审现状是严谨性第一道闸门，跳过 = 偷懒）
- **禁止不带增量计划就动手**（无计划 = 无约束，与主流程 plan 缺失同等违规）
- **禁止全文重读产物**（定点读 + 锚点是省 token 的正确姿势，全文重读 = 上下文爆炸之源）
- **禁止把会话记忆当现状依据**（只认磁盘：产物 + 代码 + git diff）
- **禁止修改主流程产物正文**（00_init/01_plan/03_plan_final/05_deepcheck 等只读；唯一可追加的是 06_audit.md 末尾「补丁记录」段——回读可区分）
- **禁止夹带计划外改动**（最小侵入，git diff 复核）
- **禁止在代码注释写 icode 元数据**（同 04_code 第 7 条）
- **禁止跳过编译/测试验证**（与 04_code 同规则，编译失败/测试失败必须如实记录 `patch_history.status="issues"`）

## 可重复执行

`/icode patch` **天然多轮可重复**：每次调用追加新的 `Patch N` 段（N 自增），`patch_count` / `patch_history` 累计。连续多轮补丁（改完测试又发现问题）不必新建工单——每轮补丁都独立记录，回读 `08_patch.md` + `06_audit.md` 补丁记录即得完整演进链。

**交付报告联动**：若该工单已生成过交付报告（`07_readme.md` 产物），本次 patch 涉及功能/修复范围变化时，提示用户"补丁后建议重新 `/icode readme` 刷新交付报告"（不自动执行，用户决定）。
