# cheap-research (MCP server for icode-skill)

**可选增强**：为 icode-skill 提供"便宜 LLM 推理"统一 MCP 接口。

> cheap-research **不锁任何平台**，**不推荐任何 provider**。vision-bridge 模式的镜像版:填三件就能跑。

15 个工具：5 核心 + 10 增强。

---

## 安装（三步）

### 1. 装到全局（一次性）

```bash
cd <你的 icode-skill 仓库>/mcp/cheap-research
./install.sh
```

默认装到 `~/.claude/skills/icode/mcp/cheap-research`。想换位置用环境变量
`CHEAP_RESEARCH_TARGET=/path ./install.sh`。

### 2. 填你的三件套

```bash
vim ~/.claude/skills/icode/mcp/cheap-research/config.json
```

填：
- `provider` —— `openai_compat`（默认）或 `local_ollama`
- `base_url` —— 你平台的 API 端点（**没有默认值，请查平台文档**）
- `api_key`  —— 你平台的 KEY
- `model`    —— 你平台提供的"便宜/快速/轻量"模型名

**没有任何推荐值** —— 你用什么平台、什么模型完全由你决定。

### 3. 重启 Claude Code

调 `mcp__cheap-research__summarize` 即可。

### 修改后增量同步

你在仓库里改了 cheap-research 代码，`cd mcp/cheap-research && ./install.sh` 即可
**增量同步**到 target，无需重装 venv。

---

## 怎么知道该填什么？

任何提供 OpenAI Chat Completions 兼容接口的平台都能用。常见例（非推荐）：

```
# OpenAI 官方 (便宜)
base_url: https://api.openai.com/v1
model:    gpt-4o-mini

# Anthropic Claude (通过 OpenAI 兼容代理)
base_url: <你的代理地址>
model:    <代理提供的便宜模型，如 claude-3-5-haiku>

# 国内厂商 (按你平台真实文档为准)
base_url: https://api.deepseek.com/v1
model:    deepseek-chat

# 本地 Ollama (默认端口 11434)
provider: local_ollama
base_url: http://localhost:11434/v1
model:    qwen2.5:7b
```

> ⚠️ **不假设任何默认值** —— 你用什么平台、什么模型，请按你平台的真实文档填。

---

## provider 选项

| provider | 必填 | KEY | 模型要求 | 性能 |
|----------|------|-----|----------|------|
| `openai_compat` | base_url + api_key + model | 是 | 任意 OpenAI Chat Completions 兼容模型 | 取决于平台 |
| `local_ollama` | model（base_url 可选，默认 `http://localhost:11434/v1`；api_key 可省） | 否 | 已加载的本地模型 | 取决于本地资源 |

切换：在 `config.json` 改 `"provider": "local_ollama"`，重启 Claude Code。

---

## 卸载

```bash
./uninstall.sh         # 只取消注册, 保留代码与 venv
./uninstall.sh --purge # 删 target 目录与 venv
```

---

## 工具签名（15 工具）

详细见 [server.py](server.py)。简要：

**5 核心工具**（LLM 推理）：
- `summarize(text, max_tokens, focus)` — 长上下文压缩
- `retrieve_similar(query, candidates, k)` — 历史工单相似度匹配
- `fill_template(template, data)` — 模板填充
- `extract(text, schema, instruction)` — 结构化提取
- `propose_repo_facts(repo_path, focus, max_files)` — 仓库事实候选（不接管裁决）

