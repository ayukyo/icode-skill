# 步骤：构建与实机验证（/icode verify）

**命令**: `/icode verify [--build] [--deploy] [--listen | --test <target>] [--ticket <id>] [自然语言]` / `/icode verify --plan [--ticket <id>]` / `/icode verify <自然语言意图>`
- `--build [自然语言]`：独立构建 + 产物核验；读取存在的 LIMIT、静态检索工程编译入口、理解后附自然语言；不读取设备连接配置、不部署、不烧录、不监听。
- `--deploy`：只部署**已有、已核验身份**的构建产物并核对设备版本；不编译、不监听
- `--listen`：只监听设备上**当前运行版本**的日志并分析链路；不编译、不重新部署
- `--test <target>`：只对设备上**当前运行版本**执行指定目标测试；不编译、不重新部署。空转时停下确认触发动作
- 无动作选项：只识别用户自然语言意图，先解释要执行的阶段；意图为空或有歧义时询问，不自动部署。识别到明确动作后按相同的阶段门禁执行
- `--plan [--ticket <id>]`：只读展开指定/当前工单尚未满足的验证单元，生成验证计划；不部署、不记录 verification run
- 组合：阶段按 `--build → --deploy → --listen` 或 `--build → --deploy → --test <target>` 顺序显式声明；可以从中间开始（如 `--deploy --listen`）。`--listen` 与 `--test` 互斥；编译后要验证**新版本**，必须带 `--deploy`。`--plan` 独立使用。`--reuse` 已移除；没有任何动作选项时只解析自然语言。自然语言中的 `-j6`、`--module` 等属于请求内容，可用 `--` 显式分隔。
**产出**: 每个实际执行阶段向 metadata `verification_runs` 追加各自记录 + `verification_recorded` 事件；build 记 `kind=build, layer=build`，后续部署/监听/测试另记。失败阶段阻断后续阶段，不伪造成功记录。`--plan` 只写 `{ICODE_OUT_DIR}/verification_plan.json` 派生计划。无工单的单独 build 只写工程 `.icode_output/build/<run_id>/` 报告。均**不创建 Patch N 段、不写 `patch_history`、不改变 status/completed_steps**。
**会话**: 主会话

> **共享技能路由**：部署/监听前读取 [references/skill_routing.md](../references/skill_routing.md)，按设备、产物、多仓和消费者场景加载验证类技能。
> **证据习惯真源**：验证 baseline、layer/consumer/scenario 记录和 verified 边界统一执行 [references/evidence_and_verification.md](../references/evidence_and_verification.md)。
> **嵌入式/摄像头 profile**：`device_config.verification_profile` 或工单内 `embedded_baseline.json` 命中时，先用 `tools/embedded_profile.py` 校验并生成只读场景计划，再按同一个 verify 入口执行；不新增公开命令。
> **工程接入门**：读取 [references/project_intake.md](../references/project_intake.md)，验证必须绑定实际 Git 根、活动构建配置与制品身份；静态画像不运行构建/烧录命令。

> verify 是**独立构建/验证入口**：不承载源码修改语义（构建允许生成文件与日志），因此**不写 `patch_history`**（patch_history 只记录有变更的 Patch）；验证结果单独记 `verification_runs`。**验证通过不自动升级 `delivery_verdict=verified`**——delivery_verdict 是交付分层结论（`verified`/`verification_pending`/`blocked`/`not_applicable`），由 06_audit 终审结合全部验证证据（含本步骤记录）人工判定（见 [SKILL.md「可选字段」段](../SKILL.md)）。设备侧身份核对、轮询监听、三态判定和行为证据闭合复用 [08_patch.md §1.5](08_patch.md) 的对应子段；其“编译前置”和“部署”子段**只在 verify 显式选择相应阶段时执行**，不得从 patch 的合并流程继承隐式动作。

## 定位

**何时 verify**：已交付/打补丁后，需要单独再跑一轮实机验证（复测、回归、目标侧确认）而**无代码修改意图**时。改代码场景仍走 `/icode patch`；修改后需要自动监听时可用 `/icode patch --listen`，显式触发验证统一使用本步骤的 `--test`。

**何时不用 verify**：要改代码 → `/icode patch`；需要设备的阶段无 device_config → 先补配置；`--build` 不受设备配置或是否已部署限制。`--deploy` 无唯一可核验的现有产物 → 停止并说明所需产物身份，不猜测“最近文件”。

## 执行流程

