# 廉价子代理调研（cheap-subagent research）

> **状态**：调研结论备查，未动代码（用户 2026-07-31 指示："先研究清楚"）。
>
> 触发问题：主会话用贵的强模型额度吃紧，能否让部分调研子代理用便宜弱模型，结果合并回主会话，同时不降低工作流能力。
>
> **用户决策已定**：
> - 走方案 A（Claude 家族分级）路径，等实测需要再升级方案 B（独立 MCP）
> - 分级策略：**白名单**（先严后宽）
> - 输出规约：**强制 schema**（结构化对齐）

---

## 结论摘要

**Claude Code 原生已支持子代理模型分级，无需新 MCP**：

```python
# Agent 工具直接传 model —— 零边际成本
Agent(prompt="...", subagent_type="Explore", model="haiku")

# Workflow 脚本中也支持
agent(prompt, {model: "haiku", agentType: "general-purpose"})
```

可选 `opus` / `sonnet` / `haiku` / `fable`。**MCP 是工具层，不是模型层**——配新 MCP 调便宜模型这条路只在"用非 Claude 模型"（GPT-mini / 本地 ollama）时才有边际价值。

---

## 现有能力盘点

| 能力 | 入口 | 适用场景 |
|------|------|---------|
| `Agent(model=...)` | 主会话直接调 | 单点调研，简单任务 |
| `Workflow` 脚本 `agent({model})` | 复杂编排 | 多步骤 pipeline，需显式分级 |
| `subagent_type` | Explore / general-purpose / Plan 等 | 任务类型匹配（弱→强） |
| `run_in_background` | 异步并行 | 多个调研任务并行 |

---

## 方案对比

### 方案 A：Claude 家族内部分级（Agent model=haiku）

- **改动量**：3~5 个 step 文件 + 1 个 reference（本文档后续落地）
- **实施成本**：~0.5 天
- **降本幅度**：1/3 ~ 1/10（视模型分级）
- **优势**：零边际成本、Claude-API 风格统一、调试简单
- **风险**：
  - haiku 在多步推理 / 复杂代码理解上能力下降
  - 主会话裁决工作量上升（弱模型调研结果需更强验证）
  - 需配合 icode 已有对抗验证模式（[adversarial.md](adversarial.md)）

### 方案 B：自建 cheap-research MCP（跨厂商模型）

- **改动量**：新建 `mcp/cheap-research/`（~15 文件）+ 集成到 `mcp/install.sh`
- **实施成本**：~2~3 天
- **降本幅度**：1/10 ~ 1/100（取决于本地 ollama vs 远程 API）
- **优势**：跨厂商模型自由度（GPT-4o-mini / Gemini-Flash / 本地 Ollama）
- **风险**：
  - 跨厂商模型语义差异（GPT-4o-mini 风格 vs Claude 风格 vs Gemini 风格）
  - 服务端依赖（API 配额、本地 ollama 资源）
  - 主会话合成压力（异构结果需 schema 对齐）
  - 与 icode 已有对抗验证模式兼容性需验证

### 方案 C：混合编排（先 A 后 B 兜底）

- 方案 A 处理默认场景（Claude 家族）
- 方案 B 通过 MCP 调用非 Claude 模型（特殊场景）
- 元数据 `metadata.subagent_model` 字段管理切换
- **推荐路径**：先用 A 验证分级价值，再决定是否上 B

---

## 白名单分级列表（初稿，待实测校准）

### ✅ 可降级 haiku 的子任务（机械、模式化、容错高）

| 步骤 | 任务 | 理由 |
|------|------|------|
| 2 review | 机械扫描：grep 模式匹配、文件列举、重复检测 | 不需深度推理 |
| 2 review | 引用追溯：找到所有 `调用 X` 的位置 | 工具可验证 |
| 4 code | 批量补全：缺失分支、错误码补全 | 模式化 |
| 4 code | 格式化：apply linter、调整 import 顺序 | 工具可验证 |
| 4 code | 死代码清理：未引用函数 / 注释清理 | 静态分析 |
| 5 deepcheck Reverse | 原文对比：与计划文件 diff | 工具可验证 |
| 7 readme | 模板填充：变更列表自动生成 | 模式化 |
| 跨步骤 | 跨工程工单查找（list.md） | 纯查询 |
| 跨步骤 | 状态摘要（status.md） | 纯查询 |

### ❌ 必须主会话模型（核心推理、决策、对抗）

| 步骤 | 任务 | 理由 |
|------|------|------|
| 0 init | 需求多轮对话 | 用户对话密集 |
| 1 plan | 架构决策、风险评估 | 复杂推理 |
| 2 review | 3 质疑者对抗验证（evidence/alternative/sufficiency） | 质疑深度 |
| 2 review | 审查合成（多轮 review 结果合并） | 决策 |
| 3 merge | 意见合并定稿 | 决策 |
| 4 code | 关键设计（如新算法、并发模型） | 推理 |
| 5 deepcheck Fixed | 修复方向论证 | 推理 |
| 5 deepcheck Free | 自由探索式质疑 | 推理 |
| 5 deepcheck A6 | 3 质疑者对抗（已强制） | 推理 |
| 6 audit | 终审裁决 + 统一修复 | 决策 |
| 7 readme | 交付报告风险章节提炼 | 推理 |

### ⚠️ 灰区（需实测决定）

| 步骤 | 任务 | 决策依据 |
|------|------|---------|
| 0 init | 链路图绘制（before/after） | 工具辅助可降级 |
| 2 review | 历史工单检索相似度匹配 | 模式匹配 |
| 4 code | 重构方案（局部） | 视复杂度 |
| 5 deepcheck Reverse | 缺陷分类 | 视自动化程度 |

---

## cheap-research MCP 设计方案（如未来实施）

按 [vision-bridge](../../mcp/vision-bridge/) 模式（已知工程模式）：

```
mcp/cheap-research/
├── server.py             # MCP 入口，复用 vision-bridge 骨架
├── providers/
│   ├── openai_compat.py  # GPT-4o-mini / Gemini-Flash / 本地 Ollama 兼容协议
│   ├── local_ollama.py   # 纯本地扩展
│   └── base.py           # 抽象基类
├── config.example.json   # provider + base_url + api_key + model 四件套
├── install.sh            # 复用 mcp/ 顶层安装流程
├── uninstall.sh
├── tests/
│   ├── test_openai_compat.py
│   └── test_local_ollama.py
└── README.md
```

### 工具 API 设计（强制 schema）

#### `mcp__cheap-research__research(query, context?, schema?, max_tokens?)`

```json
{
  "name": "research",
  "description": "调便宜模型做调研查询，返回结构化结果",
  "inputSchema": {
    "type": "object",
    "properties": {
      "query": {"type": "string", "description": "调研问题"},
      "context": {"type": "string", "description": "上下文（可选）"},
      "schema": {"type": "object", "description": "期望返回结构（JSON Schema）"},
      "max_tokens": {"type": "integer", "default": 2048}
    },
    "required": ["query"]
  }
}
```

返回：

```json
{
  "answer": "调研结果（按 schema 渲染）",
  "confidence": 0.85,
  "caveats": ["已知盲点 1", "..."],
  "tokens_used": 1234,
  "model": "gpt-4o-mini",
  "cost_estimated": 0.0003
}
```

#### `mcp__cheap-research__summarize(text, max_tokens?, focus?)`

```json
{
  "name": "summarize",
  "description": "调便宜模型做摘要",
  "inputSchema": {
    "type": "object",
    "properties": {
      "text": {"type": "string"},
      "max_tokens": {"type": "integer", "default": 512},
      "focus": {"type": "string", "description": "聚焦角度（如 '风险' / '改动'）"}
    },
    "required": ["text"]
  }
}
```

### 优雅降级

- cheap-research MCP 未装 → 自动回退 `Agent(model="haiku")`（方案 A）
- 提供商 API 失败 → 返回 `{answer: null, error: "..."}`，主会话裁决
- schema 校验失败 → 返回 `{answer: null, error: "schema mismatch"}`

---

## 决策点（已定 / 未决）

### ✅ 已定

- 走方案 A 路径（暂不建 new MCP）
- 白名单策略（先严后宽）
- 强制 schema 结构化输出
- **不动代码**（用户指示，先研究清楚）

### ❓ 未决（待实测决定）

- haiku 子代理是否引入 3 质疑者对抗验证前的预筛选？
- haiku 调研结果如何标注可信度？（如 `confidence` 字段 + 置信阈值）
- 是否需要 `metadata.subagent_model` 与 `mode: full/fast` 并列？

---

## 未来实施路径（备忘）

### 阶段 1：方案 A 落地（如果启动）

1. 在 `~/.claude/icode_data/_global/subagent_model.json` 存默认配置
2. 修改 `steps/02_review.md`、`04_code.md`、`05_deepcheck.md`、`07_readme.md` 引用本文档白名单
3. 新增 `references/model-strategy.md`（本文档的精简版，嵌入到 SKILL.md）
4. 跑 1~2 个工单做 A/B 对照（baseline 全 Sonnet vs 分级版）
5. 评估降本幅度 + 质量影响

### 阶段 2：方案 B 升级（如果阶段 1 价值验证 + 用户有非 Claude 模型需求）

1. 复制 `mcp/vision-bridge/` 骨架 → `mcp/cheap-research/`
2. 实现 `providers/openai_compat.py`（复用 OpenAI 兼容协议）
3. 实现 `server.py` + 两个工具（research / summarize）
4. 写 `install.sh` + 注册到 `~/.claude.json`
5. 在 `mcp_per_step.md` 注册 + 强证据判定
6. 阶段 1 的 Agent(model=haiku) 改用 MCP 工具调用

---

## 兼容性 + 风险

### 兼容性

