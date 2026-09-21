# 内置 PPT 模板索引

> 8 套内置模板，覆盖通用汇报、架构、数据、商务咨询、竞聘复盘与学术教学。选模板时按"主色 + 风格 + 场景"快速筛选；候选 2-3 个时把对应 `preview.png` 给用户挑。

⚠️ **非商业使用** — 模板仅供个人学习与研究，禁止商业用途。

## 完整模板清单

| slug | 名称 | 页数 | 主色 | 风格 / 一句话特点 |
|---|---|---|---|---|
| `minimal-business-summary` | 简约商务总结汇报 | 16 | 深蓝白 `#485275` | 极简商务，留白多，4 章节，季度/年度汇报首选 |
| `thesis-novice` | 多专业开题方法论库 | 32 | 墨绿 `#4F6E4F` | 32 套填好的研究方法范例，跨 6 大专业 |
| `premium-corp` | 高级感大厂 PPT 合辑 | 35 | 酱红 `#A52524` + 深蓝灰 | 35 套高级感版式（战略/运营/数据/思维模型） |
| `architecture-deck` | 领导爱的架构图合辑 | 37 | 深蓝 `#1F3A93` | 大厂真实架构图（项目/AI/物流/供应链） |
| `mckinsey-style` | 麦肯锡风专业模板 | 37 | 酱红 + 深蓝灰 | 咨询逻辑结构（金字塔/漏斗/对比/总分） |
| `thesis-formula` | 开题报告万能公式 | 39 | 暖米 `#F6F0DC` + 深蓝 | 4 段开题公式（背景/意义/现状/方法） |
| `data-viz-deck` | 数据可视化合辑 | 41 | 深蓝 `#2C3E70` + 砖红 | 数据展示版式；当前文字替换引擎不更新图表数据 |
| `competition-speech` | 竞聘述职合辑 | 59 | 深蓝 `#1B3464` + 砖红 | 竞聘/述职/项目复盘（KISS/PDCA/SWOT） |

## 按主色快速筛

| 主色 | 候选 slug |
|---|---|
| **深蓝 / 商务蓝** | `minimal-business-summary`、`competition-speech`、`architecture-deck`、`thesis-formula` |
| **酱红 / 大厂** | `premium-corp`、`mckinsey-style` |
| **绿色 / 学术** | `thesis-novice` |
| **暖米色 / 学术** | `thesis-formula` |
| **数据多色** | `data-viz-deck` |

## 按场景快速筛

| 场景 | 候选 slug |
|---|---|
| 季度 / 年度工作总结 | `minimal-business-summary`、`premium-corp`、`competition-speech` |
| 商务提案 / 方案路演 | `minimal-business-summary`、`mckinsey-style`、`premium-corp` |
| 述职 / 竞聘 / 晋升答辩 | `competition-speech`、`minimal-business-summary`、`premium-corp` |
| 开题答辩 / 课题汇报 | `thesis-novice`、`thesis-formula` |
| 培训 / 课件 | `thesis-novice`、`thesis-formula` |
| 架构 / 流程 / 系统拓扑 | `architecture-deck`、`mckinsey-style`、`premium-corp` |
| 数据展示 / 财务 / 销售 | `data-viz-deck`、`mckinsey-style`、`premium-corp` |
| 运营 / 互联网产品 | `premium-corp`、`minimal-business-summary`、`data-viz-deck` |
| 咨询 / 战略分析 | `mckinsey-style`、`premium-corp` |
| 思维模型 / SWOT / PDCA / 复盘 | `competition-speech`、`mckinsey-style`、`premium-corp` |

## 按规模筛选

| 规模 | 候选 slug | 适合 |
|---|---|---|
| **小（≤ 20 页）** | `minimal-business-summary` | 短小汇报、单一主题，按需选页 |
| **中（21–42 页）** | `thesis-novice`、`premium-corp`、`architecture-deck`、`mckinsey-style`、`thesis-formula`、`data-viz-deck` | 完整章节制汇报，按需选页 |
| **大（43+ 页）** | `competition-speech` | 素材库式按需挑选 |


## 字段说明

每个模板目录包含 4 个固定文件：

| 文件 | 用途 |
|---|---|
| `template.pptx` | 原始 PPT，build_pptx.py 的输入 |
| `intro.md` | 高度浓缩简介，给上层 AI 做匹配 |
| `detail.json` | 详细结构 + 每个 slot 的寻址与建议 |
| `preview.png` | 4 页 2×2 拼接预览，给用户决策用 |

## 选择边界

需求模糊时先区分用途：简洁汇报选 `minimal-business-summary`；架构选 `architecture-deck`；数据选 `data-viz-deck`；复盘/述职选 `competition-speech`。只列匹配的候选，不为凑数量推荐不存在或不合适的模板。

`premium-corp` 与 `mckinsey-style` 有相近商务版式；`thesis-novice` 与 `thesis-formula` 分别偏研究方法范例和开题结构。读各自 `intro.md`、`detail.json` 后再确定页面，不能把模板示例数据当成项目事实。

用户指定不在本清单内的旧模板时，明确提示当前安装未提供，列出现存替代项让用户选择；不得静默替换，也不自动从网络补回。已有 edits 引用旧模板时保留原产物，待用户提供原模板或确认重新选款后再构建。
