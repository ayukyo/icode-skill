# 证据与验证默认习惯

本文件是 log / plan / deepcheck / audit / verify 共用真源。各步骤只保留触发点，不复制规则正文。

## 1. 现场事实优先

- 当前源码、历史提交、现场运行包、修复验证包是不同对象；先建立关系，再用代码解释日志。
- 设备用 SN 或物理 MAC 绑定；IP、hostname、虚拟 MAC 仅作检索线索。
- 固定日期、时区、精确时间窗、原始证据和转换过程。过滤结果不能冒充完整日志。
- 多 Git 根逐仓记录 runtime / analysis / verification commit、dirty、manifest pin、build root 和产物身份；父仓 clean 不代表子仓一致。
- 现场事实优先于旧结论和“应该如此”的口述；文档与当前 HEAD 只能提供候选解释，不能自动成为现场事实。

未知根因、旧日志、多仓或设备证据场景按 [skill_routing.md](skill_routing.md) 加载 `bug-investigation-baseline-checklist` 或 `multi-repo-artifact-provenance`。

## 2. 证据分类与主代理复核

- 始终区分 `fact`、`inference`、`refuted`、`unobserved`，每条都写 source 和 boundary。
- 事实只能覆盖直接观测范围：日志的两个时间戳可支持记录间隔，不能独自证明超时阈值、计时起点或调度按期；缺少源码/调用链时，只能说关联尚未核实，不能说“无关联”。全局免责声明不能抵消某一句把推断写成事实，提交结论前逐句复核这些断言。
- 决定性事实或反驳用 `record-claim` 写入控制面；fact/refuted 必须附 evidence。
- 子代理和便宜模型可找候选、压缩或质疑，但主代理必须重新核对决定性证据，才能发布根因、修复充分性或 verified 结论。
- 冲突证据不取平均：回到原始来源、身份、时间窗和运行版本，保留尚未解决的竞争解释。

## 3. 无日志反查上游

下游无日志先标 `unobserved`，从最后正证据节点反查上游 cache、相等去重、owner/latch、状态 gate、任务投影、路由注册、异步发送和版本契约。只有上游已证明送达、下游观测点已启用且窗口完整时，才把无日志提升为下游异常证据。

声明、可达、受理、送达、持久化、消费、用户可见和物理行为逐层分开；送达不等于消费，设置预览不等于运行视图，运行视图不等于物理行为。

## 4. 诊断、实现、验证分层

- 诊断：回答“现有证据支持什么根因或断点”，不把候选修复冒充已实现。
- 实现：回答“哪些代码和合同已改变”，不把编译或静态检查冒充现场效果。
- 验证：按用户验收合同逐层记录 environment、baseline、consumer、scenario、evidence 和 outcome。
- 部署/交付/消费/UI/physical 是独立层。App 关闭状态、有效消费者和物理行为只有被明确要求时才成为 required，不给纯 host 任务强加真机门禁。
- `verification_contract.required=true` 时，用 `record-verification --layer --consumer --scenario --baseline` 填满必需矩阵；任一必需单元缺失或最新结果非 pass，不得 `delivery_verdict=verified`。
- 嵌入式/摄像头项目可声明 `profile`、稀疏 `required_cells`、baseline `sha256:` 摘要与 `required_metrics`。每个 embedded/camera 单元都必须以 `--profile --baseline-ref` 绑定同一份合同摘要；有量化指标时再附 `--metrics-json`。profile/摘要不符、指标缺失或未达阈值都必须阻断 verified；缺 `required_cells` 的旧合同仍按三维笛卡尔积，未声明 profile 的旧 generic 合同保持原行为。
- 启用候选的运行显式绑定 `candidate_id` 和完整源码快照；先核对当前候选是否等价及 environment 是否有资格，再取 cell 的最新运行。旧候选 pass 可追加为历史，但不能覆盖当前候选 fail。physical cell 只接受 `environment=physical`，模拟故障注入必须记 simulated，不能冒充旧驱动的物理实测。
- 新建工单或活动工单明确新增 `risk_profile.risk_flags.real_env_verification=true` / triggers 风险且缺合同，会自动登记 `required=true,requirements_pending=true`，三个 required 数组为空。full/fast/override 都不能解除待细化债务；先明确具体 physical 场景与设备证据，再解除 `requirements_pending`。查询不补写合同或迁移旧工单。
- 验证复用必须给 `reuse={origin_run_id,reason,scope_paths,scope_hash,comparison_ref}`。来源必须是真实同 cell 的成功事件，设备、窗口、环境、指标、制品及直接测试身份保持一致；机器比较只允许 `.md/.rst` 文档差异，验证 scope 的实际哈希必须未变。源码、配置、构建脚本和依赖变化不能用一句“无关”豁免。`supersedes_run_id` 只引用已存在的同 cell 记录，永不修改旧 run。
- build/deploy/runtime provenance 分别记录 `source_candidate_id`、`artifacts[{path,sha256}]` 与 `evidence_refs`。fresh build 还引用开始前真实目录回执、configure/build argv、compiler、architecture；部署和运行回指真实 build run 核对完整 SHA256 清单，构建路径、部署副本路径和设备加载路径可以不同。本地文件实际读取并核对哈希；runtime 的远端哈希只是对应证据的观测值，控制面不会读取设备或自动证明实机验证。
- fresh 本地产物的 ctime 必须不早于开始回执；可复现构建允许归一化 mtime，但核验前后 inode、大小、mtime、ctime 必须稳定。同一个 run 的所有本地 build/deploy 制品读取共享 `candidate_tracking.limits` 的 `max_files/max_bytes/timeout_seconds`；超限拒绝追加，可通过既有 candidate capture 显式提高预算。
- 直接执行测试分别记录 `target_built`、`binary_executed`、`test_runner_discovered`、`ci_registered` 和实际 `binary_exit_code`。直接执行通过但未发现 runner/未登记 CI 就按这些字段如实报告；runner-only pass 不能替代同 cell 当前直接执行的失败。

