# ICode — 全流程编码工作流（步骤 0 + 1~6，含日志根因分析入口 + 工程级知识库生成）

> English: [README.md](README.md) | 中文: 本文件

ICode 是一个 Claude Code 技能（Skill），将需求到交付拆解为严格步骤，每步可单独调用，手动切换模型时操作更灵活。

- **入口命令（可选）**：`/icode log` 日志根因分析（领域无关）→ 转修复需求；`/icode init` 需求初稿对话
- **步骤 0（可选）**：需求初稿对话，多轮迭代后落档为 `00_init.md`
- **步骤 1~6**：拟定计划 → 审查 → 定稿 → 编码 → 复检 → 终审

## 特性

- **闭环交付**：(可选) 需求初稿 → 计划 → 审查 → 定稿 → 编码 → 复检 → 终审，每步可独立调用，主会话执行、不切换模型
- **双模式**：`/icode start` 全流程（多轮审查 + 对抗验证）/ `/icode fast` 精简（1 轮无对抗，约 65% 耗时），自动串联步骤 1→6
- **防偷懒质量门**：三阶段复检（Reverse/Fixed/Free）、Plan 断言实证验证、ADR 决策记录、对抗验证（独立质疑者——证据不足不确认、诚实降级不伪造共识）
- **跨工程历史检索**：init/log/plan/start 启动时自动检索相似历史工单按命令分流注入，只进会话、不污染工程产物
- **工程级知识库**（`/icode doc`）：生成全局工程知识库（跨仓库跨分支共享，模块文档只生成一次复用），供段零自动检索注入，开发时无需手动告知参考文档
- **防重复注入**：历史检索与工程文档检索共用缓存去重，避免同开发链路重复注入
- **防偷懒强化**：步骤5/6 强制 Read 确认行 + 证据 file:line + 自检清单，步骤2 对抗强制 Agent ID
- **两个可选入口**：`/icode log` 日志根因分析（先基线检查再对抗分析，领域无关）→ 转修复需求；`/icode init` 多轮需求初稿对话 → `00_init.md`
- **产物与状态管理**：统一收纳在 `.icode_output/.icode_output_N/`，`.ico_metadata.json` 记录状态/代码文件，支持跨会话恢复与断点续跑
- **可选 TB 缺陷源**：`/icode log` 零散输入含 Teambition 项目 URL 或 `<LIB>-<NUM>` 时，可选拉取缺陷单的标题/描述/评论/日志附件作为分析输入（多项目文本配置，仅拉取分析、不回写 TB；无 TB 引用时走纯本地日志路径，行为不变）
- **可选钉钉文档源**：入口（`/icode init` / `log` / `plan` / `start`）与 patch 阶段0 零散输入含钉钉分享链接（alidocs.dingtalk.com）时，可选拉取文档/钉盘文件作为需求与参考资料输入（仅拉取、不回写钉钉；原生格式需用户在钉钉 UI 导出；无钉钉引用时行为不变）
- **可选视觉理解**（`mcp/vision-bridge`）：可装可不装的图片/视频理解 MCP——**不绑任何平台**，只要你的 provider 提供 OpenAI Chat Completions 兼容接口就能用（OpenAI / Claude / Gemini / 国内厂商 / 自建 / OpenRouter 全部支持）。装好后 SKILL 工作流统一走 `mcp__vision-bridge__analyze_media` 工具；未装时 session 模型按原生能力处理，由用户自负其责。详见 [mcp/vision-bridge/README.md](mcp/vision-bridge/README.md) 与 [SKILL.md](SKILL.md) 的「可选增强」段

## 安装

将本仓库克隆到 Claude Code skills 目录：

```bash
git clone <repo-url> ~/.claude/skills/icode
```

然后跑 MCP 环境检查 + 一键安装（扫描 `mcp/*/install.sh`，每个子工程自检环境（venv/Node/npm）并缺啥补啥、注册到 `~/.claude.json`）：

```bash
/icode install
```

