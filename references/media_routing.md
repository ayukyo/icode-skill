# 媒体能力路由与证据合同

本文件是 ICODE 图片、视频、PDF 页面及视觉型附件的统一路由真源。目标是在强多模态会话中保留原生能力，同时让纯文本模型安全借助 vision-bridge；“bridge 已安装”不代表它比当前会话模型更强。

## 路由原则

先做确定性提取，再处理视觉区域：PDF/Office 的文本、元数据、表格结构与归档目录优先由本地只读适配器提取；只有布局、示意图、原理图、照片、波形、复杂表格和视频画面进入视觉路由。

本合同也覆盖工具生成的预览图（例如 PPT 的 `preview/slide-N.png`）。文件已成功渲染不等于当前会话支持图片输入；任何预览图进入模型前仍须路由。纯文本/能力未知会话只做确定性结构检查并保留 `visual_status=unobserved`，禁止用 Read/open/attach 图片探测能力。

`media_mode` 允许 `auto | native | bridge | dual | text_only`：

- `auto`：宿主明确证明当前会话支持媒体输入时，普通任务用 `native`；未证明或明确为纯文本时，vision-bridge 健康则用 `bridge`，否则降级 `text_only` 并保留人工视觉缺口。高风险视觉证据在两通道均可用时用 `dual`。
- `native`：只在宿主提供可靠的多模态能力信号时允许。禁止用“试传一张图”探测，否则纯文本会话可能被媒体消息持续污染。
- `bridge`：适合纯文本会话。bridge 输出是另一模型生成的二级证据，其质量由 capability/quality profile 约束，不因 MCP 已安装而自动成为最高权威。
- `dual`：native 与 bridge 独立分析；任一方不得看到另一方结论。语义分歧记为 `unresolved`，不得投票、拼接或用 bridge 覆盖 native。
- `text_only`：仅使用确定性文本/OCR/元数据。OCR 证明文字候选，不证明布局、连通、极性或空间关系。

可执行路由器：

```bash
python3 tools/media_router.py route \
  --mode auto --native supported --bridge available \
  --bridge-profile <vision-bridge-config-or-profile.json> \
  --native-max-images-per-message 4 \
  --task schematic --risk high
```

路由器只读取 bridge 配置中的非敏感能力字段，绝不输出 API key/token/password。宿主未提供能力证明时传 `--native unknown`。

## bridge 能力画像

bridge 配置可声明：

```json
{
  "profile_version": "eval-2026-09",
  "max_images_per_message": 4,
  "declared_capabilities": ["ocr", "small_text", "table", "diagram", "schematic", "spatial_reasoning", "video"],
  "quality_profile": {
    "evaluation_status": "tested",
    "max_images": 8,
    "max_pixels": 16000000,
    "notes": "local evaluation reference"
  }
}
```

`max_images_per_message` 是传输层硬限制，与 `quality_profile.max_images` 的评测容量不是同一概念。前者未声明时按保守默认值 `4` 执行；平台文档或实测值更低时必须填写更低值。`describe_capabilities` 返回的能力画像把它放在 `transport_limits.max_images_per_message`。

未声明或未测试的能力按 `unknown`，仍可用于候选提取，但原理图连通、器件极性等结论必须由 EDA/netlist/源码或人工复核闭环。能力画像只描述已验证边界，不根据模型名称猜测。

## 单消息图片上限与串行分批

`tools/media_router.py route` 的 `image_batching.selected_max_images_per_message` 是当前 selected mode 的执行上限。native 上限由宿主可靠能力信息传入 `--native-max-images-per-message`；未知时同样取 `4`。dual 取 native 与 bridge 两者较小值，确保两条独立通道都合法。

- 图片附件、视频关键帧、PDF 页面和 tile 的**任何一条消息**都不得超过该上限；不能只限制一次任务的总图片数。
- 超限时按原顺序串行分批，每批完成并落成文本结果后才能开始下一批；禁止并行媒体调用后让宿主把结果重新聚合进同一消息。
- 所有批次完成后只聚合文本，不把原图片再次带入聚合消息。bridge 在 provider 内强制执行此规则；native/dual 由宿主适配层遵守路由输出。
- 已经存在于会话历史中的超限用户消息无法由 ICODE 事后改写；若模型在 ICODE 执行前就拒绝该消息，需要宿主拆分附件或新建无污染会话。
- 媒体注入返回“Model only support text input”或等价拒绝时，立即停止当前会话的 native 媒体重试；把 native 视为 unsupported，转 bridge 或 text_only。不能因 PNG 已在磁盘就重复 Read/attach。

## 页面渲染、裁剪与分块

小字、原理图、密集表格先以适当 DPI 渲染目标页，再按重叠 tile 分块；不能把低分辨率整页一次分析当成完整覆盖。坐标计划可由：

```bash
python3 tools/media_router.py tiles --width <px> --height <px> --tile-size 1536 --overlap 128
```

每个结果都绑定原始文件 hash、页码、DPI、crop、tile index。整页摘要不能替代分块中的 refdes、引脚号、网标、脚注或小字证据；分块结果也必须回到整页检查跨区域关系。tile 数超过单消息上限时同样按上节串行分批。

## 视觉证据合同

对 native/bridge/OCR 的每次实质分析记录：`input_sha256`、`source_path`、`media_kind`、`page/crop/dpi/tile_index`、`channel`、`provider/model`、`prompt_profile/profile_version`、时间、状态、置信度、输出位置、限制和分歧状态。可用下列命令生成输入侧记录：

```bash
python3 tools/media_router.py evidence --path <rendered-page-or-image> \
  --media-kind pdf_page --channel native --provider session --model <model> \
  --prompt-profile schematic-v1 --profile-version v1 --page 4 --dpi 300 \
  --crop 0,0,1536,1536 --tile-index 0
```

禁止记录密钥。bridge 返回的自由文本若没有输入 hash、模型、页/裁剪和提示版本，不得作为可复演的结论级证据。

## 结论上限

- 文本提取成功不证明视觉区域完整。
- 原生多模态强不代表能替代 EDA/netlist、寄存器定义或实测。
- bridge 能力一般时，用它发现候选、OCR 小字或给纯文本会话补盲；不让它单独裁决高风险空间关系。
- 任何通道缺失、降级、裁剪遗漏或双通道分歧，都必须进入报告的 evidence gap 与 next action。
