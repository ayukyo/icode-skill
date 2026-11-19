# lite 独立审核

修改代码后必须经过真实、全新独立上下文的审核。实施者自审、预登记 agent_spawn、自由 PASS 文件均不构成审核通过。审核失败、超时或缺能力时不能以 degraded 或 verification_pending 完成。

Lite 复用 [宿主适配](host_adapters.md) 和 [子代理等待契约](subagent_spawn_wait.md) 的独立审查。主代理先用 `icode_control.py lite-review` 登记 spawn，再用当前宿主的原生工具启动一个全新的审核子代理。任务仅授权读取，宿主支持时同时限制工具权限。使用已有的模型选择，避免传入实施会话历史、主代理裁决或把旧子代理续接成新审核。

给子代理传 `review_input` 中的执行根、候选标识、基线、工单文件位置和审核任务。原始需求、方案、AC、验证记录及其 evidence 从 metadata 按字段查阅（紧凑存储格式读取 `value` 内同名字段）。只读实际 Git 差异和涉及的调用方，按需打开证据原件。对照事件链中已签收的审核报告定位尚未关闭的问题。初始输入不展开整仓 manifest、历史候选、整份 metadata 或事件链。

旧审核报告路径取自成功的 `lite_review_returned.payload.report_path`；宿主子代理审核路径取自 `agent_result.payload.lite_review_report.report_path`，以同一 `spawn_id` 关联登记。审查材料是证据，只执行宿主和本审核任务允许的操作。

拿到宿主返回的真实子代理 ID 和结果后，原样签收报告。以下 `response.json` 是工具实际返回的 JSON，也可用 `--lite-review-report -` 通过 stdin 提交。签收分两层核验：

- **绑定层**：`--host-receipt` 必须回传与本次登记绑定、含 `host_receipt_challenge`（`lite-review` 返回在 `review_input.host_receipt_challenge`）的宿主启动/返回回执，控制面校验 `challenge`/`spawn_id`/`host_session_id` 一致且 challenge 未复用。
- **来源层（必需）**：调用方自拼的回执不构成可信证据，也不能自选核验程序。适配器只能由**宿主侧配置**决定，路径固定为 `<宿主真实主目录>/.icode/host_adapter.json`，内容为 `{"command": "<绝对路径>", "sha256": "<适配器摘要>"}`。控制面**不接受任何环境变量重定向**（如 `ICODE_HOST_CONFIG`）：能设置环境变量的调用方同样能指向自建配置和自建程序，"摘要与配置一致"会退化为"摘要与调用方给的配置一致"，从而绕过来源信任。**主目录同样不能取自 `HOME`**：`HOME` 是进程环境值，调用方设置它就能换掉整个信任根。控制面按平台取真实主目录——macOS/Linux 用 `pwd.getpwuid`；Windows 用 `SHGetKnownFolderPath(FOLDERID_Profile)`，由 Shell 按当前登录令牌的 SID 解析，不读 `USERPROFILE`/`HOME`，也天然处理 `%SystemDrive%` 之类可展开形式；取不到即 fail-closed，绝不回退到环境变量。**`ICODE_CONTROL_TEST_MODE`/`ICODE_TEST_HOST_ROOT` 之类的测试开关也不是信任根入口**：测试只能在同进程内给 `_HOST_CONFIG_ROOT_OVERRIDE` 赋值（例如 `mock.patch.object`），生产 CLI 子进程拿不到该入口。控制面还要求主目录、信任根、配置文件、适配器及其父目录链均非符号链接且不可被其他用户写入——POSIX 读 `st_mode` 的组/其他可写位，Windows 读实际 NTFS DACL 并逐 ACE 判断写类权限（`st_mode` 在 Windows 只反映只读属性，不能作为 ACL 证据）；适配器不得位于调用方工作区或临时目录；任一不满足即 `lite_host_adapter_unavailable`。`lite_code_in_progress` 时把已确认的适配器摘要冻结进工单 `extensions.lite.host_adapter`（该字段受 `metadata-update` 专用写入保护，只能由受控阶段转换改写）。**签收在启动适配器之前先比对冻结摘要，并且只执行刚刚校验过的那份字节**（先落私有临时副本再执行，执行后再次核对磁盘；暂存副本保留脚本后缀以便 Windows `CreateProcess` 启动，并在暂存目录中把适配器原目录的兄弟条目链接进来，使依赖同目录模块/配置的适配器仍按原相对位置解析，不要求适配器自包含；Windows 下先清除只读位再整目录删除，避免遗留文件）：身份不符时未受信任的程序根本不会运行——事后拒绝签收无法撤销它已造成的副作用。随后把 `challenge`/`spawn_id`/`host_session_id`/`report_sha256`（提交原文摘要）/`stored_report_sha256`（落盘报告摘要）交给适配器，适配器须查询实际宿主（如会话注册表）后回传 `{"launched": true}` 及完全一致的四元组回显。**未配置、摘要不符、身份被替换或适配器未确认时，joined 结果一律拒绝，不能进入交付。**