**10 增强工具**（含 7 工具型 + 3 LLM 摘要）：
- `describe_capabilities()` — 会话级能力发现（工具清单/provider 配置态/目录与网络边界；不返回密钥、不做网络探测）
- `scan_patterns(patterns, scope_path, exclude_dirs, max_files, max_matches)` — 机械模式匹配
- `trace_refs(symbol, scope_path, max_files, max_refs)` — 符号引用追溯
- `fetch_remote(url, max_chars)` — HTTP 拉取
- `validate_migration_ops(schema_diff, repo_path)` — 迁移 ops 校验与规范化
- `parse_project_id(repo_path)` — 解析 project_id
- `scan_modules(repo_path, max_files)` — 6 级模块检测
- `diff_summary(text_a, text_b, focus, max_tokens)` — 差异摘要
- `generate_filename(context, prefix, max_tokens)` — 文件名生成
- `select_template(context, options, max_tokens)` — 模板选择

**分能力闸门（v1.1）**：15 工具分三类 capability（真源 [tools_manifest.json](tools_manifest.json)）——
`local`（describe_capabilities/scan_patterns/trace_refs/validate_migration_ops/parse_project_id/scan_modules）、`fetch`（fetch_remote）、
`llm`（8 个，需 provider 可用）。本地/网络工具**不因 provider 未配置而整体降级**。

会话首次需要 cheap-research 时先调一次 `describe_capabilities` 并缓存结果；其中
`configured` 只表示配置字段齐全，`configured_unverified` 仍需以首次真实调用结果确认。
该发现调用不属于业务产物转换，不写 per-step gate trace。

### 安全与运行边界

- 所有工具提供含 `answer/error_code/error/model` 的 MCP `outputSchema`，并声明 `readOnlyHint` / `destructiveHint` / `openWorldHint`；测试会拒绝退化成无字段约束的空壳 schema。
- Python MCP SDK 锁定在已验证的 `>=1.29,<2`；2.x API 迁移须单独评估，安装时不静默跨大版本。
- `fetch_remote` 使用流式读取（5 MiB 上限），最多跟随 5 次重定向且逐跳解析公网 IP；实际连接固定到该 IP，并保留原 Host/HTTPS SNI，关闭环境代理，从而阻断 DNS 预检与建连之间的 rebinding。返回 requested/final URL、重定向链、SHA-256、ETag、Last-Modified 与抓取时间。远端正文始终标 `trust_level=untrusted`。
- 本地扫描拒绝 symlink 文件及范围逃逸。团队/生产环境建议在 `config.json` 填 `allowed_roots`（或 `CHEAP_RESEARCH_ALLOWED_ROOTS`）；空列表为了兼容旧安装，仍由调用者选择路径。
- LLM provider 在 server 生命周期内复用连接池，默认并发上限为 3，配置热替换和 server 退出时显式关闭；外发提示统一把网页、日志、代码和文档视为不可信数据。
- 模型没有校准数据时 `confidence=null`，禁止用固定伪置信度冒充证据。

session 模型只看工具返回的结构化 dict，**不直接调用 cheap-research 配置的 LLM provider**——但 provider 调用确实发生在本 MCP server 进程内（`config.json` 配置的 base_url/api_key/model），数据出境闸门（`scan_sensitive`）与 `truncation`/`source_digest` 元数据即用于审计该外发路径。

企业邮件正文、地址、HTML、表格、图片、附件、日志及其派生摘要默认不进入 `llm` 或 `fetch` 能力。显式网页邮件链接先由 `email-evidence-intake` 复用已登录浏览器并限定目标阅读窗，再由 `tools/email_intake.py` 固定邮件身份、MIME 覆盖和附件证据；`icode-mail-observe` 只补充无人值守、邮箱范围搜索或显式 IMAP 场景。已经落入授权证据根的数据只能由本 MCP 的确定性 `local` 工具处理。只有与邮件内容分离的公开主题可直接研究；外发邮件派生内容需要用户明确确认 provider 与披露范围。`describe_capabilities` 会返回这一 `data_scope_boundary`，供 ICODE 路由层在调用前检查。

### 与 ICODE 本地 MCP 的职责边界

