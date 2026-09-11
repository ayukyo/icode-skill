# 步骤 install — ICODE / 共享技能 / MCP 一键安装（独立步骤）

**命令**: `/icode install`
**产出**: 所选宿主的 ICODE 与共享技能目录；ICODE 自管 DOCX Python runtime；可选 MCP 注册和运行环境
**会话**: 主会话
**定位**: **独立步骤**，不创建 `.icode_output_N/`、不写 `.ico_metadata.json`、不参与 1~6 流程推进。与 `doc` / `docx` / `status` / `list` 并列。

## 用途

`/icode install` 是开源用户的统一安装入口：先安装或更新 ICODE 本体，再按 `skill-packs/manifest.json` 安装全部顶层共享技能，最后安装所选 MCP。新 clone、本机升级、新机器和 CI 初始化均使用同一入口；`mcp/install.sh` 只保留为 MCP 专项维护入口。

**内置能力与独立 Skill 的安装边界**：`tools/evidence_intake.py`、`tools/email_intake.py`、`tools/document_intake.py`、`tools/media_router.py`、`debug_catalog.py`、`runtime_baseline.py`、`verification_debt.py`、`learn.py`、`scripts/submission_guard.py handoff` 及 `steps/learn.md` 都属于 ICODE 本体，随 ICODE 目录一次复制到所选宿主，**不**写入 `skill-packs/manifest.json`。manifest 声明当前 16 个需要在技能根顶层独立发现的跨项目共享 Skill（含邮件证据与 MCU 软硬件契约审计）；`--client all` 会同时安装 ICODE 本体和全部共享 Skill 到 Claude Code、Codex。

**当前 13 个声明的 MCP**：

| MCP | 形态 | 对 icode 工作流的增益 | KEY |
|---|---|---|---|
| **sequential-thinking** | npm | 分级思考 reasoning gate（L2/L3 步骤才用，本步骤 L0 不用） | ❌ |
| **vision-bridge** | Python venv | 图片/视频理解（步骤 0 init/6 audit） | ✅ 推荐装（需配三件套） |
| **memory** | npm | 跨工单记忆 | ❌ |
| **context7** | npm | 库文档实时查询，步骤 0/1/4 | ❌ |
| **playwright** ⚠️ | npm | 浏览器自动化，步骤 5/6（**仅前端项目**） | ❌ |
| **cheap-research** | Python venv | 便宜 LLM 推理降本（各步骤正文执行点的候选/压缩/结构化提取子任务），未装走 Agent(model="haiku") 兜底 | ✅ 推荐装（LLM 类工具需配三件套；本地/网络类工具不依赖） |
| **icode-evidence** | Python venv | 文件 hash/行号回指、日志时间线、文档语料清单 | ❌ |
| **icode-workspace** | Python venv | 多 Git 根、worktree、构建输入和制品来源观测 | ❌ |
| **icode-device-observe** | Python venv | 命名 profile 的 SSH/ADB/fixture 只读设备观测 | ❌ |
| **icode-mcp-health** | Python venv | 安装后 manifest、入口、工具 schema 和敏感字段健康检查 | ❌ |
| **icode-mcp-policy** | Python venv | step→server/tool 路由与最小权限机器真源 | ❌ |
| **icode-local-index** | Python venv | 大型源码/日志/文档的可重建 SQLite FTS 索引 | ❌ |
| **icode-mail-observe** | Python venv | 可选的无人值守/邮箱范围搜索 IMAP 适配器；普通网页链接直接复用已登录浏览器 | ❌（仅启用 IMAP 时需自行配置邮箱凭据） |

> 完整说明见各 `mcp/<name>/README.md`。**vision-bridge 需配 KEY（三件套）才能用**；cheap-research 分三类 capability——`local` 6 个（describe_capabilities / scan_patterns / trace_refs / validate_migration_ops / parse_project_id / scan_modules）+ `fetch` 1 个（fetch_remote）**不依赖 LLM provider，未配 KEY 也照常可用**，仅 `llm` 8 个（summarize / retrieve_similar / fill_template / extract / propose_repo_facts / diff_summary / generate_filename / select_template）需配三件套。其余无需 KEY 即可安装。
>
> **⚠️ playwright 警告**：24 个工具 schema 永久加载到 system prompt，**非前端项目 token 性价比低**。建议：前端项目保留，全部项目通用时不装。