- 不破坏现有 6 大 MCP（serena/context7/playwright/memory/sequential-thinking/vision-bridge）
- 不改对抗验证模式（[adversarial.md](adversarial.md)）
- 不改工单 metadata schema（仅追加可选字段）
- 不改已安装目录（[dev_repo only](https://...),见 [mcp_integration.md](mcp_integration.md) 降级模式）

### 风险

- **R1**：haiku 调研质量下降 → 缓解：白名单管控 + 对抗验证兜底
- **R2**：跨模型语义差异（方案 B） → 缓解：强制 schema 对齐
- **R3**：本地 ollama 资源占用（方案 B） → 缓解：限流 + 用户自管
- **R4**：主会话裁决工作量上升 → 缓解：子代理输出加 confidence 字段，阈值以下主动质疑

---

## 引用

- [mcp_integration.md](mcp_integration.md) — 现有 MCP 集成与降级模式
- [adversarial.md](adversarial.md) — 3 质疑者对抗验证模式
- [thinking_core.md](thinking_core.md) — 强证据 + 降级判定逻辑
- [../../mcp/vision-bridge/](../../mcp/vision-bridge/) — 新 MCP 工程模式参考
- [mcp_per_step.md](mcp_per_step.md) — 步骤 × MCP 推荐矩阵

---

## 高回报分流清单（cheap-research 甜点）

> 用户 2026-07-31 讨论提炼："只把不影响工作流能力的分给它，列出高回报的分流工作"。
>
> 核心洞察：**cheap-research 的真正价值不是"代替推理"，而是"代替搬运"**——把"长上下文压缩、信息提取、模式匹配"这类搬运工种从主会话剥离出来，原本吃 token 最多的"读很多东西"工作被便宜模型接管。

### 5 维评分模型

| 维度 | 分数 | 含义 |
|------|------|------|
| 能力敏感度 | 低/中/高 | 弱模型能不能搞定？敏感度高 = 强模型必须 |
| token 占用 | 低/中/高 | 原本主会话做这些消耗多少？省得多 = 高回报 |
| 频次 | 低/中/高 | 每工单触发次数？频次高 = 复利高 |
| 错误成本 | 低/中/高 | 出错对工作流影响？成本低 = 敢降级 |
| 可验证性 | 有/无 | 主会话能否独立验证？可验证 = 敢降级 |

**高回报 = 能力敏感度低 × token 高 × 频次高 × 错误成本低 × 可验证**

### 跨步骤通用甜点（最强 5 ★）

| 工种 | 现位置 | 敏感度 | token | 频次 | 错误成本 | 可验证 | 评分 |
|------|--------|--------|-------|------|----------|--------|------|
| **长上下文压缩** | 跨步骤通用 | 低 | 高 | 高 | 低 | 弱 | ★★★★★ |
| **跨工程工单检索** | list.md / init 自动注入 | 低 | 中 | 高 | 低 | ✅ | ★★★★★ |
| **历史工单相似度匹配** | init / plan / start / log 自动注入 | 低 | 中 | 高 | 低 | ✅ | ★★★★ |
| **结构化信息提取** | doc.md 代码事实审计 | 中 | 中 | 中 | 中 | ✅ | ★★★★ |
| **模板化生成** | readme / 变更列表 | 低 | 中 | 中 | 低 | ✅ | ★★★★ |

### 按 7 步分阶段清单

#### 步骤 0 init（需求初稿对话）

| 子任务 | 评分 | 备选 MCP 工具 |
|--------|------|---------------|
| 历史工单相似匹配 | ★★★★ | `mcp__cheap-research__retrieve_similar({query, k=5})` |
| 链路图描述生成（before/after） | ★★★ | `mcp__cheap-research__describe_link({graph_struct})` |
| 需求要点抽取 | ★★★ | `mcp__cheap-research__extract({text, schema=需求要点})` |

#### 步骤 1 plan（架构设计）

| 子任务 | 评分 | 备选 MCP 工具 |
|--------|------|---------------|
| 跨工程代码事实审计 | ★★★★ | `mcp__cheap-research__audit_facts({repo_path, focus})` |
| 历史 ADR 检索 | ★★★★ | `mcp__cheap-research__retrieve_adr({topic})` |
| 接口误用预审（机械化） | ★★★ | `mcp__cheap-research__scan_misuse({api_definition})` |
| 端到端路径推演表（机械化） | ★★★ | `mcp__cheap-research__trace_path({entry, depth})` |
| 风险点列举 | ❌ | 必须主会话（推理敏感） |

#### 步骤 2 review（多轮审查）

| 子任务 | 评分 | 备选 MCP 工具 |
|--------|------|---------------|
| 历史相似 issue 检索 | ★★★★ | `mcp__cheap-research__retrieve_similar({issue})` |
| grep 模式扫描 | ★★★ | `mcp__cheap-research__scan_patterns({regex_list})` |
| 引用追溯（找 X 调用） | ★★★ | `mcp__cheap-research__trace_refs({symbol})` |
| 3 质疑者对抗验证 | ❌ | 必须主会话（质疑深度） |
| 审查意见合成 | ❌ | 必须主会话（决策） |

#### 步骤 3 merge（合并定稿）

| 子任务 | 评分 | 备选 MCP 工具 |
|--------|------|---------------|
| 重复意见合并 | ★★ | `mcp__cheap-research__dedupe({opinions})` |
| 关键决策 | ❌ | 必须主会话 |

#### 步骤 4 code（编码实施）

| 子任务 | 评分 | 备选 MCP 工具 |
|--------|------|---------------|
| 死代码清理 | ★★★ | `mcp__cheap-research__find_dead({code})` |
| 批量补全（缺失分支） | ★★★ | `mcp__cheap-research__fill_branches({func_bodies})` |
| 格式化 / import 排序 | ★★★ | `mcp__cheap-research__format({code, style})` |
| 关键算法 / 并发模型 | ❌ | 必须主会话 |

#### 步骤 5 deepcheck（三阶段递进）

| 子任务 | 评分 | 备选 MCP 工具 |
|--------|------|---------------|
| Reverse 阶段原文对比 | ★★★ | `mcp__cheap-research__diff_summary({a, b})` |
| 阶段摘要压缩 | ★★★ | `mcp__cheap-research__summarize({text, focus})` |
| Fixed / Free 阶段深度分析 | ❌ | 必须主会话 |
| 3 质疑者对抗 | ❌ | 必须主会话 |

#### 步骤 6 audit（终审）

| 子任务 | 评分 | 备选 MCP 工具 |
|--------|------|---------------|
| 6 维度报告生成（结构化） | ★★★ | `mcp__cheap-research__generate_report({findings, schema=审计6维})` |
| 终审裁决 | ❌ | 必须主会话 |

#### 步骤 7 readme（交付报告）

| 子任务 | 评分 | 备选 MCP 工具 |
|--------|------|---------------|
| 模板填充（变更列表） | ★★★ | `mcp__cheap-research__fill_template({template, data})` |
| 风险章节提炼 | ★★ | 推理敏感度中等，灰区 |

### 工种封装（cheap-research 工具 API 精细化）

按"高回报工种"显式封装工具，主会话调用粒度即工种粒度：

```python
# 核心：长上下文压缩（最强甜点）
mcp__cheap-research__summarize(text, max_tokens=512, focus=可选)
# → 跨步骤通用，从长文档/long log/全工程代码摘要抽取关键信息

# 检索类（高频）
mcp__cheap-research__retrieve_similar(query, k=5, source=可选)
# → 历史工单相似度匹配（init/plan/log 自动注入）

mcp__cheap-research__trace_refs(symbol, scope=全工程)
# → 符号引用追溯

# 模式匹配（机械化）
mcp__cheap-research__scan_patterns(regex_list, files=可选)
# → 批量 grep，结果结构化

# 提取类（结构化）
mcp__cheap-research__extract(text, schema=JSON_SCHEMA)
# → 按 schema 抽取信息（事实审计、风险点列举）

# 模板生成（模式化）
mcp__cheap-research__fill_template(template, data)
# → 模板填充（readme、变更列表、报告）

# 辅助
mcp__cheap-research__diff_summary(a, b, focus=关键变更)
mcp__cheap-research__dedupe(items, threshold=0.85)
mcp__cheap-research__format(code, style=prettier|black)
mcp__cheap-research__find_dead(code, language=python|cpp)
mcp__cheap-research__fill_branches(func_bodies, lang)
```

主会话侧用法：

```python
# 旧：主会话自己压缩长文
summary = "（这里主会话读 5000 字 log 输出 200 字摘要）"

# 新：调 cheap-research，token 消耗极低
mcp__cheap-research__summarize(
    text=long_log,
    max_tokens=200,
    focus="异常根因"
)
# 返回：{"answer": "...", "confidence": 0.85, "model": "gpt-4o-mini", "cost": 0.0003}
```

### 关键架构原则

1. **工具粒度 = 工种粒度**：一个工具 = 一个工种（如 `_summarize` / `_retrieve_similar`），不是"通用 LLM 调用"
2. **主会话不感知模型细节**：工具名是"做什么"，不是"用什么模型"
3. **强制 schema 入出**：所有返回结构化（避免异构模型风格漂移）
4. **不接管推理**：保留 adversarial / 决策 / 风险评估给主会话
5. **优雅降级**：未装 cheap-research → Agent(model=haiku) 兜底

### 量化预估（粗算，待实测校准）

| 工种 | 单次省 token | 频次/工单 | 单工单节省 |
|------|-------------|-----------|-----------|
| 长上下文压缩 | ~3000 | ~10 次 | ~30k tokens |
| 历史检索 | ~500 | ~5 次 | ~2.5k tokens |
| 结构化提取 | ~800 | ~3 次 | ~2.4k tokens |
| 模板生成 | ~600 | ~4 次 | ~2.4k tokens |
| **合计** | — | — | **~37k tokens/工单** |

按 Claude Opus 价格计算，约节省 $1.1/工单；按 GPT-4o-mini 计算约 $0.05/工单。**降本幅度 90%+**。

> **估算过于乐观**：实际还需考虑 MCP 自身 prompt 开销、序列化成本、主会话裁决成本。**实测为准**，A/B 对照后再校准。

---

## 更新日志

- **2026-07-31** 初稿：核心结论 + 方案对比 + 白名单分级
- **2026-07-31** v0.2：新增"高回报分流清单"（5 维评分 + 7 步逐项 + 工种封装 API + 量化预估）
- **2026-07-31** v0.3：完整逐项评分（按入口：init / log / doc / readme / start / fast / 1~6）

---

## 完整逐项评分（按入口 · v0.3）

> 用户 2026-07-31 深入研究指示：把 init / log / doc / readme / start / fast / 步骤 1~6 全部子任务穷尽，按 5 维评分。
>
> 评分图例：✓ 必分流（高回报）/ ⚠ 灰区（主会话复核）/ ✗ 不分（敏感度高）

### 步骤 0 init（需求初稿对话）

| 子任务 | 评分 | 理由 | MCP 工具 |
|--------|------|------|----------|
| 现状盘点（读工程结构） | ✓ | 长上下文压缩 | `summarize` |
| 链路图绘制（before/after） | ⚠ | 模板化但需主会话裁剪 | `describe_link` |
| 需求点抽取（多轮对话） | ✗ | 推理敏感 | — |
| 4 维度验证清单生成 | ✗ | 推理敏感 | — |
| 历史工单匹配（自动注入） | ✓ | 高频检索 | `retrieve_similar` |
| 强证据判定（steering/rules） | ✗ | 必须主会话 | — |

### 步骤 1 plan（项目计划）

| 子任务 | 评分 | 理由 | MCP 工具 |
|--------|------|------|----------|
| schema 迁移（自动/幂等） | ✓ | 工具可验证 | `apply_migration` |
| 接口误用预审 | ⚠ | 模式化但需上下文 | `scan_misuse` |
| 端到端路径推演表 | ⚠ | 模式化但需串联判断 | `trace_path` |
| 4 维度设计态固化 | ✗ | 架构决策 | — |
| 风险评估 | ✗ | 推理敏感 | — |
| 历史 ADR 检索（自动注入） | ✓ | 高频检索 | `retrieve_adr` |
| 跨工程代码事实审计 | ✓ | 长上下文压缩 | `audit_facts` |

### 步骤 2 review（多轮审查）

| 子任务 | 评分 | 理由 | MCP 工具 |
|--------|------|------|----------|
| 首轮审查（通读文件） | ⚠ | 模式化但需理解 | `extract` |
| 第 N 轮增量审查 | ✓ | diff 摘要 | `diff_summary` |
| 维度结果（结构化模板） | ✓ | 模板填充 | `fill_template` |
| **3 质疑者对抗验证** | ✗ | 必须主会话（质疑深度） | — |
| 历史相似 issue 检索 | ✓ | 高频检索 | `retrieve_similar` |
| grep 模式扫描 | ✓ | 机械匹配 | `scan_patterns` |
| 引用追溯 | ✓ | 符号检索 | `trace_refs` |
| 审查意见合成 | ✗ | 必须主会话（决策） | — |

### 步骤 3 merge（合并定稿）

| 子任务 | 评分 | 理由 | MCP 工具 |
|--------|------|------|----------|
| 重复意见合并 | ✓ | 模式匹配 | `dedupe` |
| 关键决策（区分类别） | ✗ | 必须主会话 | — |
| 定稿自检 | ✗ | 决策 | — |

### 步骤 4 code（编码实施）

| 子任务 | 评分 | 理由 | MCP 工具 |
|--------|------|------|----------|
| schema 迁移（实施态） | ✓ | 工具可验证 | `apply_migration` |
| 编码实施（关键设计） | ✗ | 推理敏感 | — |
| 死代码清理 | ✓ | 静态分析 | `find_dead` |
| 批量补全（缺失分支） | ✓ | 模式化 | `fill_branches` |
| 格式化 / import 排序 | ✓ | 工具可验证 | `format` |
| Code Review Fix 4 维度复检 | ✗ | 必须主会话 | — |

### 步骤 5 deepcheck（三阶段递进）

| 子任务 | 评分 | 理由 | MCP 工具 |
|--------|------|------|----------|
| Reverse 阶段原文对比 | ✓ | 工具可验证 | `diff_summary` |
| 阶段摘要压缩 | ✓ | 长上下文压缩 | `summarize` |
| Fixed 阶段（固定维度） | ⚠ | 模板化但需理解 | `extract` |
| Free 阶段（自由探索） | ✗ | 必须主会话 | — |
| **3 质疑者对抗（A6）** | ✗ | 必须主会话（质疑深度） | — |
| 阶段自检 | ✗ | 决策 | — |

### 步骤 6 audit（终审）

| 子任务 | 评分 | 理由 | MCP 工具 |
|--------|------|------|----------|
| 6 维度出具终审报告 | ⚠ | 模板化但需综合判断 | `generate_report` |
| 6.2 强制修复 | ✗ | 决策 | — |
| 6.3 最终交付 | ✗ | 决策 | — |
| 6.4 交付报告提示 | ✓ | 模板填充 | `fill_template` |
| schema 状态汇总 | ✓ | 工具可验证 | `summarize` |
| 实现偏差备忘（回溯标注） | ✓ | 模板填充 | `fill_template` |

### 步骤 7 readme（交付报告）

| 子任务 | 评分 | 理由 | MCP 工具 |
|--------|------|------|----------|
| 文件名生成 | ✓ | 模板化 | `generate_filename` |
| 智能模板选择（功能开发 / 查BUG） | ✓ | 模式匹配 | `select_template` |
| 模板填充（7 个段落） | ✓ | 模板填充 | `fill_template` |
| 风险章节提炼 | ⚠ | 推理敏感度中等 | `summarize` + 主会话筛 |
| 已知限制（避免重复 BUG） | ✓ | 检索 | `retrieve_similar` |
| 自包含约束检查 | ⚠ | 模式化但需判断 | `extract` |

### log（日志根因分析）

| 子任务 | 评分 | 理由 | MCP 工具 |
|--------|------|------|----------|
| 阶段 0 输入采集（零散信息） | ✓ | 摘要压缩 | `summarize` |
| 阶段 1 基线检查（git diff） | ✓ | diff 摘要 | `diff_summary` |
| 阶段 2 日志侦察 | ✓ | 长上下文压缩 | `summarize` |
| 阶段 3 链路图分析 | ⚠ | 模式化但需上下游 | `summarize` |
| 阶段 4 根因假设 | ✗ | 推理敏感 | — |
| 阶段 5 增量精确化 | ⚠ | 摘要压缩 | `summarize` |
| 阶段 6+7 对抗分析 | ✗ | 推理敏感 | — |
| 阶段 8 修复建议 | ✗ | 决策 | — |
| 8.6 自动 memory 沉淀建议 | ✓ | 模板化 | `extract` |
| TB 缺陷源拉取 | ✓ | 远程检索 | `fetch_tb` |
| TB 评论等回复 | ✓ | 模板填充 | `fill_template` |
| TB 附件分析 + ffmpeg 抽帧 | ✓ | 多模态 | vision-bridge 已承担 |
| 追问机制 | ✗ | 必须主会话 | — |

### doc（工程级知识库生成）

| 子任务 | 评分 | 理由 | MCP 工具 |
|--------|------|------|----------|
| project_id 解析 | ✓ | 模式匹配 | `parse_project_id` |
| 意图识别（去参数化） | ⚠ | 推理敏感度中等 | `classify_intent` |
| 模块检测（6 级优先级） | ✓ | 模式匹配 | `scan_modules` |
| 代码特征扫描 | ✓ | 长上下文压缩 | `summarize` |
| 增量判定 | ⚠ | 模式化但需上下文 | `diff_summary` |
| 强制思考前置 | ✗ | 必须主会话 | — |
| 质量审视与模板版本迁移 | ✓ | 模板化 | `apply_migration` |
| 正常生成（按章节） | ✓ | 模板填充 | `fill_template` |
| 99_code_facts_audit（事实审计） | ✓ | 长上下文压缩 | `audit_facts` |
| 进度输出（阶段级） | ✓ | 模板化 | `fill_template` |
| 主动 stale 扫描 | ✓ | 工具可验证 | `scan_patterns` |

### start（full 模式串联）

| 子任务 | 评分 | 理由 | MCP 工具 |
|--------|------|------|----------|
| 目录决策（复用/新建） | ✗ | 必须主会话（含用户交互） | — |
| 历史检索（自动注入） | ✓ | 高频检索 | `retrieve_similar` |
| 串联执行步骤 1~6 | — | 嵌套子任务 | — |

### fast（精简全流程）

| 子任务 | 评分 | 理由 | MCP 工具 |
|--------|------|------|----------|
| 目录决策 | ✗ | 必须主会话 | — |
| 历史检索 | ✓ | 高频检索 | `retrieve_similar` |
| 入口警告（用户自负其责） | ✗ | 必须主会话 | — |
| 串联执行（plan → review 1 轮 → merge → code → deepcheck Reverse → audit） | — | 嵌套子任务 | — |

### list（跨工程工单查询）

| 子任务 | 评分 | 理由 | MCP 工具 |
|--------|------|------|----------|
| 全量读取 index.json | ✓ | 纯查询 | 内置 |
| 关键词搜索 | ✓ | 模式匹配 | `scan_patterns` |
| 多过滤（project/status/since/limit） | ✓ | 工具可验证 | 内置 |
| 表格化输出 | ✓ | 模板填充 | `fill_template` |

### status（工单状态查询）

| 子任务 | 评分 | 理由 | MCP 工具 |
|--------|------|------|----------|
| 状态查询（默认只读） | ✓ | 纯查询 | 内置 |
| verdict 标注 | ✗ | 必须主会话（决策） | — |
| scan-verdict 批量扫描 | ✓ | 模式匹配 | `extract` |

---

## 入口 × 工种热度矩阵（v0.3 新增）

| 入口 \\ 工种 | summarize | retrieve_similar | extract | audit_facts | fill_template | scan_patterns | diff_summary | trace_refs | find_dead | format |
|--------------|-----------|------------------|---------|-------------|---------------|---------------|--------------|------------|-----------|--------|
| init         | ★★★★     | ★★★★            | ★       | ★★          | ★★            | ★             | ★            | ★          | —         | —      |
| plan         | ★★★       | ★★★              | ★       | ★★★★        | ★             | ★             | ★            | ★          | —         | —      |
| review       | ★★        | ★★★              | ★★      | ★           | ★★            | ★★★           | ★★           | ★★★        | —         | —      |
| merge        | ★         | ★                | ★       | —           | ★             | ★             | ★            | ★          | —         | —      |
| code         | ★         | ★                | ★       | —           | ★             | ★             | ★            | ★          | ★★        | ★★     |
| deepcheck    | ★★★       | ★                | ★       | ★           | ★             | ★             | ★★★          | ★          | ★         | —      |
| audit        | ★★        | ★                | ★       | ★           | ★★            | ★             | ★            | ★          | —         | —      |
| readme       | ★★        | ★★               | ★★      | ★           | ★★★★          | ★             | ★            | ★          | —         | —      |
| log          | ★★★★★     | ★★★              | ★★      | ★★          | ★             | ★★            | ★★           | ★          | —         | —      |
| doc          | ★★★       | ★                | ★★★     | ★★★★        | ★★            | ★★            | ★★           | ★          | —         | —      |
| start        | ★★        | ★★★★            | ★       | ★★          | ★             | ★             | ★            | ★          | —         | —      |
| fast         | ★         | ★★★              | ★       | ★           | ★             | ★             | ★            | ★          | —         | —      |
| list         | —         | ★★               | ★       | —           | ★             | ★             | —            | —          | —         | —      |
| status       | ★         | ★                | ★       | —           | ★             | ★             | —            | —          | —         | —      |

**矩阵判读**：
- **最热甜点**：log × summarize（长 log 根因分析必读）
- **次热**：doc × audit_facts、readme × fill_template、init/log/start × retrieve_similar
- **冷门**：merge / fast（步骤短、子任务少）

---

## 工种工具映射（v0.3 完整）

按热度矩阵反推 cheap-research MCP 工具清单（按实现优先级排序）：

```python
# Tier 1：必装（覆盖 80% 高回报场景）
"research":      mcp__cheap-research__retrieve_similar(query, k=5, source)
"summarize":     mcp__cheap-research__summarize(text, max_tokens=512, focus=可选)
"audit_facts":   mcp__cheap-research__audit_facts(repo_path, focus=可选)
"extract":       mcp__cheap-research__extract(text, schema=JSON_SCHEMA)
"fill_template": mcp__cheap-research__fill_template(template, data)

# Tier 2：增强（机械匹配 / 检索）
"scan_patterns": mcp__cheap-research__scan_patterns(regex_list, files=可选)
"trace_refs":    mcp__cheap-research__trace_refs(symbol, scope=全工程)
"diff_summary":  mcp__cheap-research__diff_summary(a, b, focus=关键变更)
"dedupe":        mcp__cheap-research__dedupe(items, threshold=0.85)

# Tier 3：辅助（特定场景）
"scan_misuse":   mcp__cheap-research__scan_misuse(api_definition)
"trace_path":    mcp__cheap-research__trace_path(entry, depth)
"describe_link": mcp__cheap-research__describe_link(graph_struct)
"select_template": mcp__cheap-research__select_template(context)
"generate_filename": mcp__cheap-research__generate_filename(context)
"parse_project_id": mcp__cheap-research__parse_project_id(repo_path)
"scan_modules":  mcp__cheap-research__scan_modules(repo_path)
"fetch_remote":  mcp__cheap-research__fetch_remote(url, type=TB|general)

# Tier 4：代码操作（code 步骤专用）
"find_dead":     mcp__cheap-research__find_dead(code, language=python|cpp)
"fill_branches": mcp__cheap-research__fill_branches(func_bodies, lang)
"format":        mcp__cheap-research__format(code, style=prettier|black)
"apply_migration": mcp__cheap-research__apply_migration(schema_diff)
```

---

## 关键洞察（v0.3 总结）

1. **MCP 价值密度不均**：
   - log / doc / readme 三个入口价值最高（长上下文 + 模板化）
   - merge / fast 价值最低（步骤短、子任务少）
   - init / log / start / fast 自动注入点是"高频复用"的关键场景

2. **工种聚合**：20+ 个子任务映射到 ~18 个 MCP 工具，说明**工种聚合有效**（不是每个子任务一个工具）

3. **三大甜点明确**：
   - **长上下文压缩**（summarize）覆盖 log / doc / init / deepcheck
   - **历史检索**（retrieve_similar）覆盖 init / plan / review / readme / log / start / fast
   - **模板填充**（fill_template）覆盖 readme / doc / audit / status

4. **必须主会话的"硬核"工作**：
   - 3 质疑者对抗验证（review / deepcheck / log）
   - 架构决策（plan / code 关键设计）
   - 终审裁决（audit）
   - 修复方案（log / code）
   - 用户对话（init / start / fast 决策点）

5. **实施优先级（建议）**：
   - 阶段 1a：先实现 Tier 1（5 个工具）→ 覆盖 80% 价值
   - 阶段 1b：补 Tier 2（4 个工具）→ 覆盖 95% 价值
   - 阶段 2a：补 Tier 3（按需）→ 边际价值
   - 阶段 2b：补 Tier 4（代码操作）→ 特定场景

6. **设计原则**：
   - 工具粒度 = 工种粒度（一个工具 = 一个工种）
   - 主会话不感知模型细节（工具名是"做什么"不是"用什么模型"）
   - 强制 schema 入出（避免异构模型风格漂移）
   - 不接管推理（保留 adversarial / 决策 / 风险评估）
   - 优雅降级（cheap-research 未装 → Agent(model=haiku) 兜底）

---

## 子代理 + MCP 协同架构（v0.4 新增）

> 用户 2026-07-31 关键追问：子代理之前会用到现有 MCP（serena / context7 / vision-bridge / playwright / memory / sequential-thinking），如果用 cheap-research MCP，**子代理还能继续用别的 MCP 配合吗**？

### 简短回答

**✅ 完全可以。** Claude Code 默认行为：general-purpose 子代理继承主会话所有 MCP 工具。cheap-research 与其它 6 大 MCP 是**平铺关系**（不是嵌套），子代理就是"组合者"。

### MCP 工具可达性矩阵

| 子代理类型 | cheap-research | serena | vision-bridge | playwright | context7 | memory | sequential-thinking |
|------------|----------------|--------|---------------|------------|----------|--------|---------------------|
| general-purpose（Tools: *） | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Explore（只读） | ✅ | ✅（只读工具） | ✅ | ✅ | ✅ | ✅ | ✅ |
| Plan（只读） | ✅ | ✅（只读工具） | ✅ | ✅ | ✅ | ✅ | ✅ |
| claude-code-guide | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |

**关键事实**：
- general-purpose 子代理继承主会话所有工具（含所有 MCP）
- Explore / Plan 子代理限制 Edit/Write，但仍可调 MCP 的只读工具
- **MCP 工具是平铺暴露给子代理**——不是嵌套、不是隔离

### 子代理组合的"六边形"

```
                ┌─────────────────┐
                │   Playwright    │ ← 网页交互 / 实时数据
                └────────┬────────┘
                         │
   ┌──────────┐    ┌─────┴─────┐    ┌──────────────────┐
   │ context7 │ ←→ │  子代理 LLM │ ←→ │  vision-bridge   │
   │  外部文档 │    │  (主会话/子 │    │   多模态理解      │
   └──────────┘    │  代理模型) │    └──────────────────┘
                   └─────┬─────┘
                         │
        ┌────────────────┼────────────────┐
        │                │                │
   ┌────┴─────┐    ┌─────┴─────┐    ┌─────┴─────┐
   │  serena  │    │   cheap   │    │  memory   │
   │ 符号/引用│    │  -research│    │ 实体关系  │
   │  精确查询 │    │ 便宜 LLM  │    │  知识图谱  │
   └──────────┘    │  推理    │    └───────────┘
                   └──────────┘
                              ↑
                       新增维度
```

### 联动示例（实际场景）

**场景 1：审查 5 个新代码文件**

主会话原本（baseline）：
- 主会话自己读 5 文件 + 总结 → 吃大量 Opus token

cheap-research 协同后：
1. **子代理**用 serena 拿文件骨架（`find_symbol` 5 次）→ 精确、不耗 token
2. **子代理**用 cheap-research 摘要每文件（5 次 `summarize`）→ 便宜模型省 80% token
3. **子代理**用 serena + cheap-research 找风险（`find_referencing_symbols` + `extract`）→ 组合
4. **主会话**拿到结构化报告，**只裁决大决策**

**场景 2：审查第三方库最新 API**

1. **子代理**用 context7 查库最新文档（`query-docs`）→ 权威
2. **子代理**用 cheap-research 摘要文档（`summarize`）→ 关键点
3. **子代理**用 serena 查本工程相关调用（`find_referencing_symbols`）→ 关联
4. **子代理**用 sequential-thinking 做结构化推演（`sequentialthinking`）→ 可验证
5. **子代理**返回结构化报告给主会话

**场景 3：分析用户问题截图**

1. **子代理**用 vision-bridge 看图（`analyze_media`）→ 原始多模态
2. **子代理**用 cheap-research 解释图（`extract` 按 schema）→ 结构化
3. **子代理**用 serena 查相关代码（`find_symbol`）→ 关联
4. **主会话**拿到结构化结论

### 与现有 6 大 MCP 的功能定位

| MCP | 类型 | 核心能力 | LLM 依赖 |
|-----|------|---------|---------|
| **serena** | 工具型 | 精确符号/引用查询 | ❌ 无（纯 LSP） |
| **context7** | 工具型 | 外部库最新文档 | ❌ 无（纯检索） |
| **vision-bridge** | 工具型 | 多模态理解 | ⚠️ 调多模态模型 |
| **memory** | 工具型 | 实体关系存储 | ❌ 无（纯存储） |
| **sequential-thinking** | 工具型 | 结构化思考 | ❌ 无（本地推理） |
| **playwright** | 工具型 | 浏览器交互 | ❌ 无（纯协议） |
| **cheap-research**（新增） | **推理型** | **通用 LLM 推理** | ✅ **便宜模型** |

**核心洞察**：
- 现有 6 大全是**工具型 MCP**（核心提供"某种特定能力"）
- cheap-research 是**推理型 MCP**（核心提供"通用 LLM 推理"）
- 两者**互补不替代**：
  - 工具型：精确能力（找符号、查文档、看图、抓网页）
  - 推理型：通用能力（长文压缩、提取、检索、模板填充）

### cheap-research 给子代理的真正价值

**价值转换**：

| 子代理 | 没有 cheap-research | 有 cheap-research |
|--------|---------------------|---------------------|
| 能调工具型 MCP | ✅ | ✅ |
| 能搞 LLM 推理 | 主会话模型（贵） | **便宜模型下沉** |
| 长文压缩 | 主会话做（贵） | cheap-research 来（便宜） |
| 多轮推理 | 主会话做（贵） | cheap-research 来（便宜） |

**一句话总结**：
> cheap-research 给子代理"**便宜的 LLM 推理能力**"——子代理原本只能用"工具型 MCP"（精确但不通用），有了 cheap-research 之后可以"工具型 MCP + 便宜 LLM 推理"组合，**推理能力下沉**。

### 关键架构原则（v0.4 总结）

1. **MCP 工具可达性是默认行为**（general-purpose 全工具），不需特殊配置
2. **MCP 之间是平铺关系**（不是嵌套），子代理是组合者
3. **cheap-research 不替代其他 MCP，而是协作**（提供便宜的 LLM 推理）
4. **最强的子代理组合 = 强证据 MCP + 便宜 LLM**：
   - serena（精确符号）+ cheap-research（摘要长文） = "精准 + 省 token"
   - vision-bridge（看图）+ cheap-research（解释图） = "多模态 + 省 token"
   - context7（最新文档）+ cheap-research（提取关键点） = "权威 + 省 token"
5. **不会降低工作流能力**：
   - 工具型 MCP 能力边界不变（serena 仍是 LLM-independent 精确符号查询）
   - 推理能力下沉到便宜模型 = 主会话省 token，但工具能力不打折
   - 主会话仍把控"决策门"（adversarial / 风险评估 / 终审）

### 实施风险与对策

| 风险 | 描述 | 对策 |
|------|------|------|
| R5 | 便宜模型对工具型 MCP 输出做"过度解读" | 工具型 MCP 输出已是结构化，便宜模型只需"它说什么我信什么" |
| R6 | 子代理串联太多 MCP 导致调试复杂 | 实施阶段监控"工具调用次数 / 工单"，加阈值 |
| R7 | 便宜模型对工具型 MCP 错误输出"补全" | 加 `confidence` 字段 + 阈值告警 |
| R8 | MCP 工具集合膨胀导致上下文占用 | 实施阶段区分"必备/可选" MCP，引导子代理按需调用 |

### 给未来实施者的指引

1. **必须支持子代理组合**：cheap-research 安装时不能"独占"工具集，要与现有 6 大 MCP 共存
2. **schema 跨 MCP 一致**：cheap-research 输出 schema 与其他 MCP 输入 schema 对齐（如 `extract` 应能直接接 `serena.find_referencing_symbols` 的输出）
3. **监控与降级**：实施期记录"工具调用次数 / 工单"，超阈值走 Agent(model=haiku) 兜底
4. **跨 MCP 联动示例入测试**：至少 3 个端到端用例（serena+cheap / vision+cheap / context7+cheap）

---

## 更新日志

- **2026-07-31** 初稿：核心结论 + 方案对比 + 白名单分级
- **2026-07-31** v0.2：新增"高回报分流清单"（5 维评分 + 7 步逐项 + 工种封装 API + 量化预估）
- **2026-07-31** v0.3：完整逐项评分（按入口：init / log / doc / readme / start / fast / 1~6）
- **2026-07-31** v0.4：子代理 + MCP 协同架构（MCP 工具可达性 + 联动示例 + 风险对策）
- **2026-07-31** v0.5：模型配置模式（严格复用 vision-bridge "四件套 + 不锁平台" 模式）

---

## 模型配置模式（v0.5 新增 · 严格复用 vision-bridge）

> 用户 2026-07-31 指示：模型配置参考 [mcp/vision-bridge](../../mcp/vision-bridge/) 模式。
>
> 核心原则：**不锁任何平台、不推荐任何 provider**。你用什么平台、什么模型完全由你决定。

### vision-bridge 配置范式（参考对象）

**config.json 四件套**（用户填什么就用什么）：

```json
{
  "provider": "openai_compat",   // 或 local_ocr
  "base_url": "...",             // 你的 API 端点
  "api_key": "...",             // 你的 API KEY
  "model": "...",               // 你的模型名
  "timeout": 120,               // 可选
  "video_frames": 8             // 可选（视频专用）
}
```

**承诺**：
- 不推荐任何 provider
- 不锁任何平台
- 任何 OpenAI Chat Completions 兼容端点都能用
- `_doc_*` 内嵌字段做就地说明

### cheap-research config.json 模板（复用 vision-bridge 风格）

```json
{
  "_doc_1": "cheap-research 不推荐任何 provider。填你平台的真实值即可。",
  "_doc_2": "可选 provider: openai_compat | local_ollama。",
  "_doc_3": "openai_compat: 任意 OpenAI Chat Completions 兼容端点，必需下面 base_url/api_key/model 三件套。",
  "_doc_4": "local_ollama: 本地 Ollama 服务，零 KEY，仅需 base_url + model（如 http://localhost:11434/v1 + qwen2.5:7b）。",

  "provider": "openai_compat",

  "_base_url_doc": "你的 API 端点，不带尾 /。比如 https://api.openai.com/v1 或 http://localhost:11434/v1",
  "base_url": "",

  "_api_key_doc": "你的 API KEY。在你平台的开发者后台申请。local_ollama 时可填任意字符串。",
  "api_key": "",

  "_model_doc": "你平台提供的便宜模型名（从你平台文档『便宜/快速/轻量模型』列表里挑）。建议：GPT-4o-mini / Gemini-Flash / qwen2.5:7b / Claude Haiku 等。",
  "model": "",

  "_timeout_doc": "可选：HTTP 超时秒，默认 60（比 vision-bridge 短，cheap-research 任务更小）。",
  "timeout": 60
}
```

### cheap-research provider 设计

**严格复用 vision-bridge 的 [providers/](../../mcp/vision-bridge/providers/) 模式**：

```python
# providers/base.py —— 抽象基类
class LLMProvider:
    name: str
    def invoke(self, prompt: str, schema: dict = None, max_tokens: int = 2048) -> dict:
        raise NotImplementedError

# providers/openai_compat.py —— 通用兼容协议（复制 vision-bridge 实现）
class OpenAICompatProvider(LLMProvider):
    """覆盖所有 OpenAI Chat Completions 兼容端点"""
    name = "openai_compat"

# providers/local_ollama.py —— 本地 Ollama 扩展
class LocalOllamaProvider(LLMProvider):
    """本地 Ollama 服务（零成本、无外网）"""
    name = "local_ollama"
```

**与 vision-bridge 的差异**：
- 视频相关字段（`video_frames`）→ 移除（cheap-research 不处理视频）
- 文案相关字段（`max_tokens` / `temperature` / `default_focus`）→ 新增
- provider 名（`local_ollama` vs `local_ocr`）→ 针对便宜模型场景

### platform 选择矩阵（cheap-research 视角）

| 平台 | provider | 适用度 | 备注 |
|------|----------|--------|------|
| 官方 OpenAI | openai_compat | ★★★★ | GPT-4o-mini 价格低、能力强 |
| Anthropic Claude | openai_compat | ★★★★ | 通过 OpenAI 兼容代理（与 vision-bridge 同套） |
| Google Gemini | openai_compat | ★★★★ | Gemini-Flash 几乎免费 |
| 国内厂商（DeepSeek / Qwen / GLM） | openai_compat | ★★★★★ | 价格极低、能力强 |
| OpenRouter 聚合 | openai_compat | ★★★★ | 多模型聚合，按量切 |
| 本地 Ollama | local_ollama | ★★★★★ | 零成本、零外网 |
| 本地 LM Studio | openai_compat | ★★★★ | 兼容 OpenAI 协议 |
| 自建 vLLM | openai_compat | ★★★★ | 兼容 OpenAI 协议 |

> **与 vision-bridge 风格一致**：上表"备注"非推荐，仅说明常见搭配。**用户自决**。

### 安装流程（复用 mcp/vision-bridge/install.sh 模式）

```bash
# 1. 装到全局（一次性）
cd <icode-skill 仓库>/mcp/cheap-research
./install.sh

# 2. 填你的三件套
vim ~/.claude/skills/icode/mcp/cheap-research/config.json

# 3. 重启 Claude Code
# 调 mcp__cheap-research__summarize / extract / retrieve_similar 等
```

**install.sh 内部**（与 vision-bridge 几乎一致）：
- 同步代码到 `~/.claude/skills/icode/mcp/cheap-research/`
- 创建 venv、装依赖
- 注册到 `~/.claude.json` 的 `mcpServers.cheap-research`
- 自检配置文件 schema

### README 风格（参考 vision-bridge 段落）

```markdown
# cheap-research (MCP server for icode-skill)

**可选增强**：为 icode-skill 提供"便宜 LLM 推理"统一 MCP 接口。

> cheap-research **不锁任何平台**，**不推荐任何 provider**。只要你的 provider 提供
> OpenAI Chat Completions 兼容接口（绝大多数 LLM 平台都兼容），填三件就能跑。

## 安装（三步）

### 1. 装到全局（一次性）
cd <你的 icode-skill 仓库>/mcp/cheap-research
./install.sh

### 2. 填你的三件套
vim ~/.claude/skills/icode/mcp/cheap-research/config.json
填：
- provider  —— openai_compat（默认）或 local_ollama
- base_url  —— 你平台的 API 端点（**没有默认值，请查平台文档**）
- api_key   —— 你平台的 KEY
- model     —— 你平台提供的"便宜/快速/轻量"模型名

### 3. 重启 Claude Code
调 mcp__cheap-research__summarize / extract / retrieve_similar 等。

**没有任何推荐值** —— 你用什么平台、什么模型完全由你决定。
```

### 关键复用点（与 vision-bridge 完全对齐）

| 维度 | vision-bridge | cheap-research |
|------|---------------|----------------|
| config.json 字段 | provider / base_url / api_key / model + 可选 | 完全一致 |
| provider 类型 | openai_compat / local_ocr | openai_compat / local_ollama |
| install.sh 流程 | 同步 + venv + 注册 | **完全复用** |
| _doc_* 内嵌文档 | ✅ | ✅ |
| 不锁平台承诺 | ✅ | ✅ |
| OpenAI 兼容协议 | ✅ | ✅ |
| 优雅降级 | provider 缺字段 → UnconfiguredProvider | **完全复用** |
| 单文件自检 | config.json schema 校验 | **完全复用** |

### 优雅降级（与 vision-bridge 一致）

```python
# server.py 内部（伪代码，与 vision-bridge 一致）
def get_provider():
    cfg = load_config()
    name = cfg.get("provider", "openai_compat").lower()
    if name == "openai_compat":
        missing = [k for k in ("base_url", "api_key", "model") if not cfg.get(k)]
        if missing:
            # 缺字段等同未装：返回 fallback provider，不抛错、不阻塞
            return UnconfiguredProvider(missing=missing)
    ...
```

**降级效果**：
- cheap-research 未装 → 主会话 / 子代理完全感知不到（不可调用）
- cheap-research 装了但 config 缺字段 → 工具调用返回 `{error: "未配置"}`，不抛错
- 主会话 / 子代理侧 → 走 Agent(model=haiku) 兜底（方案 A）

### 元信息（cheap-research 自身 metadata）

为与 icode 现有文档生态对齐，cheap-research 自身 README 应包含：

```markdown
- 当前状态：可选 MCP（不影响 icode 工作流主干）
- 触发场景：长上下文压缩 / 历史检索 / 模板填充 / 跨工程信息提取
- 降级路径：未装 → Agent(model=haiku)；装了缺字段 → 工具返回 error
- 强证据：~/.claude.json 的 mcpServers.cheap-research 段存在 + config.json 三件套已填
- 兼容性：与现有 6 大 MCP（serena / context7 / vision-bridge / memory / playwright / sequential-thinking）共存，子代理可自由组合
```

**这与 [mcp_integration.md](mcp_integration.md) 的"MCP 强证据 + 降级路径"模式完全对齐**——实施 cheap-research 时直接复用本文档落到 references/mcp_integration.md 即可。

### 风险与对策（v0.5 增补）

| 风险 | 描述 | 对策 |
|------|------|------|
| R9 | 不同平台 API 字段差异（流式 vs 非流式、tool calling 支持度） | 优先用非流式 + 简单 prompt + 强制 schema 输出，不依赖 tool calling |
| R10 | 本地 Ollama 资源占用（显存、CPU） | 文档提示"小模型够用（7B 以下），大模型无优势" |
| R11 | API 配额超限 | 增 `max_tokens` / `timeout` 限流；增 retry 1 次 |
| R12 | 多个 MCP 都用 OpenAI 兼容协议时配置重复 | 未来 v0.6 考虑 `_lib/platform_entry.py` 共享 base_url/api_key（与 vision-bridge `_lib/` 对齐） |

---

## 待决事项（v0.5 汇总）

**已定**：
- 走方案 A 路径（暂不建 cheap-research）
- 白名单策略
- 强制 schema 输出
- 模型配置严格复用 vision-bridge 模式

**未决**（未来实施时讨论）：
- 实际启动哪个 provider？（OpenAI / Claude Haiku / 本地 Ollama）
- 是否需要 v0.6 共享 `_lib/platform_entry.py`？
- cheap-research 与 vision-bridge 安装是否合并（统一 mcp_install.sh）？

---

## 双闸门决策（v0.6 新增 · 用户拍板）

> 用户 2026-07-31 决策：**价值回报 ≥ 3 ★ 才做、高风险的不做**。
>
> 这意味着之前的"高回报清单"还要再过滤——只保留"价值 ≥ 3 ★ AND 风险 ≠ 高"的子任务。

### 双维度评分模型

```python
# 维度 1：价值回报（1~5 ★）
value = f(token_省, 频次, 可验证性)
# 维度 2：风险等级（低 / 中 / 高）
risk = f(错误成本, 推理敏感度, 决策性)

# 入选规则
入选 = (value >= 3★) AND (risk != 高)
```

### 三档分类

| 档位 | 价值 | 风险 | 处理 |
|------|------|------|------|
| **Tier A 必做** | ≥ 3 ★ | 低 | 实施 + 集成 |
| **Tier B 灰区** | ≥ 3 ★ | 中 | 实施 + 主会话复核 |
| **Tier C 不做** | < 3 ★ 或 高风险 | — | 排除 |

### 排除清单（按原因分类，备忘不做）

#### C1. 价值 < 3 ★（即便利宜也不值得做）

| 子任务 | 入口 | 价值 | 理由 |
|--------|------|------|------|
| 重复意见合并 | merge | ★★ | 步骤短，省不了多少 |
| 死代码清理 | code | ★★ | 静态分析工具已覆盖 |
| 批量补全（缺失分支） | code | ★★ | 局部、模式化 |
| 格式化 / import 排序 | code | ★★ | linter 已覆盖 |
| 风险章节提炼 | readme | ★★ | 推理敏感 |
| 6.2 强制修复 | audit | ★ | 决策密集 |
| 6.3 最终交付 | audit | ★ | 决策密集 |

#### C2. 高风险（不能做）

| 子任务 | 入口 | 风险等级 | 理由 |
|--------|------|----------|------|
| 3 质疑者对抗验证 | review / deepcheck / log | 高 | 质疑深度直接定工作流质量 |
| 架构决策（4 维度设计态） | plan | 高 | 决策性 |
| 终审裁决 | audit | 高 | 决策性 |
| 修复方案 | log 阶段 8 | 高 | 决策性 |
| 根因假设 | log 阶段 4 | 高 | 推理敏感 |
| 修复方向论证 | log 阶段 6+7 | 高 | 推理敏感 |
| 需求点抽取 | init | 高 | 推理敏感 |
| 4 维度验证清单 | init | 高 | 推理敏感 |
| 关键决策（区分类别） | merge | 高 | 决策性 |
| 编码实施（关键设计） | code | 高 | 推理敏感 |
| 自由探索式质疑 | deepcheck Free | 高 | 推理敏感 |
| 用户对话密集环节 | init / start / fast 决策点 | 高 | 用户交互 |
| 强制思考前置 | doc | 高 | 必须主会话 |

### 入选清单（Tier A + Tier B · v0.6 重核）

#### 步骤 0 init

| 子任务 | 价值 | 风险 | 档位 | MCP 工具 |
|--------|------|------|------|----------|
| 现状盘点（读工程结构） | ★★★★ | 低 | **A** | `summarize` |
| 链路图绘制 | ★★★ | 中 | **B** | `describe_link` |
| 历史工单匹配 | ★★★★ | 低 | **A** | `retrieve_similar` |

#### 步骤 1 plan

| 子任务 | 价值 | 风险 | 档位 | MCP 工具 |
|--------|------|------|------|----------|
| schema 迁移 | ★★★ | 低 | **A** | `apply_migration` |
| 接口误用预审 | ★★★ | 中 | **B** | `scan_misuse` |
| 端到端路径推演 | ★★★ | 中 | **B** | `trace_path` |
| 历史 ADR 检索 | ★★★★ | 低 | **A** | `retrieve_similar` |
| 跨工程代码事实审计 | ★★★★ | 低 | **A** | `audit_facts` |

#### 步骤 2 review

| 子任务 | 价值 | 风险 | 档位 | MCP 工具 |
|--------|------|------|------|----------|
| 首轮审查 | ★★★ | 中 | **B** | `extract` |
| 第 N 轮增量审查 | ★★★ | 低 | **A** | `diff_summary` |
| 维度结果结构化 | ★★★ | 低 | **A** | `fill_template` |
| 历史相似 issue 检索 | ★★★★ | 低 | **A** | `retrieve_similar` |
| grep 模式扫描 | ★★★ | 低 | **A** | `scan_patterns` |
| 引用追溯 | ★★★ | 低 | **A** | `trace_refs` |

#### 步骤 4 code

| 子任务 | 价值 | 风险 | 档位 | MCP 工具 |
|--------|------|------|------|----------|
| schema 迁移（实施态） | ★★★ | 低 | **A** | `apply_migration` |

> **代码步骤大量子任务被排除**（★<3 + 高风险），仅保留 schema 迁移。

#### 步骤 5 deepcheck

| 子任务 | 价值 | 风险 | 档位 | MCP 工具 |
|--------|------|------|------|----------|
| Reverse 阶段原文对比 | ★★★ | 低 | **A** | `diff_summary` |
| 阶段摘要压缩 | ★★★ | 低 | **A** | `summarize` |
| Fixed 阶段（固定维度） | ★★★ | 中 | **B** | `extract` |

#### 步骤 6 audit

| 子任务 | 价值 | 风险 | 档位 | MCP 工具 |
|--------|------|------|------|----------|
| 6 维度出具报告 | ★★★ | 中 | **B** | `generate_report` |
| 6.4 交付报告提示 | ★★★ | 低 | **A** | `fill_template` |
| schema 状态汇总 | ★★★ | 低 | **A** | `summarize` |
| 实现偏差备忘 | ★★★ | 低 | **A** | `fill_template` |

#### 步骤 7 readme

| 子任务 | 价值 | 风险 | 档位 | MCP 工具 |
|--------|------|------|------|----------|
| 文件名生成 | ★★★ | 低 | **A** | `generate_filename` |
| 智能模板选择 | ★★★ | 低 | **A** | `select_template` |
| 模板填充（7 个段落） | ★★★★ | 低 | **A** | `fill_template` |
| 已知限制检索 | ★★★ | 低 | **A** | `retrieve_similar` |
| 自包含约束检查 | ★★★ | 中 | **B** | `extract` |

#### log

| 子任务 | 价值 | 风险 | 档位 | MCP 工具 |
|--------|------|------|------|----------|
| 阶段 0 输入采集 | ★★★★★ | 低 | **A** | `summarize` |
| 阶段 1 基线检查 | ★★★ | 低 | **A** | `diff_summary` |
| 阶段 2 日志侦察 | ★★★★★ | 低 | **A** | `summarize` |
| 阶段 3 链路图分析 | ★★★ | 中 | **B** | `summarize` + 主会话筛 |
| 阶段 5 增量精确化 | ★★★ | 中 | **B** | `summarize` |
| 8.6 memory 沉淀 | ★★★ | 低 | **A** | `extract` |
| TB 缺陷源拉取 | ★★★ | 低 | **A** | `fetch_remote` |
| TB 评论等回复 | ★★★ | 低 | **A** | `fill_template` |
| TB 附件分析 | ★★★ | 低 | **A** | vision-bridge 已承担 |

> **log 阶段 4 根因假设、阶段 6+7 对抗、阶段 8 修复建议、追问机制全部排除**（高风险）。

#### doc

| 子任务 | 价值 | 风险 | 档位 | MCP 工具 |
|--------|------|------|------|----------|
| project_id 解析 | ★★★ | 低 | **A** | `parse_project_id` |
| 意图识别 | ★★★ | 中 | **B** | `classify_intent` |
| 模块检测 | ★★★ | 低 | **A** | `scan_modules` |
| 代码特征扫描 | ★★★ | 低 | **A** | `summarize` |
| 增量判定 | ★★★ | 中 | **B** | `diff_summary` |
| 质量审视与模板迁移 | ★★★ | 低 | **A** | `apply_migration` |
| 正常生成（按章节） | ★★★ | 低 | **A** | `fill_template` |
| 99_code_facts_audit | ★★★★ | 低 | **A** | `audit_facts` |
| 进度输出 | ★★★ | 低 | **A** | `fill_template` |
| 主动 stale 扫描 | ★★★ | 低 | **A** | `scan_patterns` |

#### start / fast / list / status

| 子任务 | 入口 | 价值 | 风险 | 档位 | MCP 工具 |
|--------|------|------|------|------|----------|
| 历史检索 | start | ★★★★ | 低 | **A** | `retrieve_similar` |
| 历史检索 | fast | ★★★ | 低 | **A** | `retrieve_similar` |
| 全量读取 index.json | list | ★★★ | 低 | **A** | 内置 |
| 关键词搜索 | list | ★★★ | 低 | **A** | `scan_patterns` |
| 表格化输出 | list | ★★★ | 低 | **A** | `fill_template` |
| 状态查询 | status | ★★★ | 低 | **A** | 内置 |
| scan-verdict 批量扫描 | status | ★★★ | 低 | **A** | `extract` |

### 工具优先级（基于双闸门过滤）

#### Tier 1 必装（覆盖 80% 入选场景）

```python
"retrieve_similar":   ★★★★★ 频次最高（init/plan/review/log/start/fast/readme）
"summarize":          ★★★★★ 价值最高（log 0/1/2/deepcheck/doc）
"audit_facts":        ★★★★  (plan/doc 99_code_facts)
"fill_template":      ★★★★  (review/audit/audit/readme/list/status)
"extract":            ★★★    (doc 99/log 8.6)
```

#### Tier 2 增强（覆盖 95% 入选场景）

```python
"scan_patterns":      ★★★  (review grep/repo stale/list)
"trace_refs":         ★★★  (review 引用追溯)
"diff_summary":       ★★★  (review/deepcheck/doc 增量)
"apply_migration":    ★★★  (plan/code schema)
"fetch_remote":       ★★★  (log TB 拉取)
"generate_filename":  ★★★  (readme)
"select_template":    ★★★  (readme)
"parse_project_id":   ★★★  (doc)
"scan_modules":       ★★★  (doc)
```

#### Tier 3 灰区（实施 + 主会话复核）

```python
"describe_link":      ★★★  (init/log 链路图)
"scan_misuse":        ★★★  (plan 接口误用)
"trace_path":         ★★★  (plan 端到端路径)
"generate_report":    ★★★  (audit 6 维度)
"classify_intent":    ★★★  (doc 意图识别)
```

#### 不做（Tier 4 排除）

```python
"find_dead":          ★★  (code 步骤，价值低)
"fill_branches":      ★★  (code 步骤，价值低)
"format":             ★★  (code 步骤，价值低)
"dedupe":             ★★  (merge，价值低)
"fill_template_v2":   ★★  (readme 风险章节，<3★)
"fix_suggestion":     高风险  (log 修复方案)
"root_cause":         高风险  (log 根因假设)
"architecture_decision":  高风险  (plan)
"adversarial_check":  高风险  (review/deepcheck)
"final_verdict":      高风险  (audit)
```

### 关键洞察（v0.6 总结）

1. **双闸门过滤后实施面收窄**：
   - 原 20+ 子任务 → 入选 28 个（Tier A 22 + Tier B 6）
   - 原 18 个 MCP 工具 → 必装 13 个（Tier 1: 5 + Tier 2: 8 + Tier 3: 6 灰区）
   - 不做：7 个工具（find_dead / fill_branches / format 等边际价值）

2. **高风险子任务集中在"决策门"**：
   - 3 质疑者对抗（review / deepcheck / log）
   - 架构决策（plan / code）
   - 终审裁决（audit）
   - 修复方案（log / code）
   - 用户对话（init / start / fast）

3. **实施优先级约束**：
   - 阶段 1a：先实现 Tier 1（5 个核心工具）→ 覆盖 80% 价值
   - 阶段 1b：补 Tier 2（8 个工具）→ 覆盖 95% 价值
   - 阶段 2：补 Tier 3（6 个灰区工具）→ 100% 入选场景
   - 不实施 Tier 4（7 个工具）

4. **架构级原则更新**：
   - **便宜模型不接管决策**：所有 ✗ 标记的工作一律不交给便宜模型
   - **推理敏感度优先**：5 维评分中"能力敏感度"权重提升（如提高 30%）
   - **错误成本为闸门**：错误成本低 → Tier A；中 → Tier B；高 → 排除
   - **可验证为加速**：可验证的子任务优先做（敢降级）

5. **rollback 机制**：
   - 实施 cheap-research 后若发现某子任务质量下降 → 立即 rollback 至主会话
   - rollback 触发条件：审计发现 ≥1 个 critical issue 归因于 cheap-research
   - rollback 手段：metadata.subagent_model 改 opus / sonnet，或临时关闭该 MCP

### 下一步决策（v0.6 增补）

- ✅ 价值 ≥ 3 ★ + 低风险 → 入选 Tier A
- ✅ 价值 ≥ 3 ★ + 中风险 → 入选 Tier B（主会话复核）
- ❌ 价值 < 3 ★ 或 高风险 → 不做
- **新增问题**：灰区子任务（Tier B）需要"主会话复核"——具体怎么复？怎么保证不漏？

  候选方案：
  - **B1**：便宜模型输出后强制主会话读一遍（每次）→ 成本高但稳
  - **B2**：便宜模型输出附 `confidence` 字段，< 阈值才复核 → 智能但有漏检风险
  - **B3**：同类任务累计 10 次才抽样复核 1 次 → 成本低但漏检

  建议 v0.7 阶段讨论决定。

---

## 更新日志

- **2026-07-31** 初稿：核心结论 + 方案对比 + 白名单分级
- **2026-07-31** v0.2：新增"高回报分流清单"（5 维评分 + 7 步逐项 + 工种封装 API + 量化预估）
- **2026-07-31** v0.3：完整逐项评分（按入口：init / log / doc / readme / start / fast / 1~6）
- **2026-07-31** v0.4：子代理 + MCP 协同架构（MCP 工具可达性 + 联动示例 + 风险对策）
- **2026-07-31** v0.5：模型配置模式（严格复用 vision-bridge "四件套 + 不锁平台" 模式）
- **2026-07-31** v0.6：双闸门决策（价值 ≥ 3 ★ + 低风险入选；高风险排除；灰色 6 个待复核机制）
- **2026-07-31** v0.7：安装/卸载集成（mcp/install.sh + uninstall.sh 自动扫描，强证据 + 降级路径与 vision-bridge 完全对齐）
- **2026-07-31** v0.8：单闸门收紧（价值 ≥ 3 ★ + 低风险 = 唯一入选；中风险全部退出；零灰区原则）
- **2026-07-31** v0.9：实施路线图（6 阶段 + 6 里程碑 + 10 决策点 + 5 风险）

---

## 安装/卸载 + 强证据集成（v0.7 新增）

> 用户 2026-07-31 指示：
> 1. cheap-research 是否可调，**跟 vision-bridge 一样**——看本机是否装了 + config.json 是否填了 KEY
> 2. **一键安装/卸载 MCP** 自动扫描到 cheap-research（即 mcp/ 顶层 install.sh + uninstall.sh 集成）
>
> 这与现有 [mcp_integration.md](mcp_integration.md) 的"强证据 + 降级路径"模式完全对齐。

### 强证据判定（与 vision-bridge 完全对齐）

```python
# 强证据 A（v0.2 强证据）：~/.claude.json 的 mcpServers.cheap-research 段存在
硬证据_A = {
    "type": "object",
    "field": "mcpServers.cheap-research",
    "location": "~/.claude.json"
}

# 强证据 B（v0.2 强证据）：当前会话 deferred tools 列表里有 mcp__cheap-research__*
硬证据_B = {
    "type": "list",
    "field": "mcp__cheap-research__summarize | __extract | __retrieve_similar | __fill_template | ...",
    "location": "session tools"
}

# 强证据 A 满足 + 强证据 B 满足 → 强证据 ✅ 可调
# 任一不存在 → 走降级路径
```

**`config.json` 必须填齐的字段**（与 vision-bridge 一致）：

- `provider`（`openai_compat` 或 `local_ollama`）
- `base_url`（非空）
- `api_key`（非空；`local_ollama` 时可填任意字符串）
- `model`（非空）

**缺字段**：`vision-bridge` 已实现 `UnconfiguredProvider` 模式（工具返回 `{error: "未配置"}` 不抛错），cheap-research 严格复用。

### 强证据判定流程（伪代码）

```python
# 严格复用 [mcp_integration.md](mcp_integration.md) 模式
def is_cheap_research_available():
    # 证据 A：~/.claude.json 注册
    if not has_mcp_server_registered("cheap-research"):
        return False, "未注册（mcp/install.sh 没跑）"
    
    # 证据 B：session 工具列表
    if not has_tool("mcp__cheap-research__summarize"):
        return False, "session 未加载（重启 Claude Code）"
    
    # 配置文件三件套
    cfg = load_config("~/.claude/skills/icode/mcp/cheap-research/config.json")
    missing = [k for k in ("base_url", "api_key", "model") if not cfg.get(k)]
    if missing:
        return False, f"config.json 缺字段 {missing}"
    
    return True, "OK"
```

### 降级路径（与 vision-bridge 完全对齐）

| 状态 | 行为 | 工具调用效果 |
|------|------|----------------|
| **未装**（`mcp/install.sh` 没跑） | 工具根本不可见 | 主会话 / 子代理不感知 → 走 Agent(model=haiku) 兜底 |
| **装了但 config 缺字段** | 工具可见但调失败 | 返回 `{error: "未配置 base_url/api_key/model"}`，主会话识别后走 Agent(model=haiku) |
| **完整可用** | 工具可见且调用成功 | 返回结构化结果（answer/confidence/model/cost） |
| **provider 临时失败**（API 配额/超时） | 工具调用报错 | 返回 `{error: "..."}`，主会话识别后走 Agent(model=haiku) 兜底 |

**关键**：
- 整个流程**不阻塞**主流程（与 vision-bridge 一致）
- 主会话 / 子代理不需要感知"cheap-research 是否存在"——按 MCP 自动可见性走
- 兜底方案：Agent(model=haiku)（方案 A）

### 一键安装集成（mcp/install.sh 自动扫描）

**现状**（已有）：

```bash
# mcp/install.sh 已扫描 mcp/*/install.sh
shopt -s nullglob
installers=("$HERE"/*/install.sh)
```

**新增 cheap-research 后**：

```bash
# /home/orbbec/.claude/skills/icode/mcp/cheap-research/install.sh
# 完全复用 vision-bridge 骨架：
# 1. 同步代码到 ~/.claude/skills/icode/mcp/cheap-research/
# 2. 创建 venv、装依赖
# 3. 注册到 ~/.claude.json 的 mcpServers.cheap-research
# 4. 自检 config.json 字段
```

**用户视角**：

```bash
# 全新 clone 仓库 / 新机器 / CI 初始化
cd <icode-skill 仓库>/mcp
./install.sh                 # 扫描 + 全部安装（含 cheap-research）

# 只装 cheap-research
./install.sh cheap-research
```

**install.sh 内部要点**（与 vision-bridge 完全对齐）：

```bash
#!/usr/bin/env bash
# cheap-research 一键装/同步到 ~/.claude/skills/icode/mcp/cheap-research + 注册 MCP
set -e

HERE="$(cd "$(dirname "$0")" && pwd)"
TARGET="${CHEAP_RESEARCH_TARGET:-$HOME/.claude/skills/icode/mcp/cheap-research}"

# 守卫：防止脚本被复制到其他目录错误执行
if [ ! -f "$HERE/server.py" ] || [ ! -d "$HERE/providers" ]; then
  echo "❌ 当前目录不是 cheap-research 工程根目录"
  exit 1
fi

# --full 模式：完整重装
if [ "${1:-}" = "--full" ]; then
  rm -rf "$HERE/.venv" "$TARGET"
fi

# 1. 同步代码到 target
mkdir -p "$TARGET"
rsync -a --delete \
  --exclude='.venv/' --exclude='__pycache__/' \
  --exclude='config.json' --exclude='.env' \
  "$HERE/" "$TARGET/"
cd "$TARGET"

# 2. 创建 venv + 装依赖
python3 -m venv .venv
.venv/bin/pip install -q -r requirements.txt

# 3. 注册到 ~/.claude.json
python3 - <<PYEOF
import json
from pathlib import Path
claude_json = Path.home() / ".claude.json"
cfg = json.loads(claude_json.read_text())
cfg.setdefault("mcpServers", {})
cfg["mcpServers"]["cheap-research"] = {
    "command": str(Path("$TARGET/.venv/bin/python").resolve()),
    "args": ["-m", "mcp"],
    "cwd": str(Path("$TARGET").resolve()),
    "env": {
        "CHEAP_RESEARCH_CONFIG": str(Path("$TARGET/config.json").resolve())
    }
}
claude_json.write_text(json.dumps(cfg, indent=2, ensure_ascii=False))
PYEOF

# 4. 自检 config.json 字段
python3 - <<PYEOF
import json, os
cfg_path = os.environ.get("CHEAP_RESEARCH_CONFIG", "$TARGET/config.json")
try:
    cfg = json.loads(open(cfg_path).read())
    missing = [k for k in ("base_url", "api_key", "model") if not cfg.get(k)]
    if missing:
        print(f"⚠️  config.json 缺字段 {missing}，cheap-research 暂不可用")
        print(f"   编辑: vim {cfg_path}")
    else:
        print("✅ config.json 完整")
except FileNotFoundError:
    print(f"⚠️  config.json 不存在，cp config.example.json config.json 后填字段")
PYEOF

echo "✅ cheap-research 已安装到 $TARGET"
echo "   重启 Claude Code 后生效"
```

### 一键卸载集成（mcp/uninstall.sh 自动扫描）

**现状**（已有）：

```bash
# mcp/uninstall.sh 已扫描 mcp/*/uninstall.sh
shopt -s nullglob
uninstallers=("$HERE"/*/uninstall.sh)
```

**新增 cheap-research 后**：

```bash
# /home/orbbec/.claude/skills/icode/mcp/cheap-research/uninstall.sh
# 完全复用 vision-bridge 骨架：
# 1. 从 ~/.claude.json 移除 mcpServers.cheap-research 段
# 2. 可选：删除 ~/.claude/skills/icode/mcp/cheap-research 目录
```

**用户视角**：

```bash
# 全部卸载
cd <icode-skill 仓库>/mcp
./uninstall.sh

# 只卸载 cheap-research
./uninstall.sh cheap-research
```

### 现有 6 大 vs 7 大矩阵（v0.7 增补）

**之前**（v0.1~0.6）：6 大 MCP（sequential-thinking / context7 / memory / playwright / serena / vision-bridge）

**之后**（v0.7 实施后）：7 大 MCP，多 1 个 cheap-research

| 序号 | MCP | 类型 | 强证据 | 降级 |
|------|------|------|--------|------|
| ① | sequential-thinking | 工具型 | ✅ | 文字块结构化思考 |
| ② | context7 | 工具型 | ✅ | WebFetch 替代 |
| ③ | memory | 工具型 | ✅ | 文件存储 |
| ④ | playwright | 工具型 | ✅ | WebFetch 替代 |
| ⑤ | serena | 工具型 | ✅ | LSP 替代 |
| ⑥ | vision-bridge | 工具型 | ✅ | 原生多模态 |
| ⑦ | **cheap-research**（新增） | **推理型** | ✅ | **Agent(model=haiku)** |

**关键差异**：
- 前 6 大都是**工具型 MCP**（核心提供"某种特定能力"）
- cheap-research 是**推理型 MCP**（核心提供"便宜 LLM 推理"）
- 降级路径不同：前 6 大降级为"用替代工具"；cheap-research 降级为"用便宜模型（Agent(model=haiku)）"

### mcp_integration.md 增补（实施时落地）

在 [mcp_integration.md](mcp_integration.md) 的"6 个 MCP 的强证据 + 降级路径"段后，增补第 7 段：

```markdown
### ⑦ cheap-research（**可选增强**）

- **强证据**：`~/.claude.json` 的 `mcpServers.cheap-research` 段存在 + `config.json` 三件套（base_url/api_key/model）已填
- **强证据满足**：`mcp__cheap-research__summarize(text)` / `__extract(text, schema)` / `__retrieve_similar(query)` / `__fill_template(template, data)` 等返回结构化结果，**子代理优先用 MCP 工具**
- **降级**（没装 / 装了没填三件套）：主会话 / 子代理走 Agent(model="haiku") 兜底（方案 A），不阻塞主流程
- **触发场景**：长上下文压缩（log / doc / deepcheck）、历史检索（init / plan / start / fast）、模板填充（readme / audit / list）、结构化提取（doc 99_code_facts_audit）
- **当前状态**：待实施（用户 2026-07-31 决策：先方案 A 验证价值，再上方案 B）
- **价值评分**：依据 [cheap-subagent-research.md](cheap-subagent-research.md) v0.6 的"双闸门"评估（≥ 3 ★ + 低风险入选）
- **不接管决策**：所有高风险子任务（3 质疑者对抗 / 架构决策 / 终审裁决 / 修复方案）一律不交给 cheap-research
```

### mcp_per_step.md 增补（实施时落地）

在步骤 × MCP 推荐矩阵中，给"非必装"标记加 ✓ 推荐 cheap-research：

| 步骤 | sequential-thinking | vision-bridge | serena | context7 | **cheap-research** |
|------|---------------------|---------------|--------|----------|-------------------|
| 0 init | 🟢 | ⚪ | ⚪ | ⚪ | **⚪ 选装** |
| 1 plan | 🟢 | ⚪ | 🟢 | 🟢 | **⚪ 选装** |
| 2 review | 🟢 | ⚪ | 🟢 | ⚪ | **⚪ 选装** |
| 3 merge | 🟢 | ⚪ | ⚪ | ⚪ | ⚪ |
| 4 code | 🟢 | ⚪ | ⚪ | 🟢 | **⚪ 选装（仅 schema 迁移）** |
| 5 deepcheck | 🟢 | ⚪ | 🟢 | ⚪ | **⚪ 选装** |
| 6 audit | 🟢 | ⚪ | 🟢 | ⚪ | **⚪ 选装（仅 6.4 模板）** |
| 7 readme | 🟢 | ⚪ | ⚪ | ⚪ | **⚪ 选装** |
| log | 🟢 | 🟢 | 🟢 | ⚪ | **🟡 强烈推荐** |
| doc | 🟢 | ⚪ | 🟢 | ⚪ | **🟡 强烈推荐** |

**说明**：
- 🟢 推荐（必调 / 强烈推荐）
- ⚪ 不必调
- **🟡 强烈推荐**（仅 cheap-research 出现的标记）—— 表示"该入口 cheap-research 价值密度最高"

### 关键实施检查清单（v0.7 落地清单）

实施 cheap-research 时，必须同时落地：

- [ ] `mcp/cheap-research/server.py` (MCP 入口)
- [ ] `mcp/cheap-research/providers/{base, openai_compat, local_ollama}.py`
- [ ] `mcp/cheap-research/config.example.json` (三件套模板)
- [ ] `mcp/cheap-research/install.sh` (一键安装 + 注册)
- [ ] `mcp/cheap-research/uninstall.sh` (一键卸载)
- [ ] `mcp/cheap-research/requirements.txt` (Python 依赖)
- [ ] `mcp/cheap-research/pyproject.toml` (包管理)
- [ ] `mcp/cheap-research/README.md` (与 vision-bridge 风格一致)
- [ ] `mcp/cheap-research/tests/` (端到端测试)
- [ ] `references/mcp_integration.md` 增补 ⑦ 段
- [ ] `references/mcp_per_step.md` 增补 cheap-research 列
- [ ] `references/cheap-subagent-research.md`（本文档）同步到已安装目录
- [ ] 实际跑 1~2 个工单做 A/B 对照（验证价值）
- [ ] 灰区 6 个子任务的复核机制（待 v0.8 讨论）

### 关键架构原则（v0.7 总结）

1. **强证据 + 降级路径**：与 vision-bridge 完全对齐——判断 cheap-research 是否可用，**完全靠本机状态**（装了 + config 填了），不依赖任何硬编码
2. **一键安装/卸载集成**：mcp/install.sh 和 uninstall.sh 自动扫描到 cheap-research，无需手动改顶层脚本
3. **Graceful Degradation**：未装 / 缺字段 / API 失败 → 走 Agent(model=haiku) 兜底（不阻塞主流程）
4. **不接管决策**：严格按 v0.6 双闸门——高风险子任务一律不交给 cheap-research
5. **子代理自动可达**：cheap-research 注册到 ~/.claude.json 后，子代理（general-purpose / Explore / Plan）默认可见，无需额外配置

### 给实施者的关键提示

1. **不要改 mcp/install.sh 顶层扫描逻辑**——它已支持 `mcp/*/install.sh` 自动扫描，新建 cheap-research 的 install.sh 即可
2. **server.py 复用 vision-bridge 的 unconfigured 模式**——provider 字段缺失或三件套未填时返回 `{error: "未配置"}`，不抛错
3. **config.json schema 校验**——参考 vision-bridge 的 `_doc_*` 字段，用户填错时给清晰提示
4. **与 _lib/ 复用**——如果未来 v0.8 决定共享 `_lib/platform_entry.py`，vision-bridge 和 cheap-research 可以共用 OpenAI 兼容协议调用代码
5. **回归测试**：实施后跑 1~2 个回归用例，确认未装 / 缺字段 / 完整可用 三种状态都符合预期

---

## 决策点备忘（v0.7 完整版）

### ✅ 已定（用户拍板）

- 走方案 A 路径（暂不建 cheap-research）
- 白名单策略
- 强制 schema 输出
- 双闸门决策：价值 ≥ 3 ★ + 低风险才做
- 模型配置严格复用 vision-bridge 模式
- 强证据 + 降级路径与 vision-bridge 完全对齐
- 一键安装/卸载集成（mcp/install.sh + uninstall.sh 自动扫描）

### ❓ 未决（未来实施时讨论）

- 实际启动哪个 provider？（OpenAI / Claude Haiku / 本地 Ollama / Qwen / DeepSeek）
- 灰区 6 个子任务的"主会话复核"机制（v0.6 提出 B1/B2/B3 三种方案）
- 是否需要 v0.8 共享 `_lib/platform_entry.py`（与 vision-bridge 共享 OpenAI 兼容协议调用代码）
- cheap-research 与 vision-bridge 安装是否合并（统一 mcp_install.sh）
- 何时启动 cheap-research 实施（用户的"额度吃紧"信号触发）

---

## 单闸门收紧（v0.8 新增 · 用户拍板）

> 用户 2026-07-31 决策升级：**中风险也不做**。
>
> 之前 v0.6 双闸门（价值 ≥ 3 ★ + 低风险入选，中风险走"主会话复核"灰区）→ 现在彻底收紧为**单闸门**：价值 ≥ 3 ★ + 低风险 = 唯一入选条件。

### 新规则

```python
# v0.8 单闸门
入选 = (value >= 3★) AND (risk == 低)

# 不再做"主会话复核"的灰区变体
# 中风险 = 不做
```

### 排除清单扩展（C2 增补：中风险也归到这里）

| 子任务 | 入口 | 风险 | 理由 |
|--------|------|------|------|
| 链路图绘制 | init / log 阶段 3 | 中 | 模板化但需主会话裁剪 |
| 接口误用预审 | plan | 中 | 模式化但需上下文 |
| 端到端路径推演 | plan | 中 | 模式化但需串联判断 |
| 首轮审查 | review | 中 | 模式化但需理解 |
| Fixed 阶段 | deepcheck | 中 | 模板化但需理解 |
| 增量精确化 | log 阶段 5 | 中 | 摘要压缩但需上下文 |
| 意图识别 | doc | 中 | 推理敏感度中等 |
| 增量判定 | doc | 中 | 模式化但需上下文 |
| 6 维度报告 | audit | 中 | 模板化但需综合判断 |
| 自包含约束检查 | readme | 中 | 模式化但需判断 |

**新增 10 个排除子任务**（v0.6 时是 Tier B 灰区，现全部不做）。

### 入选清单（v0.8 严格 Tier A · 单一档位）

#### 步骤 0 init

| 子任务 | 价值 | 风险 | MCP 工具 |
|--------|------|------|----------|
| 现状盘点（读工程结构） | ★★★★ | 低 | `summarize` |
| 历史工单匹配 | ★★★★ | 低 | `retrieve_similar` |

#### 步骤 1 plan

| 子任务 | 价值 | 风险 | MCP 工具 |
|--------|------|------|----------|
| schema 迁移 | ★★★ | 低 | `apply_migration` |
| 历史 ADR 检索 | ★★★★ | 低 | `retrieve_similar` |
| 跨工程代码事实审计 | ★★★★ | 低 | `audit_facts` |

#### 步骤 2 review

| 子任务 | 价值 | 风险 | MCP 工具 |
|--------|------|------|----------|
| 第 N 轮增量审查 | ★★★ | 低 | `diff_summary` |
| 维度结果结构化 | ★★★ | 低 | `fill_template` |
| 历史相似 issue 检索 | ★★★★ | 低 | `retrieve_similar` |
| grep 模式扫描 | ★★★ | 低 | `scan_patterns` |
| 引用追溯 | ★★★ | 低 | `trace_refs` |

#### 步骤 4 code

| 子任务 | 价值 | 风险 | MCP 工具 |
|--------|------|------|----------|
| schema 迁移（实施态） | ★★★ | 低 | `apply_migration` |

#### 步骤 5 deepcheck

| 子任务 | 价值 | 风险 | MCP 工具 |
|--------|------|------|----------|
| Reverse 阶段原文对比 | ★★★ | 低 | `diff_summary` |
| 阶段摘要压缩 | ★★★ | 低 | `summarize` |

#### 步骤 6 audit

| 子任务 | 价值 | 风险 | MCP 工具 |
|--------|------|------|----------|
| 6.4 交付报告提示 | ★★★ | 低 | `fill_template` |
| schema 状态汇总 | ★★★ | 低 | `summarize` |
| 实现偏差备忘 | ★★★ | 低 | `fill_template` |

#### 步骤 7 readme

| 子任务 | 价值 | 风险 | MCP 工具 |
|--------|------|------|----------|
| 文件名生成 | ★★★ | 低 | `generate_filename` |
| 智能模板选择 | ★★★ | 低 | `select_template` |
| 模板填充（7 个段落） | ★★★★ | 低 | `fill_template` |
| 已知限制检索 | ★★★ | 低 | `retrieve_similar` |

#### log

| 子任务 | 价值 | 风险 | MCP 工具 |
|--------|------|------|----------|
| 阶段 0 输入采集 | ★★★★★ | 低 | `summarize` |
| 阶段 1 基线检查 | ★★★ | 低 | `diff_summary` |
| 阶段 2 日志侦察 | ★★★★★ | 低 | `summarize` |
| 8.6 memory 沉淀 | ★★★ | 低 | `extract` |
| TB 缺陷源拉取 | ★★★ | 低 | `fetch_remote` |
| TB 评论等回复 | ★★★ | 低 | `fill_template` |
| TB 附件分析 | ★★★ | 低 | vision-bridge 已承担 |

#### doc

| 子任务 | 价值 | 风险 | MCP 工具 |
|--------|------|------|----------|
| project_id 解析 | ★★★ | 低 | `parse_project_id` |
| 模块检测 | ★★★ | 低 | `scan_modules` |
| 代码特征扫描 | ★★★ | 低 | `summarize` |
| 质量审视与模板迁移 | ★★★ | 低 | `apply_migration` |
| 正常生成（按章节） | ★★★ | 低 | `fill_template` |
| 99_code_facts_audit | ★★★★ | 低 | `audit_facts` |
| 进度输出 | ★★★ | 低 | `fill_template` |
| 主动 stale 扫描 | ★★★ | 低 | `scan_patterns` |

#### start / fast / list / status

| 子任务 | 入口 | 价值 | 风险 | MCP 工具 |
|--------|------|------|------|----------|
| 历史检索 | start | ★★★★ | 低 | `retrieve_similar` |
| 历史检索 | fast | ★★★ | 低 | `retrieve_similar` |
| 全量读取 index.json | list | ★★★ | 低 | 内置 |
| 关键词搜索 | list | ★★★ | 低 | `scan_patterns` |
| 表格化输出 | list | ★★★ | 低 | `fill_template` |
| 状态查询 | status | ★★★ | 低 | 内置 |
| scan-verdict 批量扫描 | status | ★★★ | 低 | `extract` |

### 工具优先级（v0.8 严格单一档）

#### Tier 1 必装（5 个，核心覆盖）

```python
"retrieve_similar":   ★★★★★ 频次最高（init/plan/review/log/start/fast/readme）
"summarize":          ★★★★★ 价值最高（log 0/1/2/deepcheck/doc/init）
"audit_facts":        ★★★★  (plan/doc 99_code_facts)
"fill_template":      ★★★★  (review/audit/audit/readme/list/status/doc)
"extract":            ★★★    (log 8.6/status)
```

#### Tier 2 增强（9 个，覆盖 95% 入选场景）

```python
"scan_patterns":      ★★★  (review grep/doc stale/list)
"trace_refs":         ★★★  (review 引用追溯)
"diff_summary":       ★★★  (review/deepcheck/log 阶段 1)
"apply_migration":    ★★★  (plan/code schema)
"fetch_remote":       ★★★  (log TB 拉取)
"generate_filename":  ★★★  (readme)
"select_template":    ★★★  (readme)
"parse_project_id":   ★★★  (doc)
"scan_modules":       ★★★  (doc)
```

#### Tier 3 不做（v0.6 灰区工具，v0.8 直接排除）

```python
"describe_link":      ★★★ 中风险（链路图）
"scan_misuse":        ★★★ 中风险（接口误用）
"trace_path":         ★★★ 中风险（端到端路径）
"generate_report":    ★★★ 中风险（6 维度报告）
"classify_intent":    ★★★ 中风险（意图识别）
```

#### Tier 4 不做（边际价值低）

```python
"find_dead":          ★★  (code 步骤，价值低)
"fill_branches":      ★★  (code 步骤，价值低)
"format":             ★★  (code 步骤，价值低)
"dedupe":             ★★  (merge，价值低)
"fill_template_v2":   ★★  (readme 风险章节，<3★)
"fix_suggestion":     高风险  (log 修复方案)
"root_cause":         高风险  (log 根因假设)
"architecture_decision":  高风险  (plan)
"adversarial_check":  高风险  (review/deepcheck)
"final_verdict":      高风险  (audit)
```

### 决策点备忘（v0.8 完整版）

#### ✅ 已定（用户拍板）

- 走方案 A 路径（暂不建 cheap-research）
- 白名单 + 强制 schema
- **单闸门**：价值 ≥ 3 ★ + 低风险 = 唯一入选
- 模型配置严格复用 vision-bridge
- 强证据 + 降级路径与 vision-bridge 对齐
- 一键安装/卸载集成
- **零灰区原则**：中风险不做

#### ❓ 未决（未来实施时讨论）

- 实际启动哪个 provider？（OpenAI / Claude Haiku / 本地 Ollama / Qwen / DeepSeek）
- 是否共享 `_lib/platform_entry.py`（与 vision-bridge 共用 OpenAI 兼容协议）
- cheap-research 与 vision-bridge 安装是否合并（统一 mcp_install.sh）
- 何时启动 cheap-research 实施（用户的"额度吃紧"信号触发）

#### 🗑️ 已废弃（v0.8 撤回）

- ~~灰区 6 个子任务的"主会话复核"机制~~（用户已决定中风险不做）
- ~~Tier B 灰区工具：describe_link / scan_misuse / trace_path / generate_report / classify_intent~~（不做）

---

## 实施路线图（v0.9 新增 · 用户拍板启动）

> 用户 2026-07-31 决策升级：**启动 cheap-research 方案**。
>
> 基于 v0.1~v0.8 研究结论，给出可执行实施计划。

### 阶段划分（6 阶段 + 7.5 天）

| 阶段 | 名称 | 工时 | 关键产出 |
|------|------|------|----------|
| **阶段 0** | 脚手架 | 0.5 天 | `mcp/cheap-research/` 框架 + install.sh + uninstall.sh + 1 个 demo 工具 |
| **阶段 1** | 核心 5 工具 | 2 天 | `retrieve_similar` / `summarize` / `audit_facts` / `fill_template` / `extract` |
| **阶段 2** | 增强 9 工具 | 3 天 | `scan_patterns` / `trace_refs` / `diff_summary` / `apply_migration` / `fetch_remote` / `generate_filename` / `select_template` / `parse_project_id` / `scan_modules` |
| **阶段 3** | 集成增补 | 0.5 天 | `references/mcp_integration.md` ⑦ 段 + `mcp_per_step.md` 列 + `cheap-subagent-research.md` 同步 |
| **阶段 4** | 测试 + A/B 对照 | 1 天 | 端到端用例 + 1~2 工单对照报告 |
| **阶段 5** | 灰度上线 | 0.5 天 | fast 模式灰度 → start 模式灰度 → 全量 |
| **合计** | — | **~7.5 天** | — |

### 6 个里程碑（M1~M6）

| 里程碑 | 完成判定 | 验收标准 |
|--------|---------|---------|
| **M1**（阶段 0 末） | dev_repo 有 `mcp/cheap-research/` 框架 + 1 个 demo 工具可调通 | `mcp__cheap-research__summarize(...)` 调用成功 |
| **M2**（阶段 1 末） | 5 个 Tier 1 工具全部可调通 + 端到端测试用例通过 | 5 个工具单击测试 + 模拟工单流跑通 |
| **M3**（阶段 2 末） | 14 个工具全部可调通 | 14 个工具单击测试 + 强证据 4 种状态用例 |
| **M4**（阶段 3 末） | mcp_integration.md 增补落地 | 文档可读 + 与 vision-bridge 风格一致 |
| **M5**（阶段 4 末） | A/B 对照报告产出 | baseline vs 集成版 token/质量/耗时对比 |
| **M6**（阶段 5 末） | 灰度上线成功 | fast 模式 5 工单通过 + start 模式 2 工单通过 + 全量开放 |

### 阶段 0 详细：脚手架（0.5 天）

**任务清单**：
- [ ] 复制 `mcp/vision-bridge/` 完整结构到 `mcp/cheap-research/`
- [ ] 全局替换 `vision-bridge` → `cheap-research`（包括 server.py、install.sh、uninstall.sh、README.md、pyproject.toml）
- [ ] 删除 `providers/local_ocr.py` 替换为 `providers/local_ollama.py`（占位 stub）
- [ ] 删除 `config.json` 三件套中的 `video_frames` 字段（新 `config.json` 字段：provider / base_url / api_key / model / timeout / max_tokens / temperature / default_focus）
- [ ] 简化 `server.py`：只保留 1 个 demo 工具（`summarize`）
- [ ] 更新 `README.md` 标题与说明
- [ ] 测试 install.sh：装到 `~/.claude/skills/icode/mcp/cheap-research/` + 注册到 `~/.claude.json`
- [ ] 测试 uninstall.sh：移除注册
- [ ] 重启 Claude Code 验证 `mcp__cheap-research__summarize` 可见

**产出物**：
- `mcp/cheap-research/server.py`（含 1 个 demo 工具）
- `mcp/cheap-research/providers/{base, openai_compat, local_ollama}.py`
- `mcp/cheap-research/config.example.json`
- `mcp/cheap-research/install.sh`
- `mcp/cheap-research/uninstall.sh`
- `mcp/cheap-research/requirements.txt`
- `mcp/cheap-research/pyproject.toml`
- `mcp/cheap-research/README.md`

**验收标准**：
- `vim ~/.claude/skills/icode/mcp/cheap-research/config.json` 填三件套
- 重启 Claude Code
- 调 `mcp__cheap-research__summarize(text="hello", max_tokens=50)` 返回结构化结果

### 阶段 1 详细：核心 5 工具（2 天）

**任务清单**（按工时排序）：

| 工具 | 工时 | 输入 schema | 输出 schema |
|------|------|-------------|------------|
| `summarize` | 0.5 天 | `{text: str, max_tokens: int?, focus: str?}` | `{answer: str, confidence: float, model: str, cost: float}` |
| `retrieve_similar` | 0.5 天 | `{query: str, k: int?, source: str?}` | `{items: [{content, score, source}], model: str}` |
| `fill_template` | 0.5 天 | `{template: str, data: dict}` | `{answer: str, confidence: float, model: str}` |
| `extract` | 0.3 天 | `{text: str, schema: dict}` | `{parsed: dict, confidence: float, model: str}` |
| `audit_facts` | 0.2 天 | `{repo_path: str, focus: str?}` | `{facts: [str], source_files: [str], model: str}` |

每个工具实现：
- 输入 schema 强制（FastMCP）
- 输出 schema 强制（field 模板）
- 兜底：config 缺字段 → `{error: "未配置"}`
- 错误：API 失败 → `{error: "..."}`，主会话识别后走 Agent(model=haiku)
- 调用：复用 `providers/openai_compat.py` 单文件调用

**端到端测试用例**：
- 5 个工具单击各跑 1 次
- 1 个完整工单流（init → plan → 调 summarize → 拿到结果）
- 4 种强证据状态覆盖（未装 / 缺字段 / 完整 / API 失败）

### 阶段 2 详细：增强 9 工具（3 天）

**任务清单**：

| 工具 | 工时 | 类型 | 关键依赖 |
|------|------|------|----------|
| `scan_patterns` | 0.4 天 | 纯文本 | — |
| `trace_refs` | 0.4 天 | 纯文本 + 文件系统 | — |
| `diff_summary` | 0.3 天 | 纯文本 | — |
| `apply_migration` | 0.3 天 | 文件系统 | — |
| `fetch_remote` | 0.5 天 | HTTP 客户端 | httpx |
| `generate_filename` | 0.2 天 | 纯文本 | — |
| `select_template` | 0.3 天 | 纯文本 | — |
| `parse_project_id` | 0.3 天 | 文件系统 | — |
| `scan_modules` | 0.3 天 | 文件系统 | — |

**端到端测试用例**：
- 9 个工具单击各跑 1 次
- 集成场景：plan 流程（schema 迁移 + 历史 ADR + 跨工程代码事实审计）
- 集成场景：log 流程（TB 拉取 + 阶段 0 + 阶段 1 + 阶段 2 + 8.6 memory）

### 阶段 3 详细：集成增补（0.5 天）

**任务清单**：
- [ ] `references/mcp_integration.md` 增补 ⑦ 段（与 vision-bridge 风格一致）
- [ ] `references/mcp_per_step.md` 增补 cheap-research 列（10 行 × 1 列）
- [ ] `references/cheap-subagent-research.md` 同步实施进度（v1.0 状态）
- [ ] `mcp/cheap-research/README.md` 写完整（与 vision-bridge 风格一致）

### 阶段 4 详细：测试 + A/B 对照（1 天）

**任务清单**：
- [ ] 单元测试：14 个工具各 3 个用例（正常 / 缺字段 / 错误）
- [ ] 集成测试：1 个完整 init → plan → code → deepcheck → audit 流程
- [ ] A/B 对照：选 1~2 个已有工单（从 `~/.claude/icode_data/` 找）
  - 跑 baseline（全部主会话模型）
  - 跑集成版（cheap-research 接管 22 个子任务）
  - 对比：token 消耗 / 质量分（critical issue 数） / 端到端耗时
- [ ] 产出 A/B 对照报告

**A/B 对照报告模板**：
```markdown
## A/B 对照：工单 <name>

| 维度 | baseline（纯主会话） | 集成版（cheap-research 接管） | 节省 |
|------|---------------------|---------------------------|------|
| 总 token | 100k | 30k | 70% |
| 端到端耗时 | 5 分钟 | 4 分钟 | 20% |
| Critical issue 数 | 0 | 0 | 平 |
| 工作流产物质量 | 完整 | 完整 | 平 |
| 失败成本 | 0 | 0 | 平 |

**结论**：MCP 价值验证 / 风险提示
```

### 阶段 5 详细：灰度上线（0.5 天）

**任务清单**：
- [ ] 灰度阶段 A：fast 模式 5 工单（仅 fast 路径用 cheap-research）
  - 通过：进入阶段 B
  - 失败：rollback（卸载 cheap-research） + 复盘
- [ ] 灰度阶段 B：start 模式 2 工单（full 模式 + cheap-research）
  - 通过：进入阶段 C
  - 失败：rollback + 复盘
- [ ] 灰度阶段 C：全量开放（所有 7 步 + 入口可用）
- [ ] 监控 1 周：观察 critical issue 是否归因 cheap-research

### 关键决策点（10 项 · 实施前必须确认）

#### 决策 1：启动哪个 provider？

| 选项 | 优势 | 劣势 |
|------|------|------|
| GPT-4o-mini | 全网最便宜、能力稳 | 需 OpenAI KEY、网络依赖 |
| Claude Haiku | Anthropic 官方便宜 | 仍吃额度 |
| 本地 Ollama（Qwen 2.5 7B） | 零成本、零外网 | 需本地资源 |
| DeepSeek-V3 | 极便宜 | 网络依赖 |
| Qwen Plus | 国内便宜、稳定 | 需 KEY |

**建议**：先 `local_ollama`（Qwen 2.5 7B 配置默认）作为零成本 demo；之后切换 `openai_compat`（GPT-4o-mini）作为生产。

#### 决策 2：阶段 1 是一次到位 5 工具，还是先 1 工具试水？

- **试水**（先 1 工具）：稳、风险低，但慢
- **一次到位**（5 工具）：快、并行反馈，但风险高

**建议**：阶段 1 一次到位 5 工具（脚手架已稳定，5 工具都是相似模式）。

#### 决策 3：实施起点 dev_repo only 还是 dual_repo？

按 [[icode-skill-edit-dev-repo-only]] 规则：默认 dev_repo only。

**建议**：dev_repo only，阶段 4 A/B 对照需要时再同步到 `~/.claude/skills/icode/`。

#### 决策 4：是否共享 `_lib/platform_entry.py`（与 vision-bridge）？

- **共享**：减少重复代码，但需统一 schema
- **独立**：清晰边界，但重复

**建议**：v0.1 阶段独立（避免改动现存 vision-bridge），v1.0 之后再考虑共享。

#### 决策 5：与 vision-bridge 配置 schema 是否一致？

- **完全一致**：复用 `~/.claude/.../config.json` 同结构
- **独立**：cheap-research 自己的 `config.json`

**建议**：cheap-research 独立 `config.json`（不污染 vision-bridge 的），但字段结构一致。

#### 决策 6：灰度策略：先 fast 模式 → start 模式，对吗？

- **fast 优先**：快、轻
- **start 优先**：full 模式覆盖广

**建议**：fast 优先（fast 模式吃 cheap-research 价值密度最高：log、doc、readme）。

#### 决策 7：A/B 对照样本选哪些？

- 选已有工单（从 `~/.claude/icode_data/` 历史）
- 候选：1 个中等大小工单（5~15 文件改动）+ 1 个 log 入口工单

**建议**：从历史索引找一个 stable 完成态工单，重跑一遍。

#### 决策 8：监控指标有哪些？

- 工具调用次数 / 工单
- critical issue 数 / 工单
- 端到端耗时 / 工单
- token 消耗 / 工单
- rollback 次数 / 月

**建议**：每工单记录 5 项指标，1 周后产出对比报告。

#### 决策 9：rollback 机制？

- 阶段 5 灰度失败：卸载 cheap-research，工作流回退
- 阶段 4 A/B 对照失败：保留 dev_repo 改动，不同步到已安装目录
- 阶段 1~3 失败：dev_repo 改动回退（git revert）

**建议**：阶段 0~3 全部走 git 分支（v0.9-plan），上线前合并到 main。

#### 决策 10：是否需要 v0.10 文档（实施后状态）？

- **需要**：文档同步实施进度
- **不需要**：实施完成后重写 docs

**建议**：实施完成后 v1.0 落地，重写 docs，从 v0.9 升级到 v1.0。

### 5 大风险与 rollback

| 风险 | 描述 | rollback |
|------|------|----------|
| **R1：与 vision-bridge 配置冲突** | 共用字段 / config 路径冲突 | cheap-research 独立 config.json，不与 vision-bridge 共用 |
| **R2：实施时序混乱** | 动到 6 大 MCP 子流程 | 阶段 0~3 走 git 分支，灰度前不合 main |
| **R3：A/B 对照无法跑** | 没真实工单样本 | 选 1 个合成工单（demo）+ 1 个真实工单 |
| **R4：灰度上线时影响正常工作流** | critical issue 归因 cheap-research | 阶段 5 灰度失败 → 立即卸载 cheap-research |
| **R5：rollback 机制不紧密** | 出问题无法快速回退 | 阶段 0~3 全部走 git 分支 + 软链接式 install.sh |

### 关键里程碑验收（M1~M6 量化）

| 里程碑 | 验收测试 | 通过标准 |
|--------|---------|---------|
| M1 | 1 个 demo 工具调用 | 返回结构化结果 |
| M2 | 5 工具单击 + 1 工单流 | 端到端跑通 |
| M3 | 14 工具单击 + 4 种强证据状态 | 全部覆盖 |
| M4 | 文档可读性测试 | 与 vision-bridge 风格一致 |
| M5 | A/B 对照报告 | 集成版 token 节省 ≥ 50% |
| M6 | 灰度 7 工单 | 0 critical issue |

### 实施后状态（v1.0 落地后）

- [x] `mcp/cheap-research/` 完整工程（含 14 工具）
- [x] `mcp/install.sh` + `mcp/uninstall.sh` 自动扫描
- [x] `references/mcp_integration.md` 增补 ⑦ 段
- [x] `references/mcp_per_step.md` 增补列
- [x] `references/cheap-subagent-research.md` 升级到 v1.0
- [x] A/B 对照报告归档
- [x] 灰度上线 1 周报告

### 决策点备忘（v0.9 完整版）

#### ✅ 已定（用户拍板）

- 启动 cheap-research 方案
- 单闸门 + 零灰区
- 14 个 MCP 工具（Tier 1: 5 + Tier 2: 9）
- 22 个入选子任务
- 强证据 + 降级路径与 vision-bridge 对齐
- 一键安装/卸载集成
- 模型配置严格复用 vision-bridge 模式

#### ❓ 待实施前确认（10 决策点）

> **2026-07-31 用户全部批准**（按建议默认值落地）

1. 启动哪个 provider（**✅ 跟 vision-bridge 一样**——用户自己配 URL/KEY/模型，不推荐）
2. 阶段 1 一次到位还是试水（**✅ 一次到位 5 工具**）
3. 实施起点 dev_repo only（**✅ 只动 dev_repo**，等用户要求再同步）
4. 是否共享 `_lib/platform_entry.py`（**✅ 独立 providers/**，不共享）
5. 与 vision-bridge 配置 schema 是否一致（**✅ 独立 config.json**，但字段结构对齐）
6. 灰度策略 fast 优先（**✅ fast 模式 → start 模式 → 全量**）
7. A/B 对照样本选哪些（**✅ 1 stable 完成态工单 + 1 log 入口工单**）
8. 监控指标（**✅ 5 项**：工具调用次数 / critical issue / 端到端耗时 / token 消耗 / rollback 次数）
9. rollback 机制（**✅ git 分支 v0.9-plan + 软链接式 install.sh**）
10. 是否需要 v1.0 文档重写（**✅ 要**，实施完成后 v0.9 → v1.0）

#### 🚀 10 决策全部批准 → 进入实施预备态

**实施前置条件**：
- [ ] git 分支 `v0.9-plan` 创建（阶段 0~3 走该分支）
- [ ] dev_repo 现有 main 分支稳定（不要动）
- [ ] `~/.claude/icode_data/` 至少 1 个 stable 完成态工单 + 1 个 log 入口工单（A/B 对照样本）

**启动指令**（用户后续给出）：
- 启动阶段 0：复制 `mcp/vision-bridge/` → `mcp/cheap-research/` + 替换 + 安装 demo
- 启动阶段 1：5 工具一次到位
- 启动阶段 2：9 工具分批
- ……

**申请启动时**：
"启动 cheap-research 阶段 0"（或更高阶段）