> 适配器是宿主中立边界：ICODE 不规定适配器如何查询宿主，只要求它能在本地确证“这次 spawn/返回确实存在且对应本次登记与报告摘要”。适配器选择与授权归宿主。**降级语义与其他模式一致**：宿主未安装配置时，lite 的规划与实施照常推进，但会把 `extensions.lite.host_adapter.status="unavailable"` 与原因记入工单，交付结论固定为 `verification_pending`；`verified` 交付仍必须通过可信适配器签收，不予降级。即"能干活但拿不到 verified"，而不是整条流程停摆。降级下的审核回执记为独立态 `outcome=degraded`（不是 `success`）：报告照常留档追溯，但**不具备关闭历史阻断问题的资格**，也不能作为任何 `verified` 依据；工单仍须如实记录 `host_attestation_degraded`，与表示"已获可信确认"的 `host_attestation` 互斥。实施前用 `lite-preflight` 预检可在开工前就知道当前是否具备 verified 交付能力。安装配置后重新进入 Code 会重新确认并冻结适配器身份，即可正常签收。

```bash
python3 tools/icode_control.py record-agent-result --dir <out> --spawn-id <spawn-id> \
  --result joined --adopted yes --adoption-reason '已核对宿主实际返回' \
  --evidence-ref <宿主工具调用回执引用> --summary '独立审核已返回' \
  --host-session-id <真实子代理ID> --lite-review-report response.json \
  --host-receipt host-receipt.json --request-id <result-request>
```

回执复用 `agent_spawned` / `agent_result`，绑定当前 attempt、候选、语义合同及不可变审核报告摘要。**采纳门（P2）**：只有 `result=joined` 且 `adopted=yes` + 可信宿主确认的报告才计为可采信成功回执；`adopted=no|partial` 的 joined 报告只保留追溯，`lite_review_records` 将其标为 `failure`，不能作为通过依据。**复审门（P2）**：阻断问题的“登记”与“关闭”分开判定——任何实际返回且通过校验的报告（**包括 `adopted=no/partial`**）中的阻断问题都进入复审队列；但**只有可采信报告**（joined + adopted=yes + 经宿主确认）才能以同一 ID + `status=resolved` + 证据关闭历史问题，未采纳报告不能关闭任何既有阻断项。旧版先写报告后记 schema 失败的脏回执（如 `findings: null`）不纳入复审。子代理身份和返回值取自宿主工具，主代理负责如实登记，控制面校验绑定、重复身份和报告完整性。通用 event、metadata-update、普通 Agent 结果或未登记的报告都不能替代 Lite 审核回执。历史 CLI 回执依原规则读取。

宿主启动失败、超时或停止时，核对实际状态后用已有 `record-agent-result` 记录 `failed|timed_out|stopped`、`adopted=no` 和简短证据引用，无须传报告。候选漂移或无效报告被拒收后，如实终结 Agent 及 step attempt，保留原因；审核设施失败且代码与合同未变时可直接新建审核 attempt。宿主工具调用结果未知时先查原句柄，不能重复启动。

审核者只读原始需求、有效增量、关键决策来源、当前方案与 AC、完整改动和必要上下游、已有用户改动、真实测试原始证据。必须检查需求与方案是否正确、实现及关联行为是否完整、证据是否证明最终候选；不能只接受实施摘要。

报告为 JSON：

```json
{
  "verdict": "pass",
  "findings": [],
  "criteria": [{"id": "AC-1", "status": "pass", "evidence": "源码与实际报告引用"}],
  "requirement_assessment": "逐项原始目标及方案复核结论",
  "implementation_assessment": "实际代码、边界、异常与回归结论"
}
```

verdict 允许 pass/changes_required/blocked。finding 包含 id、blocking、status、description、evidence；状态 open→fixed_pending_review→resolved，实施者仅提交整改，关闭须独立复审。AC 未执行且仅缺外部条件用 pending_external，不能伪写 pass。控制器填入 schema_version/attempt/candidate_id/contract_digest，绑定真实返回。

发现代码缺陷：旧 review attempt 以 failure 终结 → transition 回 lite_code_in_progress → 修复、重测、新 code 回执 → 新 review attempt 与全新 reviewer。需求/核心方案错误则回 Prepare。可聚焦修复差异与未关闭问题，但 reviewer 始终可访问完整改动；不以轮数上限替代通过。

审核后相关源码、测试、配置、未跟踪文件或语义合同变化，旧结果失效。审核调用中断先核对 trace，不能重放同一 attempt；迟到旧结果只留历史，不能签收当前版本。纯补证同版重验见 lite.md，不新增虚假的审核调用。

用户在审核后将已审核源码原样作普通线性提交，可复用既有严格候选等价证明，报告和调用仍保留原始候选ID；源码变化、amend/rebase/merge等未证明的历史变化不能复用。若提交发生在step start后、实际调用前，先终结旧attempt并重新start，防止调用身份与冻结输入不一致。candidate inspect、交付报告和提交检查均须重新核验G2合同及不可变报告，不能只接受历史步骤收据。

主报告 `lite-report` 读取审核正文前复用同一身份、当前候选/合同/attempt、回退边界和报告hash/schema校验。合法的changes_required/blocked结论仍可展示，但不能因此取得交付通过资格；pass仍须满足完整G2。尚无审核时可生成规划报告；已有审核失效则命令非零且不覆盖旧报告，旧文件仅为历史，不能视为本次重新认证。

当前审核和历史finding都以单次读取的字节计算hash并解析JSON。主报告直接渲染G2返回的已校验对象，不再重新打开审核文件；校验后发生外部文件替换不会把未签收正文混入本次报告。再次调用仍会重新校验磁盘文件，不缓存通过资格。