## 命令

| 命令 | 行为 |
|---|---|
| `/icode install` | 安装 ICODE、全部共享技能和 13 个 MCP；默认只面向 Claude Code |
| `/icode install <name>` | 安装 ICODE、全部共享技能，但只安装指定 MCP |
| `/icode install --client codex` | 安装到 Codex skills 根，并为 Codex 注册 MCP；MCP entry 仍先生成 Claude 真源 |
| `/icode install --client all` | Claude Code + Codex 双端安装 ICODE、共享技能和 MCP |
| `/icode install --basic` | 只安装 ICODE 和共享技能，不创建 MCP 环境或注册项 |
| `/icode install --preview [--client ...]` | 只检查 manifest、冲突和目标动作，零写入且不调用 MCP |

**对称卸载**（虽然不是 `/icode` 命令，但同样属于本步骤的核心操作）：

```bash
./mcp/uninstall.sh                     # 一键卸载所有 13 个 mcp（默认只清 Claude Code）
./mcp/uninstall.sh <name>              # 只卸载指定 mcp
./mcp/uninstall.sh --client codex      # 卸载 + 同时清 Codex 注册
./mcp/uninstall.sh --client all        # Claude Code + Codex 双清理
```

## 执行步骤

1. **思考分级**（本步骤为 **L0：确定性执行**，不强制思考；见 [references/mcp_per_step.md](../references/mcp_per_step.md)「通用前置·分级思考」段）。作用域明确：执行确定性的 manifest 校验、文件发布、冲突检查和 MCP 注册，不创建工单。
2. **参数翻译后运行根安装器**：公开 `--basic` 映射为内部 `install.sh --skip-mcp`，公开 `--preview` 映射为内部 `install.sh --dry-run`；最终命令为 `bash <工程根>/install.sh [<mcp-name>] [--client claude|codex|all] [--skip-mcp] [--dry-run]`。`--client` 默认 `claude`；仅显式 `codex`/`all` 才写 Codex skills 根。内部参数不是 `/icode` 的公开别名。
3. 根 `install.sh` 会：
   - 先调用 `scripts/sync-to-global.sh` 安装 ICODE 本体；源码恰好位于目标 ICODE 目录时安全跳过自同步
   - 读取 manifest，把模板入口发布成各宿主技能根顶层的 `<skill-name>/SKILL.md`
   - 通过 `.icode-skill-owner.json` 区分受管技能；同内容旧副本可接管，不同内容的未托管同名技能会在任何写入前拒绝
   - 安装后校验发布 hash，并确保 ICODE 内没有可发现的嵌套技能入口
4. 未指定公开 `--basic`（即内部未传 `--skip-mcp`）时，根安装器再调用已安装 ICODE 内的 `mcp/install.sh`；该脚本会：
   - 扫描 `mcp/*/install.sh`（含 13 个声明的子工程，**新加 mcp 自动被识别**）
   - 逐个 `bash <子工程>/install.sh`，每个子工程 install.sh 自带：
     - 环境探测（Python/Node/npx/uv 等）
     - **缺啥补啥**（如 vision-bridge 建 venv；npm 类懒加载）
     - 写 `~/.claude.json` 的 `mcpServers.<name>` 段（经共享模块 `mcp/_lib/claude_registry.py`：原子写 + 损坏保护 + 回读校验 + 导出 entry 到 `~/.claude/icode_data/mcp_entries/<name>.json`）
   - 失败项不阻塞后续；最终汇总成功/失败计数
   - **`--client codex|all` 时**：每个子工程成功后再 `python3 mcp/_lib/client_registry.py codex-register <name>`（读导出的 entry → `codex mcp add <name> [--env K=V ...] -- <cmd> [args]`，add 后回读 inspect 确认）。Codex 注册失败计入失败清单，不阻塞其他子工程
5. **汇总结果**：任一阶段失败都返回非零，不能把“技能成功、MCP 失败”汇总成全量成功；按冲突或依赖提示处理后重跑
6. **必读提示**（按客户端区分）：
   - Claude Code：重启 Claude Code 后注册生效
   - Codex：新建或重开 Codex 任务后生效（当前任务不会热加载新 MCP）

## 密钥约束（首要边界）

