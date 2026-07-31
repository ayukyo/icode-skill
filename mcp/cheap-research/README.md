# cheap-research (MCP server for icode-skill)

**可选增强**：为 icode-skill 提供"便宜 LLM 推理"统一 MCP 接口。

> cheap-research **不锁任何平台**，**不推荐任何 provider**。vision-bridge 模式的镜像版:填三件就能跑。

14 个工具：5 核心 + 9 增强。

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
| `local_ollama` | base_url + model（api_key 任意） | 否 | 已加载的本地模型 | 取决于本地资源 |

切换：在 `config.json` 改 `"provider": "local_ollama"`，重启 Claude Code。

---

## 卸载

```bash
./uninstall.sh         # 只取消注册, 保留代码与 venv
./uninstall.sh --purge # 删 target 目录与 venv
```

---

## 工具签名（14 工具）

详细见 [server.py](server.py)。简要：

**5 核心工具**（LLM 推理）：
- `summarize(text, max_tokens, focus)` — 长上下文压缩
- `retrieve_similar(query, candidates, k)` — 历史工单相似度匹配
- `fill_template(template, data)` — 模板填充
- `extract(text, schema, instruction)` — 结构化提取
- `audit_facts(repo_path, focus, max_files)` — 代码事实审计

**9 增强工具**（含 6 工具型 + 3 LLM 摘要）：
- `scan_patterns(patterns, scope_path, exclude_dirs, max_files, max_matches)` — 机械模式匹配
- `trace_refs(symbol, scope_path, max_files, max_refs)` — 符号引用追溯
- `fetch_remote(url, max_chars)` — HTTP 拉取
- `apply_migration(schema_diff, repo_path)` — Schema 迁移操作生成
- `parse_project_id(repo_path)` — 解析 project_id
- `scan_modules(repo_path, max_files)` — 6 级模块检测
- `diff_summary(text_a, text_b, focus, max_tokens)` — 差异摘要
- `generate_filename(context, prefix, max_tokens)` — 文件名生成
- `select_template(context, options, max_tokens)` — 模板选择

session 模型只看工具返回结构化 dict，**永远不直接调 LLM API**。

---

## SKILL 端约定（与 icode 主工作流）

- **cheap-research 装好后**：长上下文压缩 / 模板填充 / 信息提取等场景优先走 `mcp__cheap-research__*`
- **未装 cheap-research**：走 Agent(model="haiku") 兜底（不阻塞）
- **不接管决策**：3 质疑者对抗 / 架构决策 / 终审裁决 / 修复方案一律不走本工具

详见 [references/cheap-subagent-research.md](../../references/cheap-subagent-research.md)。