先解析阶段序列再进入分支，**单独 build 必须在读取 device_config 前分流**。使用 `tools/verify_request.py --request '<原始 verify 参数>'` 校验阶段顺序、互斥性并保留 `actions`、`raw_request` 和 `natural_language`；通过宿主结构化参数或正确引用传值，禁止把原始用户文本拼成 shell 命令。解析器只识别语法，**不会理解或执行自然语言中的编译命令**；无动作选项时由主代理解释意图。意图不明时先询问；明确后仍须用解析出的实际阶段逐项执行 `action-policy --action verify --verify-action <build|deploy|listen|device_test|plan>`，不能借 `intent` 宽松预检绕过部署权限。空请求只展示用法，不执行副作用。

**阶段执行合同**：`--build` 只运行下文的构建分支；`--deploy` 只读取刚完成且已核验的构建产物，或已有唯一可核验产物，部署并核对目标版本；单独 `--listen`/`--test` 只针对设备当前运行版本取证。任何副作用前先对**全部选定阶段**做权限预检；每阶段开始前再检查其依赖、revision 和工单状态，执行、留证、记录结果；失败或 `inconclusive` 时停止后续阶段。`--build --deploy --listen` 与 `--build --deploy --test <target>` 分别执行三阶段；`--deploy --listen`/`--deploy --test <target>` 分别执行两阶段。单独监听/测试不触发构建和部署，也不得把新构建产物冒充设备已部署版本。候选监听/测试命令须先静态核对调用链及依赖；带**隐式编译前置**或部署副作用的测试目标（例如 `make test` 依赖可执行文件构建）不得用于单独 `--test`，应直接调用设备上已存在的程序或测试接口。设备阶段仍须核对设备身份、现行版本和验证合同；无法识别运行版本时记 `inconclusive`，不能宣称验证了本次构建。

> **风险与实际执行分开**：构建依赖说明测试目标可能重编，是否实际重编还取决于目标存在性、时间戳及具体规则（含头文件、生成依赖、伪目标等），不能简化为只看源文件变化，也不能说 `make test` 每次必然调用编译器。但这种可能性仍不满足严格禁编译请求，不能靠猜测缓存命中放行。该禁令约束整段答复及后续建议：当前只测现有版本时，省略新版本 build/deploy 路线，即使写成条件句也不是本次所需；缺入口就说明缺口，不扩大阶段。

