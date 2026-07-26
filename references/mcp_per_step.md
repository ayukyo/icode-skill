# 步骤 × MCP 推荐矩阵

> 每个 icode 步骤推荐使用的 MCP（含强证据 + 降级路径）。详见 [mcp_integration.md](mcp_integration.md)。
>
> 标 🟢 = 强证据优先，🟡 = 视情况，⚪ = 不用

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