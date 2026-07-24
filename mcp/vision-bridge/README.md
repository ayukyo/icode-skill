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

只暴露一个工具：

```python
async def analyze_media(
    media_path: str,            # 本地路径或 http(s) URL
    prompt: str = "",           # 附加指令 (如"重点关注红色错误")
    media_type: str = "auto",   # "image" | "video" | "auto"(按扩展名推断)
    max_tokens: int = 1024,
) -> str:                        # 文本描述
```

session 模型只看到这个工具的文本返回，**永远不接触原图/原视频**。

---

## SKILL 端约定（写在主 SKILL.md）

- **vision-bridge 装好后**：禁止把图片作为附件直接传给 session 模型，必须走 `analyze_media`
- **未装 vision-bridge**：不做约定，session 模型按其原生多模态能力处理，由用户自负其责