- 本步骤**不接触任何 KEY**：vision-bridge 等需要 KEY 的 MCP，**只引导 `config.json` 模板**，不读取、不修改、不上传任何 KEY
- 任何 mcp 的 KEY（如 vision-bridge 的 base_url/api_key/model）**由用户自行设置环境变量**，由 install.sh 写到 `~/.claude.json` 的 `env` 段（**仅写路径占位，不写真值**）
- 普通网页邮件分析不要求配置 `icode-mail-observe`。只有用户选择无人值守/邮箱范围 IMAP 时，密码或授权码才由用户自行设置为 `ICODE_MAIL_OBSERVE_SECRET`，或放入配置指向且权限为 `0600` 的 `credential_file`；安装器只注册配置路径，不读取、不复制、不回显凭据
- 严格按子工程 `install.sh` 的设计边界执行，不绕开子工程的探测逻辑

## 异常处理

- **子工程 install.sh 失败**（非零退出）：脚本不中断后续子工程，继续跑后续；最终汇总里显示失败项
- **共享技能同名冲突**：目标无 ICODE 所有权标记且内容与源不一致 → 整体预检失败；安装器不自动覆盖或删除，先人工改名/移走再重跑
- **Codex 注册失败**（`--client codex|all` 时）：Codex 已有同名且内容不一致（add 未覆盖）→ 提示先 `codex mcp remove <name>` 再重试；entry 未导出 → 提示先跑子工程 install。均计入失败清单，不自动 remove（避免破坏性更新）
- **环境探测失败**（如 Node.js / uv 未装）：install.sh 会**主动尝试安装**（按平台优先级：brew / curl / winget / powershell），失败再给手动步骤
- **`mcp/` 下无子工程**：脚本提示"未找到 * /install.sh"，退出 0（非错误）
- **网络不可达**（如 pip/npm 源不可达）：共享技能已经安装时会明确报告 MCP 阶段失败；修复网络或依赖后重跑，可幂等更新
- **vision-bridge 的 config.json 三件套（base_url/api_key/model）未填**：install.sh 只生成模板，不阻断；mcpServer 启动时 UnconfiguredProvider 会回退提示

## 已知体验问题（2026-08 新增）

`/icode install` 一键装完后，**部分 MCP 启动行为可能影响首次体验**——提前说明便于排查：

- **cheap-research LLM 类工具装完需 KEY**（仅 `llm` capability 8 个必填 KEY）
  - **症状**：cheap-research 的 `llm` 类工具（summarize / extract / propose_repo_facts / diff_summary / fill_template / retrieve_similar / generate_filename / select_template）是便宜 LLM 推理，**未配 provider 时调用会 fallback 提示"未配置 platform"**；`local` 6 个 + `fetch` 1 个**不受影响照常可用**
  - **解决**：编辑 `~/.claude/skills/icode/mcp/cheap-research/config.json` 填三件套（`provider` / `base_url` / `api_key` / `model`），不绑任何平台
  - **降级**：未装或未配置 → 对应子任务 Agent(`model="haiku"`) 兜底（不报错不阻塞）；本地/网络类子任务不受 provider 配置影响

- **vision-bridge 未配置时 fallback 提示**
  - **症状**：vision-bridge 装完没填 config.json 时，工具调用返回 fallback 字符串，AI 不会自动处理图片/视频
  - **解决**：编辑 `~/.claude/skills/icode/mcp/vision-bridge/config.json` 填三件套
  - **降级**：按 `references/media_routing.md` 路由；仅宿主已证明多模态时走 native，否则降级 `text_only` 并记录视觉缺口

## 验收标准

- ✅ 根 `install.sh` 退出 0，所选宿主的 `icode/SKILL.md` 存在
- ✅ manifest 中全部共享技能位于技能根顶层，发布 hash 一致，且 `icode/skill-packs/` 内不存在嵌套 `SKILL.md`
- ✅ 每个共享技能所有权标记合法；不同内容的未托管同名目录没有被改写
- ✅ 未跳过 MCP 时，`mcp/install.sh` 退出 0（单个 MCP 失败不阻塞其他，但最终汇总返回失败）
- ✅ `~/.claude.json` 的 `mcpServers` 包含所有声明的、依赖满足的 MCP
- ✅ `--client codex|all` 时 `codex mcp list` 含对应 MCP（或已提示同名不一致需人工处理）
- ✅ user 提示已发布「重启 Claude Code 后生效」（Codex 分支另有「新建/重开任务生效」提示）
- ✅ 公开 `--preview`（内部 `--dry-run`）零写入；重复安装内容幂等；运行时配置未被镜像删除
- ✅ **未上传任何 KEY**：检查 `git diff` 仅含 markdown/bash/python，未含 api_key/token 字面量

