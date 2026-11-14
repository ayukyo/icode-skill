# lite — 原生三阶段开发

**入口**：`/icode lite <需求>` 新建；`/icode lite` 续接当前绑定；`/icode lite --ticket <id>` 显式解析。仅显式启用，不将 fast 或 full 工单静默转换。

**主报告**：lite.md；机器真源仍是 metadata + 事件 + 实际测试证据和 lite_review_<invocation>.json。不生成旧六份占位报告。阶段为 lite_prepare、lite_code、lite_review；不是旧六步的别名。

## 入口与能力预检

读取 references/core.md，按 project_intake.md 固定工程根。保留用户范围、停止点和已有修改；不强制 worktree。恢复时明确绑定工单，读取 metadata 与 trace；缺参数且不能唯一绑定才询问，不按最大序号替换当前绑定。

实施前确认能使用本机 codex/claude CLI 创建全新只读审核上下文并读取目标仓库；实际审核通过 lite-review 受控调用。缺能力时明确阻塞，不能先改完再自审。能力咨询和只读预检不触发登录、安装或模型调用。受保护 provider 未实现的路径明确拒绝，不能退回原始 Git 绕过。

首版不支持已启用阶段计划的工程：出生、步骤动作及事件写入均明确拒绝，继续使用既有阶段流程。不能将 lite 实施映射成中性 review 来绕过阶段授权。

新建按 max(N)+1 分配空目录，第一项写入为：

```bash
python3 tools/icode_control.py create --dir <out> --ticket-id <id> \
  --birth lite --requirement '<用户原始需求>' \
  --metadata-json '{"mode":"lite","workflow_version":1}' --request-id <request>
```

不得将原需求拼进 shell；宿主调用应使用参数数组。lite 出生不复用入口态旧 full 工单，可将已有 init 作为明确来源。

## Prepare：充分理解与规划

只在此阶段加载 references/requirement_convergence.md，调查相关代码及约束，形成其规定的单源合同。原生状态为 lite_prepare_in_progress → lite_prepare_done。

按既有执行协议调用 step --step lite_prepare --phase start，取得真实 attempt；before_write 检查后调查并写合同，before_transition 检查、finish success，再 transition --to lite_prepare_done。G1 对需求来源、业务决策、具体方案、AC、验证映射及风险检查作结构校验；主代理另做语义充分性复核。

目标、业务决策或方案未清楚不能实施。stop_after=prepare 时完成本阶段即停；已明确授权完整 lite 时自动继续，不逐阶段请求批准。

## Code：围绕验收实施与测试

transition --to lite_code_in_progress，再 start lite_code。复用已明确合同，不重新解释需求。写入前 check before_write；bug 优先取得有效复现 RED，再修根因。小功能验证正常路径及实际异常/边界；跨组件、异步、缓存、恢复等只扩展相应检查，不自动 full。

使用实际代码和测试，记录 code_files，保留用户已有工作。验证使用现有 record-verification，绑定当前完整 candidate-id、environment、layer、consumer、scenario、baseline 和实际 evidence；自动化行为测试优先 --test-report <本次实际 JUnit XML>，不可自报计数。零测试/全跳过不能 pass，只有编译不满足行为 AC。

不能为了通过而缩减 AC。纯技术局部调整说明理由；业务行为、核心方案或 AC 变化回 Prepare。已知本地必需检查失败先修复，只缺具体外部环境时保留 external pending，但仍执行独立代码审核。

所有必需本地检查通过后 before_transition check → finish success → transition --to lite_code_done。不接受 degraded 完成必需步骤。

## Review：独立审核与验收

本阶段加载 references/lite_review.md。transition --to lite_review_in_progress；start lite_review 冻结候选和合同。before_write 检查后实际执行：

```bash
python3 tools/icode_control.py lite-review --dir <out> --attempt <review-attempt> \
  --backend codex --request-id <unique-request>
```

Claude 使用 --backend claude；--model 仅传用户已选择的模型。等待返回后 check after_wait；逐条处理实际报告。pass 且无阻断 finding 才满足 G2。否则按 lite_review.md 回退修复并新上下文复审，不能冒充审核结束。

最终候选审核有效且全部本地必需证据通过：before_transition check → finish success；控制面按 G3 只允许两种结论：

- 全部必需 AC 当前有效证据通过：transition --to completed --delivery-verdict verified。
- 仅缺声明的外部执行条件：transition --to completed --delivery-verdict verification_pending，展示“代码已审核，外部验证待完成”，列环境、动作、预期信号。

审核缺失/失败、有已知缺陷、必需检查失败均不能 completed。不得用 blocked/not_applicable/pending 包装这些情形。

## 回退、恢复与补证

恢复先读取 trace 和实际 candidate，核对开放 step/operation/reviewer。副作用结果未知先核验，不盲重放。回退前终结旧执行，再 transition 到 lite_prepare_in_progress 或 lite_code_in_progress；控制面撤销相应下游完成投影与旧交付结论，保留历史。

已完成后代码或合同改变，应从相应阶段重入。代码与语义合同完全未变、只补外部证据时，record-verification 仅记事实；随后显式 transition --to completed --delivery-verdict verified 受控重验 G2/G3，复用仍有效审核，不新造 Code/Review。新失败不能退回旧 pass，也不能继续呈现历史 verified。

主报告由 lite-report 派生，不另起总结代理；索引、归档和状态展示按 mode/version 解析三步合同。只在实际风险触发时加载串口/严格证据/阶段计划/生命周期等原合同，lite 不豁免安全边界。
