# vision-bridge (MCP server for icode-skill)

**可选增强**：为 icode-skill 提供图片/视频理解的统一 MCP 接口。

> vision-bridge **不锁任何平台**，**不推荐任何 provider**。只要你的 provider 提供
> OpenAI Chat Completions 兼容接口（绝大多数 LLM 平台都兼容），填三件就能跑。

---

## 安装（三步）

### 1. 装到全局（一次性）

```bash
cd <你的 icode-skill 仓库>/mcp/vision-bridge
./install.sh
```

默认装到 `~/.claude/skills/icode/mcp/vision-bridge`。想换位置用环境变量
`VISION_BRIDGE_TARGET=/path ./install.sh`。

### 2. 填你的三件套

```bash
vim ~/.claude/skills/icode/mcp/vision-bridge/config.json
```

填：
- `provider` —— `openai_compat`（默认）或 `local_ocr`
- `base_url` —— 你平台的 API 端点（**没有默认值，请查平台文档**）
- `api_key`  —— 你平台的 KEY
- `model`    —— 你平台提供的"支持图片/视频"的模型名

**没有任何推荐值** —— 你用什么平台、什么模型完全由你决定。

### 3. 重启 Claude Code

调 `mcp__vision-bridge__analyze_media` 即可。

### 修改后增量同步

你在仓库里改了 vision-bridge 代码，`cd mcp/vision-bridge && ./install.sh` 即可
**增量同步**到 target，无需重装 venv。

---

## 怎么知道该填什么？

任何提供 OpenAI Chat Completions 兼容接口的平台都能用。常见例（非推荐）：

```
# OpenAI 官方
base_url: https://api.openai.com/v1
model:    gpt-4o-mini

# Anthropic Claude (通过 OpenAI 兼容代理)
base_url: <你的代理地址>
model:    <代理提供的 Claude 模型名>

# 国内厂商（按你平台真实文档为准）
base_url: <你平台给的端点>
model:    <你平台视觉模型名>

# 自建 vLLM / Ollama / LM Studio (OpenAI 兼容服务)
base_url: http://localhost:8000/v1
model:    <你加载的视觉模型>
```

> ⚠️ **不假设任何默认值** —— 你用什么平台、什么模型，请按你平台的真实文档填。

---

## provider 选项

| provider | 必填 | 视频 | KEY | 模型要求 | 性能 |
|----------|------|------|-----|----------|------|
| `openai_compat` | base_url + api_key + model | 是（ffmpeg 抽帧） | 是 | 任意支持 image_url 的模型 | 取决于平台 |
| `local_ocr` | tesseract 本地装 | 否 | 否 | — | 仅 OCR 文字提取 |

切换：在 `config.json` 改 `"provider": "local_ocr"`，重启 Claude Code。

---

## 卸载

```bash
./uninstall.sh         # 只取消注册, 保留代码与 venv
./uninstall.sh --purge # 删 target 目录与 venv
```

---

## 工具签名

保留兼容文本接口，并增加能力画像和可复演证据接口：

```python
async def analyze_media(
    media_path: str,            # 本地路径或 http(s) URL
    prompt: str = "",           # 附加指令 (如"重点关注红色错误")
    media_type: str = "auto",   # "image" | "video" | "auto"(按扩展名推断)
    max_tokens: int = 1024,
) -> str:                        # 文本描述

async def describe_capabilities() -> str:  # 不含密钥的 provider/model/能力画像 JSON

async def analyze_media_evidence(
    media_path: str,
    prompt: str = "",
    media_type: str = "auto",
    max_tokens: int = 1024,
    prompt_profile: str = "general-v1",
    profile_version: str = "v1",
    page: int | None = None,
    crop: str = "",
    dpi: int | None = None,
    tile_index: int | None = None,
) -> str:                        # 带 hash/模型/页/裁剪/状态的 JSON
```

`analyze_media` 供旧调用兼容；新工作流优先 `analyze_media_evidence`。证据接口只接受可计算 SHA-256 的本地常规文件，返回顶层 `input_sha256/media_kind/channel/provider/model/prompt_profile/profile_version` 及页、裁剪、DPI、tile、限制和分歧字段；远程 URL 只能走旧接口并按候选信息处理。bridge 通道中的 session 只看到文本/JSON 返回，不接触原媒体；ICODE 是否使用当前会话模型的原生视觉由 [媒体能力路由](../../references/media_routing.md)决定。

---

## 本地 CLI 通道（MCP 工具不可用时兜底）

codex 等客户端**不注入 MCP tools**（只暴露 `list_mcp_resources` 等资源类工具，见 openai/codex issue #30922）时，`analyze_media` 工具对会话模型不可见。此时可用**本地 CLI 通道**等价调用（结果同样纯文本输出，不接触原图）：

```bash
# 需要 VISION_BRIDGE_CONFIG 指向已填三件套的 config.json
VISION_BRIDGE_CONFIG=~/.claude/skills/icode/mcp/vision-bridge/config.json \
  ~/.claude/skills/icode/mcp/vision-bridge/.venv/bin/python \
  ~/.claude/skills/icode/mcp/vision-bridge/server.py \
  --analyze-media /path/to/frame.jpg \
  --prompt "提取时间点/界面显示/操作序列/错误提示" \
  --max-tokens 1024
# stdout 为文本描述; 退出码 0 成功, 非 0 看 stderr
```

新工作流可用 `--analyze-evidence <path>` 返回证据 JSON，用 `--capabilities` 返回不含密钥的能力画像；页/裁剪参数为 `--page/--crop/--dpi/--tile-index`。不带 CLI 模式参数时仍走 MCP server，旧行为不变。

---

## SKILL 端约定（写在主 SKILL.md）

- vision-bridge 是纯文本/能力未知会话的补盲通道，不因安装成功自动取代 GPT 等宿主已证明的强原生视觉。
- 宿主能力未知时禁止试传图片；先用 ICODE `tools/media_router.py route` 判定。selected mode 为 bridge/dual 才调用本服务。
- `declared_capabilities` 和 `quality_profile` 只声明实际评测边界。未知能力的输出只能作候选证据；高风险双通道分歧保持未决。
- Codex 等未注入 MCP 工具的环境可用本地 CLI；结果仍须记录输入 hash、provider/model、prompt profile、页/裁剪/DPI 和状态。
