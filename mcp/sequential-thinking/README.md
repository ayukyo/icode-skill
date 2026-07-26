# sequential-thinking (MCP server for icode-skill)

为 icode-skill 工作流提供「强制思考前置」首选载体——sequential-thinking MCP server。

> icode 工作流在 [SKILL.md](../../SKILL.md) 与 [references/thinking_core.md](../../references/thinking_core.md)
> 中规定：**每个步骤开始前必须 ultrathink 并完成结构化思考**。首选路径是调用本 MCP，
> 不可用时降级为「### 结构化思考」文字块。

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

让 MCP 注册生效。此后 icode 工作流的每一步首选调用 `mcp__sequential-thinking__sequentialthinking`，
而不是降级到文字块。

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

- **装好后**：icode 工作流每步的「强制思考前置」必须首选 `sequential-thinking` MCP
- **未装好**：走降级文字块路径，但工作流仍可运行（思考环节不省略，只换载体）
- **判定 MCP 是否可用**的严谨逻辑见 [references/thinking_core.md](../../references/thinking_core.md)——
  简言之：deferred tools 列表里能看到 `mcp__sequential-thinking__sequentialthinking` 即视为"已配置可用"

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
