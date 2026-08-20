# 步骤 install — MCP 环境检查与一键安装（独立步骤）

**命令**: `/icode install`
**产出**: 无（仅修改 `~/.claude.json` / `~/.claude/skills/icode/mcp/`）
**会话**: 主会话
**定位**: **独立步骤**，不创建 `.icode_output_N/`、不写 `.ico_metadata.json`、不参与 1~6 流程推进。与 `doc` / `status` / `list` 并列。

## 用途

icode 工作流强依赖 MCP（每个 mcp 子工程自带 `install.sh` 提供一键安装）。`/icode install` 用于**一次性检查 + 安装**所有 `mcp/*/` 子工程下的 MCP。新 clone 本工程、新机器、CI 初始化都应跑一次。

**当前 6 个声明的 MCP**：

| MCP | 形态 | 对 icode 工作流的增益 | KEY |
|---|---|---|---|
| **sequential-thinking** | npm | 强制思考前置（每步必用） | ❌ |
| **vision-bridge** | Python venv | 图片/视频理解（步骤 0 init/6 audit） | ✅ 推荐装（需配三件套） |
| **memory** | npm | 跨工单记忆 | ❌ |
| **context7** | npm | 库文档实时查询，步骤 0/1/4 | ❌ |
| **playwright** ⚠️ | npm | 浏览器自动化，步骤 5/6（**仅前端项目**） | ❌ |
| **cheap-research** | Python venv | 便宜 LLM 推理降本（23 子任务），未装走 Agent(model="haiku") 兜底 | ✅ 推荐装（需 KEY） |

> 完整说明见各 `mcp/<name>/README.md`。**vision-bridge 和 cheap-research 是我们自研的、需配 KEY（三件套）才能用**。其余无需 KEY 即可安装。
>
> **⚠️ playwright 警告**：24 个工具 schema 永久加载到 system prompt，**非前端项目 token 性价比低**。建议：前端项目保留，全部项目通用时不装。

## 命令

| 命令 | 行为 |
|---|---|
| `/icode install` | 默认 = 一键安装所有 6 个 mcp（触发自动安装 uv 等依赖），**只注册 Claude Code**（`~/.claude.json`） |
| `/icode install <name>` | 只装指定 mcp（如 `/icode install filesystem`） |
| `/icode install --no-auto-install` | 跳过自动装依赖（依赖缺失时直接给手动步骤，不联网下载） |
| `/icode install --client codex` | 安装 + 额外注册到 **Codex**（`codex mcp add`）；仍默认注册 Claude Code（entry 真源） |
| `/icode install --client all` | Claude Code + Codex **双注册** |

**对称卸载**（虽然不是 `/icode` 命令，但同样属于本步骤的核心操作）：

```bash
./mcp/uninstall.sh                     # 一键卸载所有 6 个 mcp（默认只清 Claude Code）
./mcp/uninstall.sh <name>              # 只卸载指定 mcp
./mcp/uninstall.sh --client codex      # 卸载 + 同时清 Codex 注册
./mcp/uninstall.sh --client all        # Claude Code + Codex 双清理
```

## 执行步骤

1. **强制思考前置**（不可跳过，缺证据视为不合规；按 [references/thinking_core.md](../references/thinking_core.md)「强制思考前置·统一契约」段执行）：本步骤子项（至少 3 步）=
   - 本次作用域明确（本步骤直接调用 `mcp/install.sh`，不读写工程文件）
   - 当前 `~/.claude.json` mcpServers 段速读（了解现状，避免重复注册）
   - 执行结果逐项验证（不只看 install.sh 退出码，还要确认每个 mcpServer 已写入）
2. **运行 `bash <工程根>/mcp/install.sh [<name>] [--no-auto-install] [--client claude|codex|all]`**（cwd 必须在 icode-skill 工程根；用 `git rev-parse --show-toplevel` 解析工程根，失败则报错"请在 icode-skill 工程根内运行"）。`--client` 默认 `claude`（不碰 Codex）；仅显式 `codex`/`all` 才触达 Codex
3. install.sh 顶层脚本会：
   - 扫描 `mcp/*/install.sh`（含 6 个声明的子工程，**新加 mcp 自动被识别**）
   - 逐个 `bash <子工程>/install.sh`，每个子工程 install.sh 自带：
     - 环境探测（Python/Node/npx/uv 等）
     - **缺啥补啥**（如 vision-bridge 建 venv；npm 类懒加载）
     - 写 `~/.claude.json` 的 `mcpServers.<name>` 段（经共享模块 `mcp/_lib/claude_registry.py`：原子写 + 损坏保护 + 回读校验 + 导出 entry 到 `~/.claude/icode_data/mcp_entries/<name>.json`）
   - 失败项不阻塞后续；最终汇总成功/失败计数
   - **`--client codex|all` 时**：每个子工程成功后再 `python3 mcp/_lib/client_registry.py codex-register <name>`（读导出的 entry → `codex mcp add <name> [--env K=V ...] -- <cmd> [args]`，add 后回读 inspect 确认）。Codex 注册失败计入失败清单，不阻塞其他子工程
