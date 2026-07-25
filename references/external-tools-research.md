# 外部工具调研笔记（非 iCode 集成入口）

> 本文件是 iCode 维护者对"是否有外部工具值得借鉴思想"的**调研结论沉淀**，**不是 iCode 集成入口**——iCode 主流程不依赖、不推荐、不安装、不绑定任何外部工具；本文件仅作未来决策参考。
>
> 三问过滤原则：① 真实问题？ ② 已有实现？ ③ 影响调用链？ 任一不确认就不动 iCode 现状。

## 类别一：代码智能 / 结构化图谱

### 调研目标

探讨"Tree-sitter + 图谱 + MCP"形态的工具是否能优化 iCode 步骤 4（编码）和步骤 5（复检）的上下文加载与 blast-radius 分析。

### 结论（在 iCode 内已用 Grep 等价物落地）

**借鉴思想**：CRG 的 `get_impact_radius`（caller/import/test 三链）+ `detect_changes`（风险打分 + 测试盲区）思想，已用 iCode 步骤 4「三链预扫」+ 步骤 5「blast-radius 三链自检」以纯 Grep 等价物落地（详见 [steps/04_code.md](../steps/04_code.md)「## 编码实施·准入」段 + [steps/05_deepcheck.md](../steps/05_deepcheck.md)「blast-radius 三链自检」段）。

**为什么不集成外部图谱工具**：

1. 集成需要安装新二进制、写 `.mcp.json`、改 `.claude/settings.json`、与 venv/PATH/重启 session 等多个坑（参见外部项目 TROUBLESHOOTING 第 1/2/4/5 条）
2. iCode 已有 Grep 等价物可覆盖 80% 场景，剩余 20%（动态分发/重载/类型推断）由 Claude 自身注意力兜底
3. 集成强依赖外部项目版本号演进，会污染 iCode 的"零依赖工作流"特性
4. 一旦集成失败，回滚退路复杂

**保留出口**（仅当未来用户主动要求时再评估）：

- 用户在工程内已自行安装外部图谱工具并自配置 MCP server：iCode 不拦截，模型端可主动调用，失败时降级到 Grep 三链预扫
- 不在 iCode 文档（SKILL.md / README.md）中显式推广、不在 prompt 模板中强制、不改 iCode 配置文件

### 调研备忘（供未来复核）

- 外部项目 benchmark 自报"`~82x` token 节省中位数（区间 38x ~ 528x）"——但与现实 agentic grep 相比折扣大；典型百万行级工程才有性价比，小工程反被 metadata 拖慢
- 外部项目成熟度判定 v2.x（PyPI 上有，CI 绿色，multi-language 30+），有真实用户案例
- 已知坑清单（TROUBLESHOOTING）：venv 内须重 install、install 后必须重启 session、hook schema 历史 bug、uvx/pipx PATH 依赖
- 维基类工具（CRG `generate_wiki_tool`）能生成 markdown wiki——但与 iCode `/icode doc` 的章节模板目标重叠，集成意义低

## 类别二：协议层 / 总线层工具

### 调研目标

是否有"统一语义压缩 / 通信建模"工具能降低 iCode `19 路订阅 + 13 路发布 + 9 路 client + 9 路 server` 类拓扑的人工盘点成本。

### 结论（沉淀为步骤 1 §1.5 工程结构快照）

iCode 不做"工具集成"，而是把这类需求沉淀到 [steps/01_plan.md](../steps/01_plan.md)「§1.5 工程结构快照」段，由模型端自动从工程文档（`~/.claude/icode_data/project_docs/`）或临时 Grep 兜底生成（详见 schema 迁移前置段）。这条路径是**项目自身元信息**驱动，**不依赖外部工具**。

## 类别三：IPC 优化器 / Build Cache / Dependency Analyzer

### 调研目标

评估是否有"增量编译缓存 / 依赖图谱"工具能加速 iCode 步骤 4 的编译验证阶段（最长 3 次重试的兜底逻辑）。

### 结论

iCode 不集成。原因：iCode 步骤 4「编译验证」已是项目自带 Make/CMake/Bazel 的薄壳调用，引入外部缓存工具属于"项目自身构建优化"范畴——超出 iCode 工作流边界。若用户工程已用 ccache/sccache/bazel，iCode 的 shell 调用天然受益（无需 iCode 改造）。

## 决策记录

| 时间 | 决定 | 触发原因 |
|------|------|----------|
| 2026-07-25 | 类别一不集成 | Grep 等价物已覆盖 + 集成成本高 + 零依赖原则 |
| 2026-07-25 | 类别一思想已用 Grep 落地（步骤 4 准入三链 + 步骤 5 blast-radius 三链） | 双方向调研结论一致 |
| 2026-07-25 | 类别二不集成 | 用步骤 1 §1.5 工程结构快照兜底 |
| 2026-07-25 | 类别三不集成 | 超出 iCode 工作流边界 |

## 复评触发条件

以下任一条件满足时，应复评本调研：

- iCode 用户群中 ≥ 30% 在 50+ 文件的中型工程上反复抱怨步骤 4/5 token/时间浪费
- 外部项目（如本调研第一类）出现稳定的"轻量本地等价物"且声誉良好
- iCode 工作流边界显著扩展（如支持新型工作流场景：架构评审 / 大规模迁移）

复评时本文件须整体复读、重新三问过滤、补/改对应调研结论，按"icode-skill 改 dev_repo only"原则只改 dev_repo，不主动同步到已安装目录。

---

> **本文档边界**：本调研笔记**仅供维护者决策时翻阅**。iCode 用户在跑 `/icode plan` / `/icode code` / `/icode deepcheck` 时**不应**被提示安装或考虑任何外部工具——任何"询问装不装"的语义都应被 iCode 工作流剔除。如发现 prompt 模板中出现此类引导，按"最小改动"原则删除并提交 issue。
