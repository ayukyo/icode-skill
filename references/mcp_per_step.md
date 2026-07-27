# 步骤 × MCP 推荐矩阵（v2.2 强证据二元化）

> 每个 icode 步骤推荐使用的 MCP。v2.2 起消除 🟡"应该调"模糊地带，改为二元：🟢 必须调（强证据场景满足）/ ⚪ 不必调（不评估）。详见 [mcp_integration.md](mcp_integration.md)。

## 推荐级别语义（v2.2 二元化）

| 级别 | 符号 | 语义 | 触发条件 | 未调用的合规处理 |
|------|------|------|---------|-----------------|
| **必须调** | 🟢 | 强证据场景满足就**必须调用**（先实际调用一次，失败/空才能降级） | 强证据场景满足（见下表）+ MCP 在 `~/.claude.json` 注册 且 tool 在 deferred tools 列表 | **降级声明**：在产物文件「MCP 调用记录」段写明降级原因（MCP 不可用 / LSP server 缺失 / 调用返回空）|
| **不必调** | ⚪ | 强证据场景不满足，**无需评估、无需声明** | 强证据场景不满足 | 无需说明 |

## v2.1 -> v2.2 变化（破兼容性变更）

**v2.1**（已弃用）：🟢 必须调 / 🟡 应该调 / ⚪ 不必调。🟡"应该调"语义模糊，AI 倾向"被动不调用"+ 补降级声明应付，实测仍只触发 sequential-thinking。
**v2.2**（当前）：🟢 必须调 / ⚪ 不必调。**消除 🟡**——强证据场景满足就 🟢 必调（执行步骤内嵌 + thinking_core gate 双保险），不满足就 ⚪ 完全不评估。

**为什么变**：v2.1 的 🟡 是降级口子——AI 把"应该调"解读为"可选"，配合"工程类型感知"段的降级表，5 个 MCP 全有合理降级理由，强制化变成"强制声明降级"。v2.2 二元化后，AI 没有模糊地带：要么必调，要么不评估。

## 强证据场景判定（v2.2 核心）

**判定时机**：每个步骤开始时（thinking_core gate + 执行步骤内嵌点），按以下场景判定每个 MCP 是否 🟢。**不满足强证据场景 = ⚪ = 不评估不声明**。

| MCP | 🟢 强证据场景（满足即必调） | ⚪ 否则 |
|-----|---------------------------|--------|
| **sequential-thinking** | 所有步骤（强制思考前置，已嵌入 thinking_core） | 无 |
| **serena** | plan/code/deepcheck/doc/review 步骤 **且** 工程有可索引源码（.py/.ts/.js/.jsx/.tsx/.vue/.c/.cpp/.h/.rs/.go/.java/.kt 等，非空非 demo-skeleton） | 其余步骤 / 无可索引源码 |
| **context7** | init/plan/code 步骤 **且** 需求或代码涉及第三方库（package.json/Cargo.toml/go.mod/requirements.txt/pom.xml/build.gradle 等声明依赖，且需求触及该库 API） | 其余步骤 / 不涉及第三方库 |
| **vision-bridge** | 任意步骤 **且** 用户主动提供了图片/截图/视频（会话中有媒体附件或路径） | 不给图永远不调 |
| **playwright** | deepcheck/audit 步骤 **且** 前端工程（含 .html/.jsx/.tsx/.vue 或 package.json 含 react/vue） | CLI/后端/嵌入式工程 |
| **memory** | init/plan 步骤 **且** 本工程历史工单数 ≥ 1（`~/.claude/icode_data/index.json` 中本 project_path 工单数 ≥ 1） | 新工程首个工单 / demo |

**判定执行**：
- serena/context7 的"可索引源码"/"第三方库"探测：步骤 1 plan 开始时 `ls` 顶层 + grep 依赖文件，结果写入 `01_plan.md` §1.5
- memory 的工单数探测：Read `~/.claude/icode_data/index.json` 按本工程 project_path 计数
- vision-bridge/playwright 的工程类型/媒体探测：按会话上下文 + 工程文件判定

## 推荐矩阵（v2.2 二元化）

> 矩阵只标默认推荐；实际执行按上方「强证据场景判定」动态判定。强证据场景不满足时，即使下表标 🟢 也降为 ⚪。

| Step | sequential-thinking | serena | context7 | vision-bridge | playwright | memory |
|---|---|---|---|---|---|---|
| **0 init** | 🟢 | ⚪ | 🟢* | 🟢* | ⚪ | 🟢* |
| **0 log** | 🟢 | ⚪ | 🟢* | 🟢* | ⚪ | 🟢* |
| **doc** | 🟢 | 🟢* | ⚪ | 🟢* | ⚪ | ⚪ |
| **1 plan** | 🟢 | 🟢* | 🟢* | 🟢* | ⚪ | 🟢* |
| **2 review** | 🟢 | 🟢* | ⚪ | 🟢* | ⚪ | ⚪ |
| **3 merge** | 🟢 | ⚪ | ⚪ | ⚪ | ⚪ | ⚪ |
| **4 code** | 🟢 | 🟢* | 🟢* | 🟢* | ⚪ | ⚪ |
| **5 deepcheck** | 🟢 | 🟢* | ⚪ | 🟢* | 🟢* | ⚪ |
| **6 audit** | 🟢 | ⚪ | ⚪ | 🟢* | 🟢* | ⚪ |
| **7 readme** | 🟢 | ⚪ | ⚪ | 🟢* | ⚪ | ⚪ |
| **install/status/list** | 🟢 | ⚪ | ⚪ | ⚪ | ⚪ | ⚪ |

