# 步骤：构建与实机验证（/icode verify）

**命令**: `/icode verify --build [--ticket <id>] [自然语言]` / `/icode verify [--deploy | --listen | --test <target>] [--reuse <artifact>]` / `/icode verify --plan [--ticket <id>]`
- `--build [自然语言]`：独立构建 + 产物核验；读取存在的 LIMIT、静态检索工程编译入口、理解后附自然语言；不读取设备连接配置、不部署、不烧录、不监听。
- 默认（无参）：对当前工单执行**部署 + 一轮实机验证**（读 `~/.claude/icode_data/device_config/<project_id>.json` 配置；无配置/deploy_enabled=false → 按 08_patch §1.5 缺失诊断提示，不静默跳过）
- `--deploy`：仅执行部署（构建/烧录/上传 + 版本核对），不监听
- `--listen`：部署 + 自动监听（连设备部署 + 持续轮询 LOG + 实时链路分析，告知触发即监听、用户随时操作被捕获）
- `--test <target>`：部署 + 指定目标侧测试（显式触发验证节奏：空转停下确认用户已操作再继续，防误触发）
- `--reuse <artifact>`：构建来源修饰符，复用指定/最近产物并记 `build_source=reused` + `artifact_identity`，跳过重新构建
- `--plan [--ticket <id>]`：只读展开指定/当前工单尚未满足的验证单元，生成验证计划；不部署、不记录 verification run
- 互斥与组合：`--build`/`--plan`/`--deploy`/`--listen`/`--test` 主动作互斥；`--reuse` 只可与部署类动作组合，不能与 `--build`/`--plan` 组合。verify 选项放在自然语言之前；自然语言中的 `-j6`、`--module` 等属于构建意图。可用 `--` 显式分隔自然语言。
**产出**: 执行模式向 metadata `verification_runs` 追加一条 + `verification_recorded` 事件；build 记 `kind=build, layer=build`。`--plan` 只写 `{ICODE_OUT_DIR}/verification_plan.json/.md` 派生计划。无工单的 build 只写工程 `.icode_output/build/<run_id>/` 报告，不新建工单。均**不创建 Patch N 段、不写 `patch_history`、不改变 status/completed_steps**。
**会话**: 主会话

> **共享技能路由**：部署/监听前读取 [references/skill_routing.md](../references/skill_routing.md)，按设备、产物、多仓和消费者场景加载验证类技能。
> **证据习惯真源**：验证 baseline、layer/consumer/scenario 记录和 verified 边界统一执行 [references/evidence_and_verification.md](../references/evidence_and_verification.md)。
> **嵌入式/摄像头 profile**：`device_config.verification_profile` 或工单内 `embedded_baseline.json` 命中时，先用 `tools/embedded_profile.py` 校验并生成只读场景计划，再按同一个 verify 入口执行；不新增公开命令。
> **工程接入门**：读取 [references/project_intake.md](../references/project_intake.md)，验证必须绑定实际 Git 根、活动构建配置与制品身份；静态画像不运行构建/烧录命令。

> verify 是**独立构建/验证入口**：不承载源码修改语义（构建允许生成文件与日志），因此**不写 `patch_history`**（patch_history 只记录有变更的 Patch）；验证结果单独记 `verification_runs`。**验证通过不自动升级 `delivery_verdict=verified`**——delivery_verdict 是交付分层结论（`verified`/`verification_pending`/`blocked`/`not_applicable`），由 06_audit 终审结合全部验证证据（含本步骤记录）人工判定（见 [SKILL.md「可选字段」段](../SKILL.md)）。设备侧执行细节（轮询监听/三态判定/特征可见性核查/行为证据闭合）**全部复用 [08_patch.md §1.5](08_patch.md)**，本步骤只定义独立入口 + 记录契约，不复制设备流程。

## 定位

**何时 verify**：已交付/打补丁后，需要单独再跑一轮实机验证（复测、回归、目标侧确认）而**无代码修改意图**时。改代码场景仍走 `/icode patch`；修改后需要自动监听时可用 `/icode patch --listen`，显式触发验证统一使用本步骤的 `--test`。

**何时不用 verify**：要改代码 → `/icode patch`；部署类动作无 device_config → 先补配置；`--build` 不受设备配置或是否已部署限制。`--reuse` 但无既有构建产物 → 报错提示先 `--deploy`/`--listen`/`--test`。

## 执行流程

先解析主动作再进入分支，**build 必须在读取 device_config 前分流**。使用 `tools/verify_request.py --request '<原始 verify 参数>'` 校验选项互斥并保留 `raw_request` 和 `natural_language`；通过宿主结构化参数或正确引用传值，禁止把原始用户文本拼成 shell 命令。解析器只识别语法，**不会理解或执行自然语言中的编译命令**；语义由主代理按下节证据解析。

### `--build` 独立构建分支