> **静态预检不是试执行**：`make -n` / `--dry-run` 不是无副作用沙箱；Makefile 展开中的 `$(shell ...)`、带 `+` 或 `$(MAKE)` 的 recipe 仍可能执行。先阅读入口、include 与调用链，未核实前不推荐用 dry-run 安全探测。另须分别核实执行主机、cwd 与产物身份；`./app` 仅是相对路径，不能单凭它断定命令在开发机还是设备上执行。依据见 [工程接入合同](../references/project_intake.md) 与 [GNU make 手册](https://www.gnu.org/software/make/manual/make.html)。

### `--build` 独立构建分支

1. **工程与记录归属**：按工程接入合同解析实际执行根。显式 `--ticket` 或 UI 锁定工单必须唯一解析并使用其执行根，失败即停止，禁止换 latest。单独 build 未指定工单时仅复用当前根已有的唯一活动/当前工单；不存在则写 `<工程根>/.icode_output/build/<run_id>/`，无需创建工单或设备配置。组合设备阶段必须先解析到可写工单，不能把无工单 build 报告当设备验证记录。closed 工单仍冻结，不能以无工单报告绕过。`log_done` 等分析阶段也可单独构建，不要求先修改源码或完成 code。工单执行走现有 verify 的 `step start/check/finish`；无工单不伪造 metadata/事件。
2. **读取 LIMIT（存在时必读）**：按 `references/dir_and_metadata.md` 的 `resolve_project_id` 及 F1 归一化定位 `~/.claude/icode_data/limits/<project_id>.md`，同时检查实际 checkout 的 `.icode_output/limit.local/<project_id>.md`，按 `steps/limit.md` 的有效视图规则合并。不存在则明确记 `limit_found=false` 后继续，不能因此阻断，也不能虚构红线。记录源路径、条目和摘要，提取编译入口、并发、增量/clean、环境、源码修改、制品保护及目标板约束。
3. **理解自然语言与检索入口**：保留原始请求，提取目标/模块、全量或增量、配置/板型/工具链、并发、产物与可选打包需求。先读取用户点名入口及 LIMIT 强制入口，再读 README 的构建段、检索工程根/目标模块的编译脚本和 Makefile/CMakeLists.txt 等。复用现有静态工程画像，禁止为识别用途运行 `--help`、source 脚本或任意候选入口。记录候选路径及 file:line，阅读实际调用链，区分**编译、install、复制打包、镜像生成、部署**；仅复制已有 BIN 的 package 脚本不能证明已编译。
4. **确定并公示具体命令**：用户明确意图决定目标和范围，LIMIT 决定硬约束；已有 wrapper 优先，脚本内容与 README 用来核验是否真的满足意图。未指定范围时优先工程已有的增量目标，不默认全量/clean。给出实际 cwd、逐条命令、来源、预期产物和副作用后，对已授权的普通构建直接执行。若目标有歧义或命令与有效红线冲突，先报告具体冲突并询问，不悄悄换目标、忽略限制或扩大到刷机；继续不依赖答案的只读核查。不猜通用脚本参数，不把自然语言当 shell。
5. **构建前门禁**：绑定本地 HEAD、工作区 diff、活动配置、工具链及预期源码特征。已有 upstream 时只读比较本地与已知跟踪提交，不能把 fetch 成功或提交日期当工作区已更新；落后则展示差异，按用户明确 baseline 构建，不能自行 pull/reset。检查嵌套 `nproc`/裸 `-j` 是否覆盖限制，环境与工具是否可用，避免跨 checkout wrapper 指错根；可能重打产物目录时先备份已有交付件。工单执行 `step check --boundary before_side_effect` 后记录构建 operation；无工单也保留同等检查证据。源码修改/删除/覆盖交付物和硬件写入仍遵守用户授权与工程红线。
6. **实际执行与退出码**：顺序执行构建及必要 install，日志写独立 run 目录；捕获真实退出码，不能用管道最后一段或包校验成功冒充编译成功。长构建后台运行，按用户频率或持续工作进度回报；等待后工单执行 `after_wait` 回检，不盲重放不确定动作。失败最多沿同一目标增量排查，禁止为通过而更换验收目标；可修的用户级环境按权限处理，源码修改另走 patch。
7. **核验产物并判定**：记录目标、路径、大小、SHA-256、构建标识、源码 baseline/配置与日志；对本次预期特征用符号/反汇编/构建输出等匹配实际 BIN。同名/新复制时间/旧包 sha256 全通过不算 fresh 构建证据；增量无重编仅在依赖和制品身份已匹配时可通过。单独打包、缺少预期产物、命令未执行或身份无法绑定均不能报构建通过。执行失败记 `fail`，取证/目标不明记 `inconclusive`；全部命令成功且产物核验通过才记 `pass`。
8. **报告与记录**：每次写 `build_run.json/.md`，包含 `raw_request`、解析意图、`execution_root`、`limit_refs`、`command_source_refs`、逐条命令/cwd/退出码/日志、实际源码基线、产物身份及结论。有工单放 `<ICODE_OUT_DIR>/build/<run_id>/` 并通过下述原子命令追加运行记录；无工单只写工程 build 报告。失败/inconclusive 也保留报告。不标记 deploy pass，不清除设备/物理验证债务，不自动升级交付结论。

   ```bash
   python3 tools/icode_control.py record-verification --dir "{ICODE_OUT_DIR}" \
     --kind build --layer build --build-source fresh --outcome pass \
     --artifact-identity '<产物路径及sha256/构建标识>' \
     --baseline '<实际HEAD/diff/配置/工具链摘要>' \
     --consumer '<构建目标>' --scenario '<本次构建场景>' \
     --evidence '<build_run.json及实际构建日志>' --request-id '<run_id>'
   ```

   fail/inconclusive 如实替换 outcome，未启动编译时 build_source=unknown。`kind=build` 固定 layer=build，不带 device，不允许 reused/existing；pass 必须提供 fresh、非空产物身份及源码 baseline。显式验证合同需要 profile/baseline_ref 时仍按合同记录，不新增/覆盖合同来消债。

**示例（由工程上下文确定实际命令）**：

```text
/icode verify --build
/icode verify --build 仅增量编译 module_a，并发6，不烧录
/icode verify --build --ticket myproject-1 使用 LIMIT 的 wrapper，只编译 sensor_driver
/icode verify --build 用 scripts/build.sh 编译 release 配置，先 install 再生成新包，保留旧包
/icode verify --build --deploy --listen
/icode verify --build --deploy --test camera_stream
/icode verify --deploy --listen
/icode verify --deploy --test camera_stream
/icode verify 只查看设备当前日志，不重新部署
```

### `--plan` 只读分支

本分支是派生查询，不启动 verify attempt，不生成 verification run 或完成回执；不能为满足执行模式的输出端口而补造验证记录。

1. 用 `resolve-ticket --ticket <id> --workspace <工程根>`（省略 `--ticket` 时解析当前工单）得到 `ICODE_OUT_DIR`。
2. 若 device_config 声明 `verification_profile`，或工单目录存在 `embedded_baseline.json`，先运行：

   ```bash
   python3 tools/embedded_profile.py validate \
     --baseline "{BASELINE_MANIFEST}"
   python3 tools/embedded_profile.py plan \
     --baseline "{BASELINE_MANIFEST}" \
     --output "{ICODE_OUT_DIR}/embedded_verification_plan.json"
   ```

   profile 工具只解释数据并生成计划，不连接设备、执行输入字符串或自动注入故障。

3. 运行验证债务计划；有 embedded plan 时以它的合同作只读 overlay，不改 metadata：

   ```bash
   python3 tools/verification_debt.py plan \
     --ticket-dir "{ICODE_OUT_DIR}" \
     [--contract-file "{ICODE_OUT_DIR}/embedded_verification_plan.json"] \
     --output "{ICODE_OUT_DIR}/verification_plan.json"
   ```

4. 展示尚未满足单元的显式 required cell、当前原因、所需设备/制品/源码 baseline 摘要、指标和证据格式，然后结束本步骤。不得自动执行计划，不得追加 `verification_runs`，不得升级 `delivery_verdict`；legacy 工单缺验证合同则标 `legacy_untracked`。

### 执行设备阶段（`--deploy` / `--listen` / `--test`）

1. **身份解析 + 拓扑门禁**：按 `resolve-ticket --ticket <id> --workspace <工程根>`（或最新目录只读便利）确定 `ICODE_OUT_DIR`；worktree 工单先过统一拓扑门禁（§3.8），verdict=blocked 报错退出
2. **读 device_config 与 profile**：按 [08_patch.md §1.5](08_patch.md)「读配置」计算 `project_id`（含 F1 worktree 归一）→ Read `~/.claude/icode_data/device_config/<project_id>.json` → 校验 `project_id` 一致；缺失配置则诊断提示。只有声明 `--deploy` 时才要求 `deploy_enabled=true`；单独监听/测试按连接能力核查，不因禁用部署误阻断只读取证。若存在 `verification_profile={profile,baseline_manifest}`，只允许 `embedded|camera` 与 baseline 数据路径；先执行 `embedded_profile.py validate/plan`，禁止把配置扩展成任意 probe 命令。读取计划中的 `verification_contract`：metadata 未登记时用 `metadata-update --set-json` 原子登记；已登记且完全一致则复用；存在差异则展示合同差异并停止，不得静默覆盖用户验收合同。登记后重新 `validate`，再进入验证动作。
3. **执行验证动作**：
   - `--deploy`：只部署已核验产物并核对设备当前版本；单独执行时选择已有唯一产物，组合 `--build --deploy` 时使用本次构建的产物。记录真实 `artifact_identity` 和构建来源，不重复编译；若设备配置的 deploy 意图内还含编译指令，展示冲突并停止，不能隐式补编译
   - `--listen` / `--test`：对已部署的设备按 [08_patch.md §1.5](08_patch.md) 轮询监听/三态判定（含特征可见性核查 + 证据双通道标注）；`--test` 走空转确认节奏。不运行构建或部署脚本；组合时使用前一部署阶段核验过的设备版本
4. **三态判定**（仅监听类）：`pass`（修复生效/链路通）/ `fail`（进不了闭环但可定位）/ `inconclusive`（未触发 / 特征不可见 / 证据模糊）。**未触发 ≠ 失败**，如实记 `inconclusive` 并标注触发条件未发生
5. **记录 verification_runs**（metadata，实际执行的阶段按真实 pass/fail/inconclusive 追加，不是仅成功才追加；完全未执行只报阻塞、不伪造 run。schema 见 [schemas/ticket-metadata.schema.json](../schemas/ticket-metadata.schema.json)）：
   ```json
   {
     "run_id": "<uuid>",
     "at": "date +%Y-%m-%dT%H:%M:%S",
     "kind": "build | deploy | listen | device_test",
     "build_source": "fresh | reused | existing | unknown",
     "device": "<连接标识，可选>",
     "artifact_identity": "<实际部署 commit/构建标识，可选>",
     "window": "<监听窗口描述，可选>",
     "profile": "generic | embedded | camera，可选",
     "baseline_ref": "sha256:<与 verification_contract.baseline_ref 完全一致的摘要>",
     "metrics": {"fps": 29.7},
     "outcome": "pass | fail | inconclusive",
     "evidence": "file:line / 日志时间点 / 行为证据（双通道标注）",
     "note": "<可选补充>"
   }
   ```
   **vNext 工单禁止分两步直写**；每阶段用控制面原子记录 metadata+事件。`build_source=reused` 仅用于历史记录读取，公开命令不再提供 `--reuse`；本次构建后部署记 `fresh`，已有产物部署记 `existing`。公开 `--test <target>` 的 target 是测试目标，记录在 `--scenario` 中；它不等于设备连接 ID，`--device <id>` 必须来自已核验的设备配置，不能把 target 原样填入：

   ```bash
   python3 tools/icode_control.py record-verification --dir {ICODE_OUT_DIR} \
     --kind <deploy|listen|device_test> --build-source <fresh|reused|existing|unknown> \
     --outcome <pass|fail|inconclusive> --evidence '<文件:行/时间窗/行为证据>' \
     [--device <id>] [--scenario <target或验证场景>] [--artifact-identity <commit/build-id>] [--window <window>] \
     [--profile <generic|embedded|camera>] \
     [--baseline-ref <sha256:与合同完全一致的64位小写十六进制摘要>] \
     [--metrics-json '{"fps":29.7}'] \
     [--note <text>] [--request-id <key>]
   ```
6. **输出**：验证结论（三态）+ 证据 + 是否待后续验证；**明确标注「本次验证通过 ≠ delivery_verdict=verified」**（后者由终审按交付分层判定）

## 与 patch 的关系（分离边界）

| 维度 | `/icode verify` | `/icode patch --listen` |
|---|---|---|
| 代码修改 | 不涉及源码修改（build可生成构建产物） | 承载 Patch N（含修改） |
| patch_history | **不写** | 有文件 mutation 才写；纯验证轮不新增条 |
| verification_runs | **写**（每次） | 1.5 实机验证结果**同样写**（与 patch_history 分离） |
| status/completed_steps | 不变 | 不变 |
| delivery_verdict | 不自动升级 | 不自动升级 |

## 强制约束

- **禁止**把纯验证写成 Patch N 段 / 给 `patch_history` 塞验证记录（语义污染：patch_history 只承载代码变更）
- **禁止** `verify` 通过就自行 `delivery_verdict=verified`（交付分层契约，见 [references/control_plane.md](../references/control_plane.md) 与 [06_audit.md](06_audit.md)）
- **禁止**部署未经身份核验的旧产物或用同名文件/时间戳推定版本；无唯一候选时停下，不增加公开 `--reuse` 捷径
- **禁止**单独 `--listen`/`--test` 隐式编译或部署；新构建版本要进入设备必须显式包含 `--deploy`
- **禁止** 未触发判 fail（触发条件未发生 → `inconclusive`，先问用户）
- **禁止** profile 计划工具执行设备命令；烧录、EEPROM/寄存器写、分区切换、断电或破坏性 fault injection 均需用户显式授权
- **禁止** `--build` 读取设备连接配置、部署/烧录/上传/监听，或把仅打包成功记为编译通过
- 设备侧硬规则（不 `--force`、不删除、只读 git）继承 08_patch §1.5 与 worktree 只读白名单

## MCP 推荐

verify 为 **L1（短决策记录）**：结构化验证执行（判断+设备连接+日志分析），监听轮询的增量日志达到 `long_text_threshold_bytes` 时走 `verify.listen_log_summary` 规则（tool=summarize），见 [08_patch.md](08_patch.md) 与 [references/mcp_per_step.md](../references/mcp_per_step.md)。其它本地 MCP 按 `mcp/icode-mcp-policy/policy.json` 的 verify routes 按条件选用。非监听主动作也记录 `listen_mode=false/incremental_bytes=0` 的 not-eligible 判定；新工单 verify 的 finish 会校验本步骤 trace。无工单构建将等价决策记入 build_run，不创建工单。

**强制约束**：🟢/🟢*/⚪ 语义 + 双保险机制（执行步骤内嵌 + thinking_core gate）详见 [SKILL.md「MCP 调用覆盖强制化」](../SKILL.md) + [references/mcp_per_step.md「双保险机制」](../references/mcp_per_step.md)。

**派生报告按需生成**：`verification_debt.py` 默认只写 `--output` 指定的 JSON；需阅读或导出 Markdown 时再提供 `--markdown <路径>`。旧双文件调用仍兼容，不删除历史报告。