## 跨平台说明（2026-07-26 修复）

所有 npx 系 MCP（context7 / memory / playwright / sequential-thinking）通过 `mcp/_lib/platform_entry.py::build_server_entry()` 统一注册，主方案 `command=npx, args=[-y, pkg]` 在 Windows / Linux / macOS 三平台都能跑：

- **Windows**：`shutil.which("npx")` 解析到 `npx.cmd`（batch 文件），Claude Code 启动器可直接 spawn
- **Linux/macOS**：`shutil.which("npx")` 解析到 `/usr/bin/npx`（npm 自带 shell 脚本），可执行
- **Fallback**：当 `npx` 不可用时，注册项 `_fallback` 字段写入 `node + npx-cli.js` 路径（位于 node 同级 `node_modules/npm/bin/npx-cli.js`）

**修改 MCP 后的步骤**：
1. 修改 `mcp/<name>/scripts/register_mcp.py` 或 `mcp/<name>/install.sh`
2. `bash mcp/<name>/install.sh --uninstall && bash mcp/<name>/install.sh`
3. **重启 Claude Code**（启动时同步加载 mcpServers，中途修改不生效）

## DOCX 自管运行时

`./install.sh` 在 ICODE 同步成功后，自动运行已安装副本的 `tools/docx/bootstrap_runtime.py`。它在 `~/.local/share/icode/runtime/docx/<lock-hash>/` 创建独立 venv 并安装锁定依赖，不使用全局 pip、sudo、宿主 `python-docx` 或系统 LibreOffice。首次安装需要包索引网络（离线发行可随 `tools/docx/wheels/` 附带 wheels）；失败时 ICODE 同步结果保持有效，但安装命令以非零退出，不能把 DOCX 能力宣传为可用。

DOCX 视觉验收只使用 ICODE 发行为当前 OS/CPU/glibc 打包且 SHA-256 校验通过的 renderer；无匹配 bundle 时生成和结构验收仍可用，manifest 必须标注 `visual_qa_pending`。禁止回退系统 `soffice`。

## 与其他步骤的关系

- **与 `doc` / `docx` / `status` / `list` 并列**：均为独立步骤，不参与 1~6 流程推进
- **建议时机**：clone 仓库后立即跑一次；后续 install.sh 升级时再跑（增量更新）
- **不写工单**：不创建 `00_init.md` / `.ico_metadata.json`，不更新 `~/.claude/icode_data/index.json`

## 卸载时机

卸载 13 个 mcp 用 `mcp/uninstall.sh`（顶层脚本）。**注意**：
- 移除 `~/.claude.json` 注册项（经共享模块 `claude_registry.unregister`，同时清理 `~/.claude/icode_data/mcp_entries/<name>.json` 导出）
- `--client codex|all` 时同时 `codex mcp remove <name>`（未注册幂等跳过）
- vision-bridge 默认不删安装目录与 `.venv`；要彻底清理安装 target 使用其 `uninstall.sh --purge`（源码仓不删）
- npm/uv 缓存系统级保留（不删，下次装仍可用）
## MCP 推荐

本步骤为 **L0（确定性执行，不强制思考）**（见 [references/mcp_per_step.md](../references/mcp_per_step.md)「通用前置·分级思考」段）：安装脚本 + 配置校验，无 LLM 分析子任务，不调用 sequential-thinking。除下述健康/策略复检外，其余 MCP 本步骤不调用。

`icode-mcp-health` 与 `icode-mcp-policy` 是本步骤的条件后端：安装完成后的新会话可用前者做 manifest/入口复检、用后者校验路由真源；当前安装进程仍以 shell 契约测试和 JSON/语法检查完成首次验收，不能反向依赖“刚注册但尚未热加载”的 MCP。

**强制约束**：🟢/🟢*/⚪ 语义 + 双保险机制（执行步骤内嵌 + thinking_core gate）详见 [SKILL.md「MCP 调用覆盖强制化」](../SKILL.md) + [references/mcp_per_step.md「双保险机制」](../references/mcp_per_step.md)；本步骤表内的 🟢/🟢* 标注按上方真源判定。