1. **工程与记录归属**：按工程接入合同解析实际执行根。显式 `--ticket` 或 UI 锁定工单必须唯一解析并使用其执行根，失败即停止，禁止换 latest。未指定时仅复用当前根已有的唯一活动/当前工单；不存在则写 `<工程根>/.icode_output/build/<run_id>/`，无需创建工单或设备配置。closed 工单仍冻结，不能以无工单报告绕过。`log_done` 等分析阶段也可构建，不要求先修改源码或完成 code。工单执行走现有 verify 的 `step start/check/finish`；无工单不伪造 metadata/事件。
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
```

### `--plan` 只读分支

1. 用 `resolve-ticket --ticket <id> --workspace <工程根>`（省略 `--ticket` 时解析当前工单）得到 `ICODE_OUT_DIR`。
2. 若 device_config 声明 `verification_profile`，或工单目录存在 `embedded_baseline.json`，先运行：

   ```bash
   python3 tools/embedded_profile.py validate \
     --baseline "{BASELINE_MANIFEST}"
   python3 tools/embedded_profile.py plan \
     --baseline "{BASELINE_MANIFEST}" \
     --output "{ICODE_OUT_DIR}/embedded_verification_plan.json" \
     --markdown "{ICODE_OUT_DIR}/embedded_verification_plan.md"
   ```

   profile 工具只解释数据并生成计划，不连接设备、执行输入字符串或自动注入故障。

3. 运行验证债务计划；有 embedded plan 时以它的合同作只读 overlay，不改 metadata：

   ```bash
   python3 tools/verification_debt.py plan \
     --ticket-dir "{ICODE_OUT_DIR}" \
     [--contract-file "{ICODE_OUT_DIR}/embedded_verification_plan.json"] \
     --output "{ICODE_OUT_DIR}/verification_plan.json" \
     --markdown "{ICODE_OUT_DIR}/verification_plan.md"
   ```

4. 展示尚未满足单元的显式 required cell、当前原因、所需设备/制品/源码 baseline 摘要、指标和证据格式，然后结束本步骤。不得自动执行计划，不得追加 `verification_runs`，不得升级 `delivery_verdict`；legacy 工单缺验证合同则标 `legacy_untracked`。

### 执行验证分支

1. **身份解析 + 拓扑门禁**：按 `resolve-ticket --ticket <id> --workspace <工程根>`（或最新目录只读便利）确定 `ICODE_OUT_DIR`；worktree 工单先过统一拓扑门禁（§3.8），verdict=blocked 报错退出
2. **读 device_config 与 profile**：按 [08_patch.md §1.5](08_patch.md)「读配置」计算 `project_id`（含 F1 worktree 归一）→ Read `~/.claude/icode_data/device_config/<project_id>.json` → 校验 `project_id` 一致；缺失/deploy_enabled=false → 按 §1.5 缺失诊断提示。若存在 `verification_profile={profile,baseline_manifest}`，只允许 `embedded|camera` 与 baseline 数据路径；先执行 `embedded_profile.py validate/plan`，禁止把配置扩展成任意 probe 命令。读取计划中的 `verification_contract`：metadata 未登记时用 `metadata-update --set-json` 原子登记；已登记且完全一致则复用；存在差异则展示合同差异并停止，不得静默覆盖用户验收合同。登记后重新 `validate`，再进入验证动作。
3. **执行验证动作**：
   - `--deploy`：部署（构建/烧录/上传）+ 版本核对（记录实际部署 commit/构建标识到 `artifact_identity`）
   - `--listen` / `--test`：部署后按 [08_patch.md §1.5](08_patch.md) 轮询监听/三态判定（含特征可见性核查 + 证据双通道标注）；`--test` 走空转确认节奏
   - `--reuse`：从 `verification_runs` 最近构建（或既有产物）取 `artifact_identity`，跳过构建直接部署，但本次 `kind` 仍记实际动作（deploy/listen/device_test）
4. **三态判定**（仅监听类）：`pass`（修复生效/链路通）/ `fail`（进不了闭环但可定位）/ `inconclusive`（未触发 / 特征不可见 / 证据模糊）。**未触发 ≠ 失败**，如实记 `inconclusive` 并标注触发条件未发生
5. **记录 verification_runs**（metadata，追加一条，schema 见 [schemas/ticket-metadata.schema.json](../schemas/ticket-metadata.schema.json)）：
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
   **vNext 工单禁止分两步直写**；用控制面原子记录 metadata+事件。公开 `--test <target>` 在这里映射到内部控制面参数 `--device <id>`，内部字段名不构成公开 `/icode` 别名：

   ```bash
   python3 tools/icode_control.py record-verification --dir {ICODE_OUT_DIR} \
     --kind <deploy|listen|device_test> --build-source <fresh|reused|existing|unknown> \
     --outcome <pass|fail|inconclusive> --evidence '<文件:行/时间窗/行为证据>' \
     [--device <id>] [--artifact-identity <commit/build-id>] [--window <window>] \
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
- **禁止** `--reuse` 跳过构建却不核对产物身份（artifact_identity 必须与最近构建一致）
- **禁止** 未触发判 fail（触发条件未发生 → `inconclusive`，先问用户）
- **禁止** profile 计划工具执行设备命令；烧录、EEPROM/寄存器写、分区切换、断电或破坏性 fault injection 均需用户显式授权
- **禁止** `--build` 读取设备连接配置、部署/烧录/上传/监听，或把仅打包成功记为编译通过
- 设备侧硬规则（不 `--force`、不删除、只读 git）继承 08_patch §1.5 与 worktree 只读白名单

## MCP 推荐

verify 为 **L1（短决策记录）**：结构化验证执行（判断+设备连接+日志分析），监听轮询的增量日志达到 `long_text_threshold_bytes` 时走 `patch.listen_log_summary` 规则（tool=summarize），见 [08_patch.md](08_patch.md) 与 [references/mcp_per_step.md](../references/mcp_per_step.md)。其余 MCP 不推荐。

**强制约束**：🟢/🟢*/⚪ 语义 + 双保险机制（执行步骤内嵌 + thinking_core gate）详见 [SKILL.md「MCP 调用覆盖强制化」](../SKILL.md) + [references/mcp_per_step.md「双保险机制」](../references/mcp_per_step.md)。
