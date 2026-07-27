# 步骤 × MCP 推荐矩阵（v2.1+ 强制化）

> 每个 icode 步骤推荐使用的 MCP（含强证据 + 降级路径）。详见 [mcp_integration.md](mcp_integration.md)。

## 推荐级别语义（v2.1 强制化）

| 级别 | 符号 | 语义 | 触发条件 | 未调用的合规处理 |
|------|------|------|---------|-----------------|
| **必须调** | 🟢 | 强证据存在就**必须调用** | MCP 在 `~/.claude.json` 注册 且 tool 在 deferred tools 列表 | **降级声明**：在产物文件「MCP 调用记录」段写明降级原因（MCP 不可用 / LSP server 缺失 / 不适用场景）|
| **应该调** | 🟡 | 强证据存在就**应该调用**，除非明确不适用 | 同上 | **先尝试调用 + 思考块写明原因**：优先实际调用一次评估适用性；若明确不适用（如 CLI 工程的 playwright），在思考块「MCP 评估」段写明具体判断依据（如"工程类型不匹配"）才能跳过--v2.1.1 硬约束|
| **不必调** | ⚪ | 不在本步骤推荐范围 | — | 无需说明 |

## v2.0 → v2.1 变化（破兼容性变更）

**v2.0**（已弃用）：🟢 = "强证据优先"（推荐），🟡 = "视情况"（可选）
**v2.1**（当前）：🟢 = "必须调"（强制 + 降级声明），🟡 = "应该调"（思考块说明）

**为什么变**：实测发现 🟢🟡 的弱约束导致 AI 默认只调用 sequential-thinking（必用项），其他 MCP 全部跳过。强化为 🟢 = 强制 + 降级声明后，下次执行会被迫主动评估每个 MCP。

## 调用覆盖率强制化规则

1. **每个步骤产物文件**（01_plan.md / 02_review.md / 03_plan_final.md / 04_code_review_fix.md / 05_deepcheck.md / 06_audit.md / log_analysis.md / 00_init.md）必须含「MCP 调用记录」段
2. **每行**：MCP 名 + 实际调用结果（成功 / 降级 / 不适用）+ 证据
3. **缺此段 = 反偷懒第 21 条违规**，审计时拒收

| Step | sequential-thinking | vision-bridge | memory | context7 | playwright | serena |
|---|---|---|---|---|---|---|
| **0 init** | 🟢 | 🟡 | 🟡 | 🟢 | ⚪ | 🟡 |
| **0 log** | 🟢 | 🟡 | 🟡 | 🟡 | ⚪ | ⚪ |
| **doc** | 🟢 | 🟡 | 🟡 | 🟡 | ⚪ | 🟢 |
| **1 plan** | 🟢 | 🟡 | 🟡 | 🟢 | ⚪ | 🟢 |
| **2 review** | 🟢 | 🟡 | 🟡 | 🟡 | ⚪ | 🟡 |
| **3 merge** | 🟢 | ⚪ | ⚪ | ⚪ | ⚪ | ⚪ |
| **4 code** | 🟢 | 🟡 | 🟡 | 🟢 | ⚪ | 🟢 |
| **5 deepcheck** | 🟢 | 🟡 | 🟡 | ⚪ | 🟢 | 🟢 |
| **6 audit** | 🟢 | 🟢 | 🟡 | ⚪ | 🟢 | 🟡 |
| **7 readme** | 🟢 | 🟡 | ⚪ | ⚪ | ⚪ | ⚪ |
| **install** | 🟢 | ⚪ | ⚪ | ⚪ | ⚪ | ⚪ |
| **status / list** | 🟢 | ⚪ | ⚪ | ⚪ | ⚪ | ⚪ |

## 详细说明

### 0 init（需求初稿对话）

- **context7**：库调研（"用 React 19 还是 18？"）
- **vision-bridge**：识别用户给的设计图/截图
- **memory**：记录用户偏好（"上次他说 NoSQL"）

### 0 log（日志根因分析）

- **context7**：库 API 行为查证
- **vision-bridge**：错误截图分析

### doc（工程知识库生成）

- **serena**：理解代码结构（哪些是入口、哪些是 API、哪些是 IPC）—— **比 Read 精准 10 倍**
- **memory**：跨工程知识库经验

### 1 plan（拟定计划）

- **context7**：库 API 核对（"用 v3 还是 v2？"）
- **serena**：理解代码结构（哪些函数被谁调用）

### 2 review（审查）

- **serena**：依赖关系审查（"这个函数被哪些地方调用？"）

### 3 merge（合并审查意见）

- **sequential-thinking**：仅此（结构化合并审查点）

### 4 code（编码）

- **serena**：**game-changer**——按符号编辑、重命名引用追踪（vs Read+Grep 盲改）
- **context7**：实时查库 API（防训练知识过时）
- **memory**：项目特性（"用 gRPC v3 / API 路径前缀 /api/v2"）

### 5 deepcheck（复检）

- **playwright**：跑 E2E（前端项目）
- **serena**：找所有调用点评估影响

### 6 audit（终审）