新 clone 仓库 / 新机器 / CI 初始化时跑一次。可选 MCP 未装时工作流优雅降级（显式声明降级路径，不阻塞）。

## 可选增强：图片/视频理解

视觉理解是可选增强，**未装不影响主工作流**。装了后所有图片/视频处理统一通过 `mcp__vision-bridge__analyze_media` 工具，不污染 session 模型。

### 安装 vision-bridge

```bash
cd ~/.claude/skills/icode/mcp/vision-bridge
./install.sh                          # 自动:创 venv + 装依赖 + 注册到 ~/.claude.json
# 编辑生成的 config.json 填你的 base_url / api_key / model
# 重启 Claude Code 即生效
```

### 不绑任何平台

任何 OpenAI Chat Completions 兼容端点都能用——你用什么平台就填什么 base_url 和 model，**没有任何推荐值**。

### 缺配置时怎么办？

如果 vision-bridge 装了但 `config.json` 还没填三件套（`base_url` / `api_key` / `model`），`analyze_media` 工具会返回 fallback 提示字符串，session 模型按默认会话模型原生能力处理原图——**等同于未装 vision-bridge 的行为**。不报错、不阻塞。

详见 [mcp/vision-bridge/README.md](mcp/vision-bridge/README.md)。

## 可选增强：便宜 LLM 推理（cheap-research）

为降低主会话的 token 消耗，cheap-research 把"长上下文压缩 / 历史检索 / 模板填充 / 结构化提取"等子任务**转交便宜模型**（仍走 `mcp__cheap-research__*` 工具）。**不接管决策**：3 质疑者对抗 / 架构决策 / 终审裁决 / 修复方案一律不交给 cheap-research。

**入选条件**（单闸门）：价值 ≥ 3 ★ + 低风险 = 23 个子任务入选（含 TB 评论预提取），覆盖 log / doc / readme / init / plan / review / start / fast 等入口。

### 安装 cheap-research

```bash
cd ~/.claude/skills/icode/mcp/cheap-research
./install.sh                          # 自动:创 venv + 装依赖 + 注册到 ~/.claude.json
# 编辑生成的 config.json 填你的 base_url / api_key / model
# 重启 Claude Code 即生效
```

### 跟 vision-bridge 一样的不锁平台

任何 OpenAI Chat Completions 兼容端点都能用——你用什么平台就填什么 base_url 和 model，**没有任何推荐值**。本地 Ollama 也是 provider 之一（`provider: local_ollama`）。

### 缺配置时降级

如果 cheap-research 装了但 `config.json` 还没填三件套，工具调用会返回 fallback 提示 dict，session 模型按默认会话模型处理——**等同于未装 cheap-research 的行为**。不报错、不阻塞。

详见 [mcp/cheap-research/README.md](mcp/cheap-research/README.md)。

## 快速开始

