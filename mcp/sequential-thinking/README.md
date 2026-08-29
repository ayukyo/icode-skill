# sequential-thinking (MCP server for icode-skill)

为 icode-skill 工作流的 **L2/L3 复杂推理/高风险对抗步骤** 提供结构化思考载体——sequential-thinking MCP server。

> icode 工作流在 [SKILL.md](../../SKILL.md) 与 [references/thinking_core.md](../../references/thinking_core.md)
> 中规定：**reasoning gate 分级（L0～L3）决定思考载体**。L2/L3（plan/review/code/patch/log/deepcheck/audit
> 默认 L2；其余步骤命中升级触发器时）首选路径是调用本 MCP，不可用时降级为「### 结构化思考」文字块。
> **L0/L1（status/list/help/install/bak/readme/ppt/close/reopen/worktree/init/doc/limit/merge）不调用本 MCP**，
> 不进入可用性探测——避免简单步骤的仪式化调用、额外延迟与上下文噪声。

---

## 它是什么

[sequential-thinking](https://github.com/modelcontextprotocol/servers/tree/main/src/sequentialthinking)
是 MCP 官方 server 之一（npm 包 `@modelcontextprotocol/server-sequential-thinking`），
提供 `sequentialthinking` 工具，支持多步结构化思考、动态调整总步数、修订与分支。

---

## 安装（两步）

### 1. 注册 MCP server

需要 Node.js（≥ 18）与 npm（≥ 9），`npx` 通常随 npm 自带。

```bash
cd <你的 icode-skill 仓库>/mcp/sequential-thinking
./install.sh
```

脚本会：
1. 探测 `node` / `npx` / `python3`（兼容 Windows 上 `python` 与 `python3` Store stub 差异）
2. 把 `sequential-thinking` server 写入 `~/.claude.json` 的 `mcpServers` 段
3. 注册方式：`node` 启动 `npx -y @modelcontextprotocol/server-sequential-thinking`

**首次调用** MCP 时 `npx` 会自动下载包到 npm 缓存，无需预装。

### 2. 重启 Claude Code

让 MCP 注册生效。此后 icode 工作流中 reasoning gate 判 **L2/L3** 的步骤首选调用
`mcp__sequential-thinking__sequentialthinking`，而不是降级到文字块；**L0/L1 步骤不调用**。

---

## 卸载

```bash
./uninstall.sh
```

只移除 `~/.claude.json` 中的注册项，不删除 npm 缓存。

---

## 依赖

- **Node.js** ≥ 18（含 npm + npx）
- **Python** ≥ 3.8（仅用于运行 `scripts/register_mcp.py`，无需任何 pip 包）

无 Python 依赖、无需 venv、无 npm 包锁——`install.sh` 是**纯注册脚本**，
包版本由 npm registry 在首次 `npx -y` 时解析，符合「不硬编码版本号」的开源规约。

---

## 自定义包名（可选）

如想用社区其他 sequential-thinking 实现（如本地 fork）：

```bash
./install.sh "@your-org/your-sequential-thinking-fork"
```

---

## SKILL 端约定（已在主 SKILL.md 声明）

- **装好后**：icode 工作流中 reasoning gate 判 **L2/L3** 的步骤必须首选 `sequential-thinking` MCP（3～5 步；L3 另加独立对抗）
- **未装好**：L2/L3 走降级文字块路径，但工作流仍可运行（思考环节不省略，只换载体）；**L0/L1 不受影响**
- **判定 MCP 是否可用**的严谨逻辑见 [references/thinking_core.md](../../references/thinking_core.md)——
  简言之（**仅 L2/L3**）：工具列表直接可见（标准 `mcp__sequential-thinking__sequentialthinking` 或代理前缀形态）或 ToolSearch 取到 schema 即视为"已配置可用"

## 隐私（DISABLE_THOUGHT_LOGGING）

官方服务端默认会把完整 `thought` 打到 stderr，可能进入宿主日志。icode-skill 注册时**默认注入**：

```json
{
  "env": {
    "DISABLE_THOUGHT_LOGGING": "true"
  }
}
```

该配置只禁止服务端 stderr 打印，**不保证宿主不保存工具调用**；所以 `thought` 本身仍不得包含密钥、Cookie、
设备凭据或不必要的个人信息。用户显式开启日志时，不得被重装/同步脚本反复覆盖（保持用户显式覆盖能力）。

---

## 工具签名

仅暴露一个工具：

```python
async def sequentialthinking(
    thought: str,            # 当前步的思考内容
    nextThoughtNeeded: bool, # 是否需要继续下一步
    thoughtNumber: int,      # 当前步号(从 1 开始)
    totalThoughts: int,      # 预估总步数(可动态调整)
    isRevision: bool = False,    # 是否为修订前序步骤
    revisesThought: int = None,  # 若 isRevision,被修订的步号
    branchFromThought: int = None,  # 分支起点
    branchId: str = None,         # 分支 ID
    needsMoreThoughts: bool = False,  # 是否需要追加步数
) -> str
```

session 模型只能看到该工具的文本返回与上下文记录，**永远看不到原 sequential-thinking 内部状态**。