`🟢*` = 默认 🟢，但实际需满足强证据场景才必调（不满足降为 ⚪，无需声明）。

## 详细说明（强证据场景下的用途）

### 0 init（需求初稿对话）
- **context7**：库调研（"用 React 19 还是 18？"）——仅需求涉及第三方库时
- **vision-bridge**：识别用户给的设计图/截图——仅用户给图时
- **memory**：read_graph 查跨工单偏好——仅本工程有历史工单时

### 0 log（日志根因分析）
- **context7**：库 API 行为查证——仅涉及第三方库行为时
- **vision-bridge**：错误截图分析——仅用户给截图时

### doc（工程知识库生成）
- **serena**：理解代码结构（入口/API/IPC）——有可索引源码时，**比 Read 精准 10 倍**
- **vision-bridge**：截图分析——仅用户给图时

### 1 plan（拟定计划）
- **serena**：理解代码结构（哪些函数被谁调用）——有可索引源码时（**执行步骤 5.0 内嵌**）
- **context7**：库 API 核对——仅涉及第三方库时
- **vision-bridge**：识别截图——仅用户给图时
- **memory**：read_graph 查跨工单记忆——仅有历史工单时

### 2 review（审查）
- **serena**：依赖关系审查（"这个函数被哪些地方调用？"）——有可索引源码时
- **vision-bridge**：截图分析——仅用户给图时

### 3 merge（合并审查意见）
- **sequential-thinking**：仅此（结构化合并审查点）

### 4 code（编码）
- **serena**：**game-changer**——按符号编辑、重命名引用追踪——有可索引源码时（**执行步骤内嵌**）
- **context7**：实时查库 API——仅涉及第三方库时
- **vision-bridge**：截图分析——仅用户给图时

### 5 deepcheck（复检）
- **serena**：找所有调用点评估 blast-radius——有可索引源码时（**执行步骤内嵌**）
- **playwright**：跑 E2E——仅前端工程时
- **vision-bridge**：截图分析——仅用户给图时

### 6 audit（终审）
- **playwright**：真实 UI 验证——仅前端工程时
- **vision-bridge**：UI 截图分析——仅用户给图时

### 7 readme（交付报告）
- **vision-bridge**：附加 UI 截图——仅用户给图时

### install / status / list
- **sequential-thinking**：仅此

## 调用覆盖率强制化规则（v2.2）

1. **每个步骤产物文件**（01_plan.md / 02_review.md / 03_plan_final.md / 04_code_review_fix.md / 05_deepcheck.md / 06_audit.md / log_analysis.md / 00_init.md）必须含「MCP 调用记录」段
2. **每行**：MCP 名 + 实际调用结果（成功 / 降级 / 不适用）+ 证据
3. **🟢 MCP 未实际调用**（含未先尝试调用就标降级）= 反偷懒第 21 条违规
4. **⚪ MCP 无需记录**（强证据场景不满足，不评估不声明）
5. **缺「MCP 调用记录」段** = 反偷懒第 21 条违规，审计时拒收

## 双保险机制（v2.2 新增）

🟢 MCP 由两层强制驱动，确保真实触发（治本"只触发 sequential-thinking"问题）：

1. **执行步骤内嵌**（A 层）：serena 在 plan/code/deepcheck/doc/review 的执行步骤主体里有独立的"第 N 步"调用指令（非末尾推荐表），AI 顺序执行必然走到——复制 sequential-thinking 的成功模式
2. **thinking_core MCP gate**（B 层）：强制思考前置流程里，思考块先列本步 🟢 MCP -> ToolSearch 取 schema -> 实际调用 -> 结果进思考块。覆盖 context7/memory/vision-bridge/playwright

两层任一触发即合规。serena 走 A 层（执行步骤内嵌），其余 🟢 MCP 走 B 层（thinking_core gate）。

**与 v2.1 的区别**：v2.1 只有末尾推荐表 + 产物记录段（形式强制），AI 可补声明应付；v2.2 把 🟢 MCP 写进执行步骤主体和思考流程（执行强制），AI 顺序执行必然真实调用。

## 降级路径

- **serena 不可用**（无 LSP / 未装）：Read + Grep 兜底，产物「MCP 调用记录」标"serena 降级-无 LSP，用 Read+Grep 替代"
- **context7 不可用**：WebFetch 官方文档兜底，标降级
- **vision-bridge 不可用**：用户自负原生多模态能力，标降级
- **playwright 不可用**：Bash + curl 兜底（无 JS 渲染），标降级
- **memory 不可用**：本对话手动笔记兜底，标降级

**降级不是错误，但必须显式声明**（先实际调用一次，失败/空才能标降级）。