```bash
# 一步走完全流程
/icode start 实现MCU雨量传感器I2C驱动

# 或者分步执行
/icode plan 实现MCU雨量传感器I2C驱动   # 步骤1：拟定计划
/icode review                          # 步骤2：专项审查（软上限3轮，仍有问题时自动延长）
/icode review 5                        # 步骤2：指定5轮审查
/icode merge                           # 步骤3：合并定稿
/icode code                            # 步骤4：编码实施
/icode deepcheck                       # 步骤5：循环复检
/icode audit                           # 步骤6：终极终审

# 精简全流程（fast 模式：单文件/小改动场景，耗时约为全流程 65%）
/icode fast 给 calc.c 增加 isqrt 函数   # plan→review(1轮无对抗)→merge→code→deepcheck(Reverse)→audit

# 工程级知识库生成（独立步骤，任意时刻可跑，不参与 1~6 流程）
/icode doc                              # 无描述→全局扫描各工程知识库 stale 状态
/icode doc myproject                    # 检查该工程更新（增量优先：git diff 命中章节才重生成）
/icode doc 重新生成 myproject           # 全量重生成（触发确认门，保护手动编辑）
# 生成后，后续 /icode init|log|plan|start|fast 启动时段零自动检索注入相关章节

# PPT 生成（独立交付步骤：把 icode 产物/知识库转成 .pptx）
/icode ppt                                # 默认：最新工单→本次功能开发 PPT（有 log 入口→本次BUG修复）
/icode ppt 项目                            # 项目全景 PPT（内容源：project_docs 工程知识库 + 仓库结构）
/icode ppt 模块 传感器                        # 指定模块 PPT（内容源：模块 doc 章节）
/icode ppt 本次BUG修复                      # log 根因 + 08_patch + 验证→查BUG PPT
# 前置：pip install python-pptx（必需）；LibreOffice+poppler 可选（渲染 PNG 自检）
# 产出：<工程根>/.icode_output/ppt/xxx.pptx；内置模板非商业授权（tools/ppt/NOTICE）

# 需求不明确时，先讨论再进入流程
/icode init 录制传感器数据转包              # 步骤0：起一稿，进入对话
# ... 多轮对话补充需求，文档 00_init.md 每轮都被增量更新 ...
/icode start                             # 无参→检测到 init 入口态，询问"复用/新建"，选复用则把 00_init.md 作需求输入，进入步骤1→6

# 从 bug 日志分析切入修复（先查根因，再修复）
/icode log ~/work/log/服务异常 "启动后无响应"      # 入口：分析日志根因，产出 log_analysis.md + 修复需求 00_init.md
# ... 对抗分析收敛后根因确定；若质疑可继续对话重跑被质疑分支 ...
/icode start                             # 无参→检测到 log_done 入口态，询问"复用/新建"，选复用则把 00_init.md（修复需求）作输入，进入步骤1→6
```

## 可选：从 Teambition 拉缺陷单日志分析

`/icode log` 的零散输入含 Teambition 项目 URL 或 `<LIB>-<NUM>`（如 `DEMO-26`）时，可选自动拉取缺陷单的标题/描述/评论/日志附件作为分析输入；详见 [SKILL.md「使用流程示例」段（方式 D2）](SKILL.md)。配置（多项目 + cookie）详见 `~/.claude/skills/icode/tools/tb/README.md`。

## 可选：从钉钉文档/钉盘拉资料

入口（`/icode init` / `log` / `plan` / `start`）与 patch 阶段0 零散输入含钉钉分享链接（`alidocs.dingtalk.com` / `/i/nodes/{token}`）时，可选自动拉取文档/钉盘文件到 `dingtalk_source/` 作为需求与参考资料输入；详见 [SKILL.md「使用流程示例」段（方式 H）](SKILL.md)。前置：Chrome 已登录钉钉文档 + `pip install browser_cookie3`；详见 `~/.claude/skills/icode/tools/dingtalk/README.md`。

## 命令一览

