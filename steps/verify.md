# 步骤：实机验证（/icode verify）

**命令**: `/icode verify [--deploy | --listen | --test <target>] [--reuse <artifact>]` / `/icode verify --plan [--ticket <id>]`
- 默认（无参）：对当前工单执行**部署 + 一轮实机验证**（读 `~/.claude/icode_data/device_config/<project_id>.json` 配置；无配置/deploy_enabled=false → 按 08_patch §1.5 缺失诊断提示，不静默跳过）
- `--deploy`：仅执行部署（构建/烧录/上传 + 版本核对），不监听
- `--listen`：部署 + 自动监听（连设备部署 + 持续轮询 LOG + 实时链路分析，告知触发即监听、用户随时操作被捕获）
- `--test <target>`：部署 + 指定目标侧测试（显式触发验证节奏：空转停下确认用户已操作再继续，防误触发）
- `--reuse <artifact>`：构建来源修饰符，复用指定/最近产物并记 `build_source=reused` + `artifact_identity`，跳过重新构建
- `--plan [--ticket <id>]`：只读展开指定/当前工单尚未满足的验证单元，生成验证计划；不部署、不记录 verification run
- 互斥与组合：主动作 `--deploy`/`--listen`/`--test` 三选一；`--reuse` 不是主动作，可与任一主动作组合。多个主动作同时给出才报参数错误。
**产出**: 执行模式向 metadata `verification_runs` 追加一条 + `verification_recorded` 事件；`--plan` 只写 `{ICODE_OUT_DIR}/verification_plan.json/.md` 派生计划。两者都**不创建 Patch N 段、不写 `patch_history`、不改变 status/completed_steps**
**会话**: 主会话

> **共享技能路由**：部署/监听前读取 [references/skill_routing.md](../references/skill_routing.md)，按设备、产物、多仓和消费者场景加载验证类技能。
> **证据习惯真源**：验证 baseline、layer/consumer/scenario 记录和 verified 边界统一执行 [references/evidence_and_verification.md](../references/evidence_and_verification.md)。
> **嵌入式/摄像头 profile**：`device_config.verification_profile` 或工单内 `embedded_baseline.json` 命中时，先用 `tools/embedded_profile.py` 校验并生成只读场景计划，再按同一个 verify 入口执行；不新增公开命令。

> verify 是**纯实机验证独立入口**：不承载代码修改语义（无文件 mutation），因此**不写 `patch_history`**（patch_history 只记录有变更的 Patch）；验证结果单独记 `verification_runs`。**验证通过不自动升级 `delivery_verdict=verified`**——delivery_verdict 是交付分层结论（`verified`/`verification_pending`/`blocked`/`not_applicable`），由 06_audit 终审结合全部验证证据（含本步骤记录）人工判定（见 [SKILL.md「可选字段」段](../SKILL.md)）。设备侧执行细节（轮询监听/三态判定/特征可见性核查/行为证据闭合）**全部复用 [08_patch.md §1.5](08_patch.md)**，本步骤只定义独立入口 + 记录契约，不复制设备流程。

## 定位

**何时 verify**：已交付/打补丁后，需要单独再跑一轮实机验证（复测、回归、目标侧确认）而**无代码修改意图**时。改代码场景仍走 `/icode patch`；修改后需要自动监听时可用 `/icode patch --listen`，显式触发验证统一使用本步骤的 `--test`。

**何时不用 verify**：要改代码 → `/icode patch`；未部署过且无 device_config → 先补配置；`--reuse` 但无既有构建产物 → 报错提示先 `--deploy`/`--listen`/`--test`。

## 执行流程

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
     "kind": "deploy | listen | device_test",
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
| 代码修改 | 不涉及（纯验证） | 承载 Patch N（含修改） |
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
- 设备侧硬规则（不 `--force`、不删除、只读 git）继承 08_patch §1.5 与 worktree 只读白名单

## MCP 推荐

verify 为 **L1（短决策记录）**：结构化验证执行（判断+设备连接+日志分析），监听轮询的增量日志达到 `long_text_threshold_bytes` 时走 `patch.listen_log_summary` 规则（tool=summarize），见 [08_patch.md](08_patch.md) 与 [references/mcp_per_step.md](../references/mcp_per_step.md)。其余 MCP 不推荐。

**强制约束**：🟢/🟢*/⚪ 语义 + 双保险机制（执行步骤内嵌 + thinking_core gate）详见 [SKILL.md「MCP 调用覆盖强制化」](../SKILL.md) + [references/mcp_per_step.md「双保险机制」](../references/mcp_per_step.md)。