4. **汇总结果**：脚本输出成功/失败清单。失败项可能是依赖缺失/网络失败/平台不支持/Codex 同名不一致；按脚本提示处理后重跑
5. **必读提示**（按客户端区分）：
   - Claude Code：重启 Claude Code 后注册生效
   - Codex：新建或重开 Codex 任务后生效（当前任务不会热加载新 MCP）

## 密钥约束（首要边界）

- 本步骤**不接触任何 KEY**：vision-bridge 等需要 KEY 的 MCP，**只引导 `config.json` 模板**，不读取、不修改、不上传任何 KEY
- 任何 mcp 的 KEY（如 vision-bridge 的 base_url/api_key/model）**由用户自行设置环境变量**，由 install.sh 写到 `~/.claude.json` 的 `env` 段（**仅写路径占位，不写真值**）
- 严格按子工程 `install.sh` 的设计边界执行，不绕开子工程的探测逻辑

## 异常处理

- **子工程 install.sh 失败**（非零退出）：脚本不中断后续子工程，继续跑后续；最终汇总里显示失败项
- **Codex 注册失败**（`--client codex|all` 时）：Codex 已有同名且内容不一致（add 未覆盖）→ 提示先 `codex mcp remove <name>` 再重试；entry 未导出 → 提示先跑子工程 install。均计入失败清单，不自动 remove（避免破坏性更新）
- **环境探测失败**（如 Node.js / uv 未装）：install.sh 会**主动尝试安装**（按平台优先级：brew / curl / winget / powershell），失败再给手动步骤
- **`mcp/` 下无子工程**：脚本提示"未找到 * /install.sh"，退出 0（非错误）
- **网络不可达**（如 curl 拉 astral.sh 失败）：提示用户手动装，或传 `--no-auto-install` 跳过自动装
- **vision-bridge 的 config.json 三件套（base_url/api_key/model）未填**：install.sh 只生成模板，不阻断；mcpServer 启动时 UnconfiguredProvider 会回退提示

## 已知体验问题（2026-08 新增）

`/icode install` 一键装完后，**部分 MCP 启动行为可能影响首次体验**——提前说明便于排查：

- **cheap-research 装完需 KEY**（必填 KEY 才能用）
  - **症状**：cheap-research 是便宜 LLM 推理 MCP（承担长上下文压缩/历史检索/模板填充等子任务），**装完首次启动会 fallback 提示"未配置 platform"**
  - **解决**：编辑 `~/.claude/skills/icode/mcp/cheap-research/config.json` 填三件套（`provider` / `base_url` / `api_key` / `model`），不绑任何平台
  - **降级**：未装或未配置 → Agent(`model="haiku"`) 兜底（不报错不阻塞）

- **vision-bridge 未配置时 fallback 提示**
  - **症状**：vision-bridge 装完没填 config.json 时，工具调用返回 fallback 字符串，AI 不会自动处理图片/视频
  - **解决**：编辑 `~/.claude/skills/icode/mcp/vision-bridge/config.json` 填三件套
  - **降级**：AI 按 session 模型原生能力处理（user 自负其责）

## 验收标准

- ✅ `mcp/install.sh` 退出 0（单个 MCP 失败不阻塞其他）
- ✅ `~/.claude.json` 的 `mcpServers` 包含所有声明的、依赖满足的 MCP
- ✅ `--client codex|all` 时 `codex mcp list` 含对应 MCP（或已提示同名不一致需人工处理）
- ✅ user 提示已发布「重启 Claude Code 后生效」（Codex 分支另有「新建/重开任务生效」提示）
- ✅ 工程文件（`mcp/` 源码、`SKILL.md`、`steps/`）未被动过（独立步骤特性）
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

## 与其他步骤的关系

- **与 `doc` / `status` / `list` 并列**：均为独立步骤，不参与 1~6 流程推进
- **建议时机**：clone 仓库后立即跑一次；后续 install.sh 升级时再跑（增量更新）
- **不写工单**：不创建 `00_init.md` / `.ico_metadata.json`，不更新 `~/.claude/icode_data/index.json`

## 卸载时机

卸载 6 个 mcp 用 `mcp/uninstall.sh`（顶层脚本）。**注意**：
- 移除 `~/.claude.json` 注册项（经共享模块 `claude_registry.unregister`，同时清理 `~/.claude/icode_data/mcp_entries/<name>.json` 导出）
- `--client codex|all` 时同时 `codex mcp remove <name>`（未注册幂等跳过）
- vision-bridge 不删 `.venv`（要彻底清用 `--purge`，待 vision-bridge 升级时支持）
- npm/uv 缓存系统级保留（不删，下次装仍可用）
## MCP 推荐

本步骤仅用 sequential-thinking 强制思考（见 [references/mcp_per_step.md](../references/mcp_per_step.md)「通用前置」段）。其他 5 个 MCP 本步骤不推荐。

**强制约束**：🟢/🟢*/⚪ 语义 + 双保险机制（执行步骤内嵌 + thinking_core gate）详见 [SKILL.md「MCP 调用覆盖强制化」](../SKILL.md) + [references/mcp_per_step.md「双保险机制」](../references/mcp_per_step.md)；本步骤表内的 🟢/🟢* 标注按上方真源判定。