| 命令 | 功能 |
| ---- | ---- |
| `/icode help` | 帮助：输出使用流程示例 |
| `/icode log [零散信息...]` | 可选入口：日志根因分析→转修复需求 `00_init.md`（领域无关，每次都新建目录） |
| `/icode init [<粗略需求>]` | 可选步骤 0：多轮对话产出需求初稿 `00_init.md` |
| `/icode start <需求>` | 全流程：创建/复用目录 → 步骤 1→6 |
| `/icode fast <需求>` | 精简全流程：plan→review(1轮无对抗)→merge→code→deepcheck(Reverse)→audit（耗时约 65%） |
| `/icode plan <需求>` | 仅步骤 1：拟定项目计划 |
| `/icode review [N]` | 仅步骤 2：专项审查计划（N=软上限轮数，默认 3） |
| `/icode merge` | 仅步骤 3：合并审查意见定稿 |
| `/icode code` | 仅步骤 4：落地编码实施 |
| `/icode deepcheck` | 仅步骤 5：三阶段递进复检（Reverse → Fixed → Free） |
| `/icode audit` | 仅步骤 6：终极终审 + 统一修复（产出 `06_audit.md`） |
| `/icode readme` | 可选步骤 7：一次生成两份——交付报告（给自己看，完整档案）+ 跨领域简报（`_brief.md`，给其它模块研发/测试/产品看，含必要代码，较简略） |
| `/icode patch [问题或新需求]` | 追加修改（独立步骤）：主流程后/中途继续改——测试发现问题 / 新需求，在既有工单上打补丁。轻量四段式（重审现状→增量计划→最小实施→反向复检），靠磁盘产物重载上下文（换会话可继续），产出 `08_patch.md` 追加式；可选 `--listen`（自动监听）/ `--test`（显式触发验证）→ 实机部署验证（连设备部署 + 轮询监听 LOG + 增量分析；先配 `~/.claude/icode_data/device_config/<project_id>.json`，模板 `templates/device_config.json.template`，单文件多连接 adb/ssh/串口） |
| `/icode doc [自然语言]` | 工程级知识库生成（独立步骤）：扫描代码特征生成全局知识库章节，供段零自动检索注入 |
| `/icode limit [自然语言]` | 项目约束红线（独立步骤）：定义和维护本工程的红线/约束/禁区。主存全局 + 单 checkout 覆盖（自动 gitignore），追加式演进。plan 步骤引用作为硬基线 |
| `/icode ppt [自然语言]` | PPT 生成（独立交付步骤）：自然语言 → 真实 `.pptx`，4 类场景——**项目 / 模块 / 本次功能开发 / 本次BUG修复**；内容源为 icode 产物/知识库（禁止编造），内置 16 套模板（`tools/ppt/templates/`，AI 先筛 2-3 个风格匹配候选、由用户挑选；也可直接点名模板），产出 `<工程根>/.icode_output/ppt/`（不放进工单目录）可回溯；依赖 python-pptx（必需），LibreOffice+poppler 可选（PNG 预览自检）；内置模板非商业授权（见 `tools/ppt/NOTICE`） |
| `/icode status` | 只读：查当前工单状态 |
| `/icode list [关键词]` | 跨工程工单查找（纯只读） |

> 完整命令一览（含「创建目录？」列 + 复用规则 + `--verdict`/`--scan-verdict` 等参数详解）见 [SKILL.md「调用命令」段](SKILL.md)。

## 执行方式 / 目录结构 / 工作流程

执行方式（主会话 + 不主动切换模型）+ 目录结构（含 `.icode_output_N/` 产物收纳）+ 工作流程（步骤 1→6 数据流图 + fast 模式分支）详见 [SKILL.md「通用规则」段](SKILL.md)。

## 许可证

MIT — 详见 [LICENSE](LICENSE)。

## DEMO（用于测试 icode 流程）

`demo/` 是一个最小 C 计算器工程（`calc.h`/`calc.c`/`main.c`/`Makefile`），**专门用来端到端测试 icode 工作流**——五种调用方式（A 全流程 / B 分步 / C init→start / D log→start / E fast 精简）都可在此工程里真实跑通：步骤1 计划、步骤4 编码、步骤5 复检、步骤6 编译验证都有真实代码可操作。

```bash
cd demo && make && ./calc_demo   # 确认基线可编译可运行
```

测试示例需求：

- **方式A**： `cd demo && /icode start 给计算器增加取模和幂运算功能，并补全整数溢出检查`
- **方式B（分步）**： `cd demo && /icode plan 给计算器增加 isqrt 函数` 然后逐步 `/icode review` `/icode merge` `/icode code` `/icode deepcheck` `/icode audit`
- **方式C（先 init 后 start）**： `cd demo && /icode init 计算器加新功能`（多轮对话澄清需求）→ `/icode start`
- **方式D（先 log 后 start）**： `cd demo && /icode log <log路径> "症状描述"` → 产出根因 + 修复需求 → `/icode start`
- **方式E（fast 精简）**： `cd demo && /icode fast 给 calc.c 增加 isqrt 函数`（耗时约 65%）