- **playwright**：真实 UI 验证（截图、交互）
- **vision-bridge**：UI 截图分析

### 7 readme（交付报告）

- **vision-bridge**：附加 UI 截图

### install / status / list（独立步骤）

- **sequential-thinking**：仅此（其他 MCP 几乎无）
## 工程类型感知（v2.1.1 新增）

> 基于 demo-10 实测发现：vision-bridge/playwright 在 CLI 工程全标"不适用"但每次仍需评估，噪音大。本段按工程类型自动调整 MCP 推荐级别，减少"不适用"噪音。

### 工程类型探测

AI 在步骤 1 plan 开始时（或步骤 0 init）自动探测工程类型：

| 工程类型 | 探测信号 | 示例 |
|---------|---------|------|
| **CLI/后端** | 无前端文件（无 .html/.jsx/.tsx/.vue）；有 Makefile/Cargo.toml/go.mod/setup.py 等 | demo（C CLI）、纯后端服务 |
| **前端** | 含 .html/.jsx/.tsx/.vue 或 package.json 含 react/vue 等依赖 | React/Vue 工程 |
| **全栈** | 同时有前端 + 后端信号 | monorepo（含 web/ + server/） |
| **嵌入式/固件** | 有 .c/.h + Makefile/CMake + 无 main.c 入口（或 main.c 很小） | MCU 固件 |

### 推荐级别自动调整（覆盖上表默认推荐）

| MCP | 默认推荐 | CLI/后端 | 前端 | 全栈 | 嵌入式 |
|-----|---------|----------|------|------|--------|
| **playwright** | 步骤 5/6 🟢 | ⚪ 不必调 | 🟢 必须调 | 🟡 应该调 | ⚪ 不必调 |
| **vision-bridge** | 步骤 6 🟢 / 0/1 🟡 | 🟡 应该调（仅截图分析） | 🟢 必须调 | 🟢 必须调 | 🟡 应该调（仅截图分析） |
| **serena** | 步骤 1/4/5 🟢 | 🟢 必须调 | 🟢 必须调 | 🟢 必须调 | 🟢 必须调 |

**调整逻辑**：
- **CLI/后端**：playwright 降为 ⚪（无 UI 可测）；vision-bridge 降为 🟡（仅用户主动给截图时才用）
- **前端**：playwright 升为 🟢（E2E 必用）；vision-bridge 保持 🟢
- **全栈**：playwright 🟡（视前端模块大小）；vision-bridge 🟢
- **嵌入式**：playwright ⚪；vision-bridge 🟡（硬件截图/示波器截图分析）

### 工程类型记录

工程类型探测结果写入 `01_plan.md` §1.5 工程结构快照 + metadata `project_type` 字段（可选，缺失视为 "unknown" 走默认推荐）。

### 与 v2.1.1 "先尝试调用" 硬约束的关系

- ⚪ 不必调的 MCP（如 CLI 工程的 playwright）：无需尝试调用，直接标"工程类型不匹配"
- 🟢/🟡 的 MCP：仍需遵守 v2.1.1 硬约束（先尝试调用一次才能标"不适用"）
- 工程类型感知只是**降低噪音**，不是跳过调用的借口

### 降级路径

若工程类型探测失败（无明确信号），按默认推荐表执行（保守起见，playwright/vision-bridge 保持 🟢/🟡）。

## memory MCP 推荐级别细化（v2.1.1 新增）

> 基于 demo-10 实测：memory 在 6 步全标"不适用"但从未实际调用 read_graph。问题不在 memory 本身，而在推荐级别一刀切。本段按工单类型细化。

### memory 推荐级别按工单类型区分

| 工单类型 | memory 推荐级别 | 理由 |
|---------|----------------|------|
| **新工程首个工单** | ⚪ 不必调 | 全局索引无本工程历史工单，read_graph 必返空 |
| **已有历史工单的工程** | 🟡 应该调 | 跨工单偏好/项目特性可能有用 |
| **长期项目（>5 工单）** | 🟢 必须调 | 跨工单记忆价值高，必须 read_graph 查相关记忆 |
| **demo/验证工单** | ⚪ 不必调 | 验证性质，无跨工单记忆需求 |

### 工单类型探测

- **新工程首个工单**：`~/.claude/icode_data/index.json` 中本工程 `project_path` 的工单数 = 0
- **已有历史工单**：本工程工单数 1-5
- **长期项目**：本工程工单数 > 5
- **demo/验证工单**：`project_path` 含 `demo/` 或 `test/`

### 与 v2.1.1 "先尝试调用" 硬约束的关系

- ⚪ 不必调（新工程首个工单 / demo）：无需调用 read_graph，直接标"工单类型不匹配"
- 🟡 应该调（已有历史工单）：**先调用 read_graph 一次**，确认无相关记忆后才标"不适用"
- 🟢 必须调（长期项目）：**必须调用 read_graph**，且把查到的记忆纳入思考块

### 降级路径

若工单类型探测失败（无法读 index.json），按 🟡 应该调执行（保守起见，先尝试调用）。