真实 USB/驱动设备证据可使用下列身份模板；仅在相关 physical 场景要求，不给普通 host 任务强加这些字段：

```json
{"device":{"vid":"<实际VID>","pid":"<实际PID>","sn":"<实际SN>","node":"<实际设备节点>"},
 "stream_role":"<本次depth/rgb/ir等实际流角色>",
 "driver":{"name":"<实际模块或驱动标识>","version":"<已观测版本>","artifact_sha256":"<驱动制品哈希或明确未观测>"},
 "sdk":{"source_candidate_id":"<SDK源码候选ID>","artifact_sha256":"<实际SDK制品SHA256>"},
 "test_process":{"pid":"<实际进程ID>","argv":["<实际测试程序>","<经脱敏的参数>"]},
 "runtime":{"kernel":"<实际uname版本>","boot_id":"<本轮boot id>","artifact_sha256":"<已观测运行制品SHA256>"},
 "window":{"timezone":"Asia/Shanghai","start":"<精确起点>","end":"<精确终点>"},
 "refs":{"active_build_run_id":"<当前构建事件>","active_evidence_ref":"<部署/加载观测>",
         "fallback_build_run_id":"<回退构建事件或未配置>","fallback_evidence_ref":"<回退观测或未观测>"},
 "evidence_refs":["<原始设备日志/版本观测证据>"]}
```

这些是相关设备场景的建议字段，不是普通 host 工单的新必填 schema。没有读取到的身份、哈希或回退状态写“未观测”，不能用源码版本替代运行观测；argv 不放口令或凭据。

旧驱动兼容或故障路径可复用以下注入模板。每个用例绑定当前 `candidate_id`，记录 `environment=simulated`、注入点、输入/返回值、调用顺序、预期与实际状态及原始 `evidence_refs`；`outcome` 只描述本次模拟断言。模拟身份写 mock 标识，不填成真实 SN。这些记录可满足明确要求模拟验证的 cell，不能满足 physical cell。

| 场景 | 可复用的 simulated 证据模板 | 仍须实机观测的边界 |
|---|---|---|
| 节点缺失 | stub/open 对指定 mock 节点返回约定缺失错误，记录实际 errno、初始化结果和后续调用是否被正确终止。 | 不能证明真实节点枚举、权限、热插拔或错误来源。 |
| ioctl 不支持 | mock 驱动对指定 request 返回不支持错误，记录 request、返回值及 fallback/报错分支。 | 不能证明旧驱动的 ABI、真实 ioctl 支持或回退路径在该设备可用。 |
| 首次成功后读取失败 | 固定第一次读取成功、第二次失败的返回序列，记录数据、状态、错误传播及是否继续暴露旧成功值。 | 不能证明实际驱动读取时序、链路故障或设备恢复行为。 |
| 旧序号或旧时间 | 注入低于已接受序号的帧或超过合同窗口的时间戳，记录基线、输入、阈值和接受/拒绝结果。 | 不能证明设备时间域、真实乱序、帧龄或同步精度。 |
| stop/start 缓存复位 | mock 一轮成功后 stop，再 start 新轮；记录轮次、缓存状态、首帧和未更新字段是否仍被读取。 | 不能证明真实 stop/start 清除了驱动队列、硬件状态或跨进程残留。 |
| 多设备并存 | 两个 mock 身份交错返回不同数据/错误，记录实例、流角色、调用与输出归属，断言状态不会串到另一实例。 | 不能证明真实双设备枚举、USB 带宽、调度、驱动并发或物理流身份。 |

## 5. 步骤触发

| 步骤 | 必做动作 |
|---|---|
| log | 先固定设备、时间、runtime/analysis 基线和原始证据，再提出根因。 |
| plan | 把证据缺口、完整功能链、状态生命周期和验收层写成合同。 |
| deepcheck | 从消费者和失败路径逆推，检查无日志上游 gate、跨轮残留及未观测边界。 |
| audit | 主代理复核决定性证据，核对 claim ledger 与实际代码/验证记录。 |
| verify | 绑定验证 baseline，逐 layer/consumer/scenario 记录，不自动升级交付结论。 |

## 6. 最小对外表达

结论同时给出：已证事实、推断、未观测边界、运行/分析/验证版本关系、允许结论、禁止越界表述、下一项最小判别动作。不要用“已修复”覆盖“已实现未验证”，也不要用局部 subset success 覆盖完整现场验收。