`cheap-research` 只负责低成本压缩、候选提取、草稿生成和不可信公网材料抓取。文件身份/时间线、Git/构建来源、设备观测、MCP 健康、步骤路由和持久本地索引分别由 `icode-evidence`、`icode-workspace`、`icode-device-observe`、`icode-mcp-health`、`icode-mcp-policy`、`icode-local-index` 负责。cheap-research 可以压缩它们带来源回指的长输出，但不得代理其工具、去掉回指或把模型摘要升级为证据结论。机器边界见 [tools_manifest.json](tools_manifest.json) 的 `responsibility_boundary`。

> **输入纯净度建议**：`llm` 类工具依赖 LLM 输出能严格按 schema 输出 JSON。server.py 的 _parse_response 有 4 道容错闸门：剥离 think 标签 → 提取 json 代码块 → brace-matching 提取最外层 {...} → 修复数组元素间缺逗号。**实测在 prompt 里加一句"只输出 JSON，不要前后缀文本"显著降低容错失败率**——容错是 last resort，不是默认行为。


---

## SKILL 端约定（与 icode 主工作流）

- **cheap-research 装好后**：长上下文压缩 / 模板填充 / 信息提取等场景优先走 `mcp__cheap-research__*`
- **未装 cheap-research**：走 Agent(model="haiku") 兜底（不阻塞）
- **不接管决策**：3 质疑者对抗 / 架构决策 / 终审裁决 / 修复方案一律不走本工具

### gate / trace / cache 三者关系（机器化执行门）

cheap-research 的强证据执行点由三层机器机制承载，文档只解释语义、不重复发明阈值：

| 层 | 载体 | 作用 |
|----|------|------|
| **gate 真源** | `mcp/cheap-research/gates.json` | 11 个 gate 的 eligibility condition + 阈值常量（`long_text_threshold_bytes=8192` / `dedup_min_functions=50` / `tb_comment_extract_min=8` / `merge_min_rounds=2` / `max_input_bytes_per_call=8000`）。单块上限与 `summarize` 消费上限对齐，超长输入先按字节切块并保序汇总，禁止把 `truncated=true` 当完整结果。**阈值只从这里读**，禁止在 step 文档/脚本写死 |
| **trace 轨迹** | `{ICODE_OUT_DIR}/.mcp_gate_trace.jsonl` | 每 gate 一条最终判定（`gate_id`/`eligible`/`evidence`/`decision`/`attempted`/`result`/`at`）。`decision` 词表 = `called`/`cache_hit`/`skipped_not_eligible`/`skipped_stage_not_reached`/`degraded_after_attempt`；eligible=true 只允许 `called` / `cache_hit` / `degraded_after_attempt`（`degraded_after_attempt` 须 `attempted=true` 且 `result=error\|empty\|timeout`），`skipped_*` 仅用于 eligible=false。**不保存**工具完整结果/日志正文/API key/Cookie/设备凭据 |
| **cache 去重** | `{ICODE_OUT_DIR}/.cheap_research_cache.json` | 有效命中（`tool + args_hash`，source mtime 校验）→ gate 记 `decision=cache_hit`，**不重复调用**，等价履行 |
| **运行时校验器** | `python3 tools/lint_mcp_coverage.py <out_dir> [--step <step>] [--strict] [--json]` | step 转换前跑：eligible 未履行 / missing gate / degraded-without-attempt / trace schema error / 敏感数据 → 退出码 1 阻断 |

流程：**加载 gates.json → 确定性算 eligibility 并写 trace → eligible 先查 cache → 命中写 cache_hit / 未命中实际调用 → 更新 trace → step 转换前跑校验器**。详见 [references/thinking_core.md](../../references/thinking_core.md)「cheap-research 执行门（gate）流程」段与契约测试 [tests/test_cheap_research_gate_contract.sh](../../tests/test_cheap_research_gate_contract.sh)。

详见 [mcp/cheap-research/server.py](server.py)、工具类型真源 [tools_manifest.json](tools_manifest.json)
与核心契约测试 [tests/](tests/)。
