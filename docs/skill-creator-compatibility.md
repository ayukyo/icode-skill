# ICODE 与 Anthropic skill-creator 的评估兼容

此文档定义评估输入、证据交接、离线检查及后续模型实测记录；评估工具本身不调用模型，不新增 ICODE 命令，不修改 runtime、许可或安装配置。后续经授权的规则修复另行记录源码差异，不能与初始只读评估混淆。`tests/skill-evals` 中其他共享技能的成绩不是根 ICODE 的模型评估。

## 官方格式与兼容范围

补充证据：[Codex/Claude真实加载与10目标安装](host-loading-validation.md)已独立完成。
它使用禁网初始化协议，不调用模型，因此不替代下文的触发评估、行为分数或工具执行验收。

2026-09-21 只读核对 Anthropic 官方仓库，固定参考提交为 `34040c9c568585f6929bedeaad110ad08f079624`：

- [输入 schema](https://github.com/anthropics/skills/blob/34040c9c568585f6929bedeaad110ad08f079624/skills/skill-creator/references/schemas.md)：行为输入使用 `skill_name` 与 `evals`，案例包含整数 `id`、`prompt`、`expected_output`、`files`；上游还允许 `expectations`。
- [skill-creator 流程](https://github.com/anthropics/skills/blob/34040c9c568585f6929bedeaad110ad08f079624/skills/skill-creator/SKILL.md)：触发输入是 `{query, should_trigger}` 数组，行为对比与人工复核是另外的流程。
- [现成 trigger runner](https://github.com/anthropics/skills/blob/34040c9c568585f6929bedeaad110ad08f079624/skills/skill-creator/scripts/run_eval.py) 检测描述是否触发技能读取；[现成 viewer](https://github.com/anthropics/skills/blob/34040c9c568585f6929bedeaad110ad08f079624/skills/skill-creator/eval-viewer/generate_review.py) 展示产物与评分。

本项目文件在 `evals/icode/evals.json` 与 `evals/icode/trigger-evals.json`。它们保持官方可消费的字段，不在案例中夹带本地 `scenario` 或运行元数据。本地校验器采用严格子集：行为案例只接受上述四个字段。若上游要求默认路径 `evals/evals.json`，只在可丢弃的评估技能副本中复制到该位置，不能覆盖正式工程或全局安装。

上游 runner 实际运行 `claude -p`，在启动 cwd 所属项目的 `.claude/commands/` 临时创建描述代理，检测最初的 Skill/Read 调用；它不是 ICODE 完整行为执行器，也不保留本地结果 manifest 所需的逐案完整行为输出。它的触发成绩不能代替代码正确性、越权检查或整个 ICODE 工作流验收。这里不实现替代模型 SDK/runner。

## 场景与评审标准

输入语义来自当前 [自然语言入口](../references/natural_language_entry.md)、[verify](../steps/verify.md)、[crosscheck](../steps/crosscheck.md) 与 [debug](../references/debug_mode.md)，不重新定义这些合同。18 个行为案例的固定 ID 是离线覆盖清单；新增/删改 ID 需同时更新校验器、测试和此表。校验器能发现缺失、未知和重复 ID，不能证明一段任意文字仍具有原场景语义。

| ID | 场景 | 人工观察重点 |
|---|---|---|
| 1 | 中文显式 ICODE | 只做 plan，遵守设计停止点 |
| 2 | 英文混合大小写 ICODE | log 诊断，区分假设与证据，不实施修复 |
| 3 | 无绑定的普通请求 | 不强制创建 ICODE 工作流 |
| 4 | 能力咨询 | 可读帮助，但不创建工单或执行 close |
| 5 | 监听已有版本 | 不隐式 build/deploy，身份不明不报通过 |
| 6 | 测试目标隐式编译 | 静态识别 make test 依赖，保留禁编译约束 |
| 7 | completed 独立复评 | crosscheck 隔离、fresh 冻结前不读旧轮、不回写 |
| 8 | 显式路径优先 | 不被当前绑定或最大序号覆盖，无效不退回 latest |
| 9 | 当前 ticket 续接 | 检查开放 operation 实际结果，不重放副作用 |
| 10 | debug 隔离 | 不入索引、不参考正式历史、不进入 patch |
| 11 | 中文退出入口 | 明确不用 ICODE 后停止工作流接管 |
| 12 | 引用命令 | 引文仅数据，不获得执行授权 |
| 13 | 未知命令 | 帮助或澄清，不偷偷 start |
| 14 | flag 与禁令冲突 | 澄清前不执行争议动作 |
| 15 | 已完成单步继续 | 不自动扩大成编码或部署 |
| 16 | 严格零写入 | 不以 log 报告为由落盘 |
| 17 | 只编译 | 静态核对入口、LIMIT、并发，不读取设备配置 |
| 18 | 英文否定 | 不触发工作流、不做 Git 合并 |

触发集共 24 条，正例 12、负例 12。`should_trigger=true` 表示应读取技能以理解请求，**不表示授权执行工作流**；因此能力咨询和 help 是正例。负例包括相似开发任务、退出入口、英文否定和邮件/日志引用。没有真实会话状态的单句“继续”不适合独立 trigger runner；本集合将已绑定上下文明确写进查询，真正多轮恢复还需另做真实会话测试。

离线门槛固定为至少 24 条、正负标签各至少 12 条，允许继续扩充；截掉末尾 4 个负例或任一类不足 12 条均拒绝。这里只核对数量、严格布尔类型及唯一查询，不做关键词匹配或场景语义判分。

案例 `files=[]` 表示没有随包提供附件；prompt 中的 `demo`、工单状态、设备及日志是测试设定，不能冒充实际存在的设备或完成回执。执行前由评估主持人按设定准备隔离 fixture 并核实；做不到时保存阻塞输出并人工标为 inconclusive，不捏造前置条件。

## 离线命令及状态

在待评估源码副本根执行（Python 3.10+、Linux；不联网）：

```bash
python3 -B tools/check_skill_evaluation.py
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 \
  python3 -m pytest -q -p no:cacheprovider tests/test_skill_evaluation.py
```

本机使用 `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` 是为了避开宿主 anyio 与系统 pytest 的插件版本冲突；本组测试不依赖该插件。测试会创建 pytest 临时文件，全部为明显标识的合成 fixture，不能纳入模型评估成绩。

无模型结果时正常输出类似：

```json
{"structure":"valid","evidence":"pending","behavior":"pending_manual_review","case_count":18,"trigger_count":24,"positive_triggers":12}
```

| 字段/退出码 | 含义 |
|---|---|
| `structure=valid` | 输入结构、严格类型、已登记 ID、正负标签完整 |
| `evidence=pending` | 未传结果或指定结果文件不存在，没有模型结果可验 |
| `evidence=complete` | 本地 manifest 与逐案原始文件完整、字节摘要一致，不证明行为正确或来源真实 |
| `behavior=pending_manual_review` | 校验器始终输出此值，不做语义评分 |
| 0 | 请求的结构/证据检查未发现错误；可能仍 pending |
| 1 | 输入/证据无效，包括错误摘要、不完整矩阵、非法路径 |
| 2 | 使用 `--require-results`，但结果仍 pending |

显式提供结果时必须同时指定 `--evidence-root`。结果文件存在但内容为空/畸形、或 manifest 中任何输出缺失，均是 invalid，不能降格为“尚未执行”而吞掉错误。仅结果 manifest 不存在才是 pending。不要把 shell 退出码 0 单独解释成模型通过。

## 前向评估的结果 manifest schema v1

这是本项目的**独立证据 manifest**，不是官方 `grading.json`、`benchmark.json` 或 trigger runner 返回对象。每次评估使用一个模型、同一基线、每案例两组各一次；其他模型、随机重复轮次或迭代各存独立 manifest，不能混成重复 case。

严格顶层字段如下，全部必需且不允许附加 `pass`/`pass_rate` 等评分字段：

| 字段 | 类型及约束 |
|---|---|
| `schema_version` | 整数 `1`；bool/float 不接受 |
| `skill_name` | 字符串 `icode` |
| `model` | 实际运行的完整模型标识，非空字符串；来自运行工具响应/配置核验 |
| `model_source` | 非空字符串，记录供应商/宿主及模型身份取证位置；不得写密钥 |
| `runner` | 非空字符串，记录执行工具版本/命令或人工主持方法 |
| `source_commit` | 源码 Git 完整 40/64 位小写提交 ID，必须等于运行校验器所在副本的 HEAD |
| `skill_sha256` | 该副本根 `SKILL.md` 的原始字节 SHA-256，64 位小写十六进制 |
| `evals_sha256` | 本次 `--evals` 文件的原始字节 SHA-256 |
| `triggers_sha256` | 本次 `--triggers` 文件的原始字节 SHA-256 |
| `runs` | 36 条记录，18 个 case × with_skill/without_skill，矩阵严格完整 |

每条 run 恰好四个字段：`case_id` 为登记的整数 ID，`configuration` 为 `with_skill` 或 `without_skill`，`output_path` 为相对 evidence-root 的唯一原始输出文件路径，`output_sha256` 为该文件内容的 SHA-256。内容摘要指加密摘要，不是模型自行概括的“已通过”。

以下是**形状示意，不是可通过的结果或实测**；省略号不属于合法 JSON，实际提交必须填全 36 条并用真实值替换占位符：

```text
{
  "schema_version": 1,
  "skill_name": "icode",
  "model": "<实际完整模型 ID>",
  "model_source": "<供应商/宿主；模型身份日志位置>",
  "runner": "<工具及版本；执行方式>",
  "source_commit": "<git rev-parse HEAD 的完整值>",
  "skill_sha256": "<根 SKILL.md 的 64 位 SHA-256>",
  "evals_sha256": "<行为输入文件的 64 位 SHA-256>",
  "triggers_sha256": "<触发输入文件的 64 位 SHA-256>",
  "runs": [
    {"case_id": 1, "configuration": "with_skill",
     "output_path": "eval-1/with_skill/run-1/outputs/response.txt",
     "output_sha256": "<真实输出 SHA-256>"},
    {"case_id": 1, "configuration": "without_skill",
     "output_path": "eval-1/without_skill/run-1/outputs/response.txt",
     "output_sha256": "<真实输出 SHA-256>"},
    ...其余 case 2 至 18 的两组真实记录...
  ]
}
```

记录摘要与检查的实际命令：

```bash
git rev-parse HEAD
git status --short
sha256sum SKILL.md evals/icode/evals.json evals/icode/trigger-evals.json

# 运行前填入隔离评估目录与真实 manifest；不会调用模型。
EVAL_RUN=/absolute/path/to/iteration-1
python3 -B tools/check_skill_evaluation.py \
  --results "$EVAL_RUN/results.json" --evidence-root "$EVAL_RUN" --require-results
```

根 SKILL 摘要可以识别根文件未提交改动，但不能代替整棵源码树身份：主持人还须保存 `git diff`、相关 references/steps 与 fixture 清单的摘要和运行日志。若基线已变，应回到原评估副本重验，不可修改 manifest 来伪装一致。校验器验证本地字节一致性，无法认证提供者、证明日志由模型产生、排除精心伪造或证明所有外部证据真实。

输出必须是非空 UTF-8 原始答复/记录。仅“PASS”“全部通过”等状态短句、空白、NUL/非 UTF-8、未知字段、自填 passed 均拒绝；这只是排除明显证据占位，绝非关键词语义评分，其他空泛陈述仍需人工识别。路径禁止绝对路径、URL、`..`、符号链接、硬链接及特殊文件；目录组件按 fd 逐级打开，避免符号链接竞态，FIFO 不会阻塞检查。JSON 每文件最多 1 MiB，输出每文件最多 1 MiB，总输出最多 32 MiB；超限明确拒绝，不截断后冒充完整。需要更大证据时另行评审修改限额，不能用摘要替换本应保留的原始输出。

原始输出仅接受纯文本或 JSON 字符串；成功解析为 JSON 对象、数组、数值、布尔或 null 的内容全部拒绝，包括 `{"result":""}`、`[""]` 和非空 CLI 包装。JSON 字符串解码后必须非空白且不含 NUL，因此 `"\u0000"` 或文本中夹带该转义也会拒绝。CLI 的完整 JSON 包装应另存旁证，manifest 的 `output_path` 指向从真实响应中忠实提取的模型答复文件；提取过程不得生成、改写或补全答复。

上述检查只验证输出形状与证据完整性，不证明非空文本真实、正确或符合场景，也不承诺检测精心伪造的文本；`behavior` 仍为 `pending_manual_review`。

### 无工具纯文本前向评估

主代理另行获准使用现有 Claude CLI 的无工具模式时，两组仍用 `without_skill` / `with_skill`，不能改为 `baseline` / `withskill`。带技能组显式提供根 SKILL 和本轮所需入口/步骤文本（记录实际注入内容摘要），无技能组不提供这些内容；两组的案例与上下文 fixture 说明一致。只提供根 SKILL 而不提供其引用内容，会测到缺文档的能力边界，须如实记录，不能声称完整加载了 ICODE。

记录实际 CLI 版本、完整参数与原始 JSON 中的模型身份。本轮使用了 `--safe-mode --strict-mcp-config --tools '' --no-chrome --no-session-persistence --permission-mode dontAsk --output-format json`，响应 `modelUsage` 报告 `ark-code-latest`。后续每次运行仍须读取实际 modelUsage，不能根据客户端叫 Claude 就假设后端模型是 Anthropic，也不要为填写 model 字段覆盖已配置的默认模型。

纯文本评估观察意图理解、所述路由与约束理解，不能实证宿主发现/工具执行、原工单零回写、工单恢复或真实设备行为。将模式写入 `runner`，保留模型身份 JSON；人工评分也必须使用这个有限范围。离线 `evidence=complete` 仍然只代表证据完整，行为结论待独立人工评分，不扩展成宿主端到端通过。

### 2026-09-21 有限文本实测

基线 `1441fc6d91d544f3e097415fb9db278f71783715`，根 SKILL 未修改；本轮新增评估集按实际字节摘要登记。18 个案例分别取得有/无技能上下文的真实答复，共 36 份，证据检查为 complete。没有把 expected_output 发给被测模型。每组独立进程且禁用工具；首例带技能调用超预算后提高该次预算重试，原失败保留，因此不做统计或成本比较。

独立人工复核认为带技能组 **15 条在有限文本路由/约束范围内符合、3 条存在问题**，不是宿主工作流通过率：

| 案例 | 发现 | 后续验收重点 |
| --- | --- | --- |
| 2 | 从短日志把计时与源码关联推断说成事实 | 保留候选假说，事实必须指向真实证据 |
| 6 | 主体遵守禁部署，结尾却建议部署；隐式编译风险被说成必然编译 | 检查整段建议是否持续保留用户禁令 |
| 18 | 明确不用 ICODE 后仍扩展到工作流，混淆 Git 与 ICODE 命令 | 保持退出边界，核对每个命令和 Git 概念 |

无技能组也有事实推断和 Git 解释错误；不能概括为加载技能后全部改善。原始证据和人工复核保存在本地验收记录中，不公开会话及环境细节。初始交付是评估机制与缺口记录；随后用户授权修复上述问题，见下节。真实触发、启用工具后的行为和跨模型重复性仍待验证。

### 同日规则修复与反例复测

基线 `9e007c55aee7b26a5ec3fc0bf19fcd23ae0b877a` 加逐轮保存的未提交差异。保持18个案例 prompt、demo Makefile、参考文件选择和工具禁用模式不变，不向模型发送 expected_output；实际模型仍为 `ark-code-latest`。为避免截断，复测预算上限不同于最初基线，不作统计因果或成本优劣结论。

第一轮36份答复：禁部署尾部建议改善，但证据排除性断言和英文退出仍有问题。第二轮36份答复经独立语义复核：11项符合、4项有问题、3项有边界保留；日志证据问题得到纠正，英文退出不再扩展 ICODE，但普通 Git 解释仍有错误，debug 双跑建议、示例轮数传递及增量依赖描述存在邻接问题。两轮失败均保留，不能选择性只报成功案例。

修复只收紧既有边界：显式退出在路由前处理；观察与排除性断言分开；禁令覆盖尾部示例；debug 不自动要求正式工单；参数上限原值传递；评估环境不是目标工程。没有增加命令、改变状态机或以专门 Git 答案污染技能来刷分。最终复测与交付状态以[当前台账](tool-support-progress.md)为准；已加载技能文本的单次答复仍不能证明真实宿主发现及执行安全。

第三轮36份答复：带技能组独立复核为14项符合、1项问题、3项边界保留。原case2的证据推断、case6的必然重编/尾部部署建议、case18的退出和Git命令混淆均有实际改善；case6仍有额外执行位置推断，case1有目标/宿主目录混淆余留，case17把dry-run泛称无副作用，未计作完全符合。新发现的case5“三态/仅成功才记录”表述已澄清既有记录条款；debug正式修复建议也在shared/init/log三处同步条件化，并有失败→通过的合同回归。

随后仅对case5/10/15补测，两组共6份真实答复，3份带技能答复独立复核符合；case10这次另加入完整log/init参考，以覆盖之前的上下文缺口。**补测不与第三轮合并成18项全绿**，也不是一次完整manifest v1评估；具体轮数原值传递、实际收尾零写入及跨模型稳定性未因此获得实测证明。三轮完整矩阵、补测原始响应、逐轮diff和摘要均保留在本地验收记录中。

### 同日工程接入与构建预检补强

基线 `0b7a3f49` 加逐轮保留的差异，仍使用18个相同prompt、两组各一次。
本次上下文选择改为v2：case1/17补工程接入合同，case10补完整log/init，
仅case1/17提供demo Makefile（case6以其自身给定依赖关系为准）。
因此不能把本轮与旧轮直接作同输入提升率比较。

首组36份答复证据完整，独立文本复核初判带技能11符合/7问题，无技能5符合/12问题/1边界。
问题包括指定对象在尾部命令中丢失、log的debug出生类型混用、构建报告路径简写错误，
以及普通解释的附加泛化。计数只描述本轮答复，不是宿主执行成功率。
评审只读取保存的stdin和答复，宿主隐含system环境未完整采集；
因此其中“未提供cwd却声称宿主非Git”的判断需保留来源边界，不能单凭stdin缺字段断言幻觉。

源码侧发现并修复一个真实合同冲突：根L1矩阵曾把非Git cwd直接列为阻断，
与工程接入支持唯一嵌套Git根不一致；现统一为目标解析合同。
verify补充make dry-run的执行风险及主机/cwd/产物身份分离；自然语言入口要求示例也保持对象绑定、
内部字段由实际步骤取值。没有把测试专用Python/Git答案写进工作流。

新增真实隔离反例分别验证 `$(shell ...)`、`+`、`$(MAKE)` 在 `make -n` 下仍可写文件；
既有静态画像在同一fixture中不执行它们，3项通过。工程接入合同回归先失败后通过，
与上述反例一并纳入CI。完整模型复测单独保存，不用局部成功覆盖首组失败。

随后第二组完整36份答复通过证据完整性检查；独立复核将ICODE合同与普通知识错误分列：
带技能15符合/1问题/2边界，无技能8符合/5问题/5边界。口径不同于首组，不计算“提升率”。
指定工单示例、工程根边界和构建预检在本组符合；仍有case6把已证失败也归为inconclusive，
以及Makefile输出重定向、普通Python/Git解释的知识错误，不能把合同符合计数当全部事实正确。
宿主cwd等信息可能来自未保存的system环境，没有据此误判为目标根失败或幻觉。

针对case6，verify三态条款补齐“已证失败不得降为inconclusive”，同时明确监听与测试均适用，
失败事实与根因是否已查明分别判断；对应合同回归纳入CI。定向补测与完整矩阵分开保留，
不拼成18项全绿，也不据此认证跨模型稳定性、真实触发或工具执行。

定向补测取得case6两组答复，另用“已触发且有失败断言、根因未知”和“监听正常结束但未触发”
两个对照场景分别采集两组答复。带技能组两项outcome分别为fail与inconclusive，
主代理按给定记录核对该判定符合；只认证这两个分类断言，不认证附带解释全部正确。
这些新增场景不属于18例manifest，不改变完整矩阵原有15/1/2结果。

首次上线CI发现runner缺少rg，已将工程接入合同测试改用grep，
并增加隔离PATH不含rg的回归；后续构建、Pages部署及搜索通知均成功。

## 安全的 demo 隔离执行

本轮没有执行下述模型步骤。实施者应先准备已安装且获授权的模型宿主，以及官方 skill-creator 的固定版本副本；不要为本评估在主账号/主会话浏览器中登录、读取 cookie 或挂载生产凭据。

1. 使用新的容器/隔离用户运行目录，创建 demo 工程副本及独立的用户配置/ICODE 数据目录；禁止挂载正式工程、原工单、设备节点和真实账号目录。只有获授权的模型连接可用，设备工具以可审计 stub 或明确不可用方式提供，禁止连接真实设备。只改 HOME 而仍可访问生产目录并不构成安全隔离。
2. 复制**本次实际待评估快照**的根 SKILL、steps、references 与工具到隔离技能目录，并保留未提交 diff 和摘要。`git archive HEAD` 不含未提交改动，不能直接冒充当前快照。给副本添加 `evals/evals.json` 兼容路径时只做副本内操作。正常工程与 debug/crosscheck 案例分别构造自己的 fixture；每个 case/configuration 重置到相同初始快照。
3. `with_skill` 在独立新会话中显式提供该技能；`without_skill` 不加载根 ICODE 或其已安装副本，也不复用前组上下文。两组保持模型、提示、fixture、工具能力、预算一致，记录模型真实 ID；无技能组合理表示不了解 ICODE 的输出也是有效原始对比证据。不要把 expected_output 提供给被测模型。
4. 将 prompt 中的 demo 路径映射到该组 fixture；记录映射。已有工单须通过原有受控工具按合同准备，不能手填假完成状态骗过门禁。无法准备的案例保留缺口，不把被挡住的流程当完整执行成功。
5. 每次保存最终答复、完整工具调用轨迹、执行命令/cwd/返回码、写入前后差异、时间/token（工具真实提供时）与宿主日志。`response.txt` 是必需的原始输出，其他证据就近保存；人工逐项检查“没有写原工单”等负向条件，不能只相信模型声称没有写。受测模型不能写自己的评分或摘要 manifest，主持人独立收集并计算摘要。

使用官方 trigger runner 时，**从隔离 demo cwd 启动**，防止其向主工程 `.claude/commands` 写临时代理。以下变量由实施者填写，不是本轮已执行的调用：

```bash
SKILL_CREATOR=/absolute/path/to/anthropic-skills/skills/skill-creator
ICODE_COPY=/absolute/path/to/isolated/icode
DEMO_ROOT=/absolute/path/to/isolated/demo
MODEL_ID=actual-authorized-model-id
EVAL_RUN=/absolute/path/to/isolated/iteration-1
cd "$DEMO_ROOT" || exit 1
PYTHONPATH="$SKILL_CREATOR" python3 -m scripts.run_eval \
  --eval-set "$ICODE_COPY/evals/icode/trigger-evals.json" \
  --skill-path "$ICODE_COPY" --model "$MODEL_ID" \
  --runs-per-query 3 --num-workers 1 --timeout 60 \
  > "$EVAL_RUN/trigger-results.json"
```

此调用可能计费；运行前确认模型权限与预算。不要将 OpenAI/Codex 模型 ID 传给仅支持 Claude 的 runner；跨宿主行为对比可使用宿主现有代理能力收集同一 manifest，需明确这是哪个宿主的结果。runner 的异常/超时可能在上游合并成 `triggered=false`，尤其不能把因此“通过”的负例当可靠成绩，应保留 stderr 并由主持人排除基础设施失败。不要运行会自动优化/改写技能描述的 `run_loop`，本任务不授权修改根 SKILL。

## 人工评分与官方 viewer

行为运行交由官方 skill-creator 的测试主持流程或宿主已有独立代理，保留相同 prompt 的无技能/带技能两组；本项目没有宣称存在统一的官方行为 CLI。推荐与 viewer/聚合器兼容的输出布局：

```text
iteration-1/
  results.json                    # 本地证据 manifest
  eval-1/
    eval_metadata.json            # eval_id、eval_name、prompt、expectations
    with_skill/run-1/
      outputs/response.txt         # 原始模型输出
      transcript.md               # 实际工具调用轨迹
      grading.json                # 独立评审后才产生
      timing.json                 # 仅记录真实返回的运行指标
    without_skill/run-1/...
  eval-2/... 至 eval-18/...
```

按 `expected_output` 拆解可复核断言，独立评审者读取原始答复、轨迹与文件差异，再填写官方 `grading.json` 中 `expectations[{text, passed, evidence}]` 及真实 summary。`evidence` 指向具体文件/行/工具事件；回答里出现 “crosscheck”“不编译” 等词不代表做对了。没有执行条件的项记为未验证/无法判定，不写成通过；上游布尔分数不能表达完整的 inconclusive，需要在证据与单独评审说明中保留，并在报告中明确分母。建议盲评两组后再揭示配置，区分触发率、行为正确率、阻塞率，不用本地结构检查的成功数作模型分数。

评审后才调用官方聚合器和静态 viewer（先核对当前版本 CLI）：

```bash
cd "$SKILL_CREATOR" || exit 1
python3 -m scripts.aggregate_benchmark "$EVAL_RUN" --skill-name icode
python3 eval-viewer/generate_review.py "$EVAL_RUN" \
  --skill-name icode --benchmark "$EVAL_RUN/benchmark.json" \
  --static "$EVAL_RUN/review.html"
```

静态模式无需打开主会话浏览器，也不启动会清理占用端口的本地服务。只向 viewer 提供已审查的本地可信评估目录；它不是本地安全校验器，不要直接展示陌生文件或私密数据。没有 grading/模型输出时不生成空 benchmark 伪装成绩。

本交付验证了本地 schema/证据边界、官方文档接口兼容及上述 18×2 有限文本答复与人工复核。真实模型触发、启用工具的行为运行、官方 runner/viewer 实际运行、多轮 ticket 恢复、设备访问及完整宿主隔离均仍待相应实测。
