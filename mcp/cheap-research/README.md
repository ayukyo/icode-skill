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
| **gate 真源** | `mcp/cheap-research/gates.json` | catalog 内全部 gate 的 eligibility condition + 阈值常量（`long_text_threshold_bytes=8192` / `dedup_min_functions=50` / `tb_comment_extract_min=8` / `merge_min_rounds=2` / `max_input_bytes_per_call=8000`）。单块上限与 `summarize` 消费上限对齐，超长输入先按字节切块并保序汇总，禁止把 `truncated=true` 当完整结果。**阈值只从这里读**，禁止在 step 文档/脚本写死 |
| **trace 轨迹** | `{ICODE_OUT_DIR}/.mcp_gate_trace.jsonl` | 每 gate 一条最终判定（`gate_id`/`eligible`/`evidence`/`decision`/`attempted`/`result`/`at`）。`decision` 词表 = `called`/`cache_hit`/`skipped_not_eligible`/`skipped_stage_not_reached`/`degraded_after_attempt`/`unavailable_before_call`；eligible=true 只允许 `called` / `cache_hit` / `degraded_after_attempt` / `unavailable_before_call`（`degraded_after_attempt` 须 `attempted=true` 且 `result=error\|empty\|timeout`；未暴露工具用 `unavailable_before_call`，须 `attempted=false/result=unavailable` 及工具发现、替代结果引用），`skipped_*` 仅用于 eligible=false。**不保存**工具完整结果/日志正文/API key/Cookie/设备凭据 |
| **cache 去重** | `{ICODE_OUT_DIR}/.cheap_research_cache.json` | 有效命中（`tool + args_hash`，source mtime 校验）→ gate 记 `decision=cache_hit`，**不重复调用**，等价履行 |
| **运行时校验器** | `python3 tools/lint_mcp_coverage.py <out_dir> [--step <step>] [--strict] [--json]` | step 转换前跑：eligible 未履行 / missing gate / degraded-without-attempt / trace schema error / 敏感数据 → 退出码 1 阻断 |

**eligible 语义（业务义务 ≠ 工具可用性）**：eligible 只由阈值/范围/轮次/模式等业务事实计算；`cheap_available` 是必填可用性事实，只决定 decision。达到业务阈值而工具不可用 → eligible=true + `unavailable_before_call`/`degraded_after_attempt`（或有效 `cache_hit`）+ 真实人工等价履行（见 [审查证据合同](../../references/inspection_evidence.md)），禁止 `skipped_not_eligible`；仅业务不达标才允许 skip。

**资格合同版本（`contract_version`）**：机器真源为本文件旁的 `gates.json`（当前 `contract_version: 2`）。trace 行可带 `contract_version`（int 1|2），缺省继承 metadata `mcp_gate_contract_version`（vNext 出生由 `icode_control.py` 写入且受保护）；两者皆无 = 合同 v1 旧语义（`cheap_available`/`rg_available` 参与 eligibility），存量历史记录按其原始解释判定、不追溯改判。v2 下 `review.dedup` 声称低于阈值必须基于可信枚举：`rg_available=true`（ripgrep 本身可用）或可选 evidence `enumeration_verified=true`（受支持的替代枚举已核验）；ripgrep 与替代枚举都不可用时写可选 evidence `enumeration_unverified=true` 声明「枚举未知、保留债务」——该状态下 function_count 不可核验（如实记录尝试值或 0），业务义务推定成立，唯一合法记录是 eligible=true + unavailable_before_call/degraded_after_attempt（called/cache_hit 需可信枚举、skip 均非法），该约束与尝试值是否达到阈值无关；校验报告以 `enumeration_debt` 计数显式暴露未解决的枚举/去重债务；控制面按 trace 时间顺序**逐行推演**工单级余额（债务状态产生后，只有通过资格与证据校验的有效最终记录才清偿）——余额跨步骤持续存在（review 产生债务后，后续只检查 merge/audit 的推进仍报告该余额），清偿要求债务 gate 出现**通过资格与证据校验的有效最终记录**且属于当前工单——流程要求调用前先写 pending，中断/未完成/非法的重做行不清偿；unavailable/degraded 清偿记录还须机器可验证的人工等价证据（`availability.evidence_ref` 指向工单目录内的人工去重报告，trace 须携带 attempt，报告须绑定当前工单、attempt、受影响仓库范围和源码，并通过 manual_dedup_evidence 校验；可信枚举低于阈值的业务不适用记录可直接清偿；清偿记录须覆盖产生债务的全部受影响仓库与范围（**债务侧**未声明范围按**全仓**保守要求覆盖；**清偿/复检侧**必须显式声明 `repo_review_scopes`，未声明时实际范围未知、不构成任何覆盖；显式范围按目录包含关系逐仓比较，同一仓库连续债务范围累积），「函数已枚举」≠「去重债务已清偿」；review 报告在登记 `review_dedup_evidence` 时即完成完整校验并写入受事件链保护的回执，后续 code 修改或按计划删除源码以该回执维持清偿；债务也可由 `deepcheck.dedup` 真实成功、或该 deepcheck attempt 成功结束且复检报告已受控登记（`deepcheck_dedup_evidence`）的人工复检按相同仓库与逐仓范围清偿（未结束/失败/未登记的人工复检不覆盖，范围未知不覆盖））；`availability.evidence_sha256` 可选，只填 `evidence_ref` 时以受控回执摘要完成绑定；trace 含其他工单记录、不可读或丢失（而事件链曾记录未清偿债务）时余额报告为未知（null），不得当作清偿。余额无条件透传到门禁结果、状态推进输出与 state_changed 事件（`gate_enumeration_debt`，0 也显式证明清偿），交付结论可见。合同 v2 工单禁止把 trace 行降级为 v1。旧工单续跑：控制面在下一次 metadata 写入时补齐 metadata 合同字段并记录 `mcp_gate_contract_adopted_at`（显式 UTC）；若采纳时存在升级前未终结的 attempt，历史边界回退为其最早 start（`mcp_gate_contract_history_boundary`），覆盖升级后、采纳前写入的 trace——linter 以该边界区分：边界前历史行保持 v1 解释，边界后续跑新行（含无行级标注者）强制当前合同，该 attempt 须受控终结并重启后按当前合同重新留痕；也可显式追加带 `contract_version: 2` 的新行（同一 gate 以最后一条为准），不得改写历史行。

人工清偿须先登记计划候选清单与当前 review 报告回执；模块级和多仓证据格式、逐仓范围以及函数数最大值规则见 [审查步骤](../../steps/02_review.md) 和 [人工去重证据合同](../../references/inspection_evidence.md)。

流程：**加载 gates.json → 确定性算 eligibility 并写 trace → eligible 先查 cache → 命中写 cache_hit / 未命中实际调用；未暴露则按结构化可用性证据降级 → 更新 trace → step 转换前跑校验器**。详见 [references/thinking_detail.md](../../references/thinking_detail.md)「cheap-research 执行门（gate）流程」段与契约测试 [tests/test_cheap_research_gate_contract.sh](../../tests/test_cheap_research_gate_contract.sh)。

详见 [mcp/cheap-research/server.py](server.py)、工具类型真源 [tools_manifest.json](tools_manifest.json)
与核心契约测试 [tests/](tests/)。
