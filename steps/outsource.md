# 步骤 outsource — 同机 AI 外包协作

**命令**：`/icode outsource [自然语言或绝对 request.md 路径]`；也可独立调用 `icode-outsource` 技能。
**定位**：独立生命周期，不进入 ICODE 主工单 metadata、index、status、completed_steps、Runtime/UI 动作列表。
**真源**：[outsource_contract.md](../references/outsource_contract.md)。开始前完整读取该合同；所有动作使用当前实际技能根的 `tools/icode_outsource.py`，不把 cwd 或安装目录当成目标工程。

## 1. 识别意图与身份

- 只有能力咨询时只解释，不初始化 board、不发布。
- 发起会话选择稳定 `requester`，保留 session 与可选 `parent_ref`（仅关联原工单，不回写原工单）；同会话恢复不得换一个新 requester 隐藏旧单。
- 接单会话绑定用户转交的绝对入口；所有执行变更保存工具返回的 actor/session/attempt/revision。
- “接入外包结果”按 requester/parent_ref 查询并显示真实待接入交付，不要求人记 ID。无绑定则只读定位候选，无法唯一确定才询问。

以下命令中的路径及身份由 AI 从已核实上下文填写。统一 `--board <root>` 位于子命令前，可省略；绝对 request.md 会定位所属 board，显式 board 与入口冲突即拒绝。JSON 先保存为 UTF-8 文件，通过参数传入，不把正文拼接到 shell。

## 2. 发起与发布

阅读目标及调用链，整理 schema_version=1 合同。`read_paths` 覆盖依赖和构建输入，`write_paths` 落在 read_paths 内；只读工程使用空 write_paths。写合同之前确认目标真实、已有实现和影响范围；未知范围不能靠发布越界解决。

```bash
python3 tools/icode_outsource.py --board <root> init
python3 tools/icode_outsource.py --board <root> publish --contract <contract.json> --session <requester_session> --request-id <unique_request>
```

首次发布才显式初始化。保存工具返回的绝对入口和发起身份；人类交接只输出“把这份入口转交给接单 AI：`<绝对 request.md>`”。发布包含当前未提交内容快照，不能让接单端直接在原 checkout 工作。

## 3. 精确认领与执行

```bash
python3 tools/icode_outsource.py show <绝对request.md>
python3 tools/icode_outsource.py claim <绝对request.md> --actor <worker> --session <worker_session> --request-id <unique_request>
python3 tools/icode_outsource.py prepare <绝对request.md> --actor <worker> --session <worker_session> --attempt <returned_attempt> --destination <全新绝对目录> --request-id <unique_request>
```

保存 prepare 返回的 bindings（每项 source_root/execution_root）。只在已绑定执行根工作，构建输出另放独立构建目录，避免污染提交范围；执行前由宿主约束实际写范围。依赖不足通过 message 请求补充，不能回源偷读/修改。发起端在资源均未占用时先 register-resource 登记物理键及 aliases；接单端按登记键 acquire，需要补登记时 message 发起端，不冒用发起身份。请求全部资源失败就不执行任何依赖它们的动作。

执行期间按需要 heartbeat、inbox/message；message 的 JSON payload 结构以当前 CLI/合同为准。收到消息后读取并处理，再 ack 到实际已投递事件；不能把收到消息当处理完毕。旧 attempt、基线漂移、资源冲突或权限拒绝时停止依赖动作，保留证据。

## 4. 冻结交付

```bash
python3 tools/icode_outsource.py submit <绝对request.md> --actor <worker> --session <worker_session> --attempt <returned_attempt> --evidence <evidence.json> --request-id <unique_request>
```

提交前检查全部新增/删除/修改文件、每项验收及执行证据，工具核验范围和摘要。记录完成、失败、未执行和现场边界；禁止把离线测试写成设备/浏览器通过。资源操作确已结束才 release，保留说明；未停止不得释放后提交。报告交付入口，不要求人另传 ID。

## 5. 发起端接入和验收

```bash
python3 tools/icode_outsource.py list --requester <saved_requester> --parent-ref <saved_parent_ref>
python3 tools/icode_outsource.py show <resolved_request.md>
python3 tools/icode_outsource.py integrate <resolved_request.md> --actor <saved_requester> --session <requester_session> --request-id <unique_request>
python3 tools/icode_outsource.py accept <resolved_request.md> --actor <saved_requester> --session <requester_session> --checks <checks.json> --request-id <unique_request>
```

无 parent_ref 时省略该参数。先核对真实交付及授权，再 integrate；目标基线与旧值漂移时拒绝覆盖，报告差异。接入中断按工具日志恢复，仅允许匹配旧值或交付新值的条目续跑；第三种内容必须停止。多工程逐项记录，部分接入不算整体完成。integrate 已记录接入结果时不重复 finish；人工恢复需要 finish 时按其 payload 合同绑定真实接入回执，不能用“已完成”字符串代替。接入后执行实际验收验证，再 accept。

accept 必须逐项提供合同 acceptance.id 的 pass 与 evidence；有代码交付须已有真实完成接入及验证记录，无代码交付仍须逐项证据。失败走 rework，携带当前 revision、原因及所评交付版本，保留旧交付和 attempt 历史，不重写已冻结合同。rework 返回 running 且保留绑定 workspace/执行身份时，在原绑定目录继续修正，不重新 prepare、不覆盖目录；以当前回执中的 attempt/revision 继续提交。

已接入后的 rework 会先核验上一接入的真实目标，再冻结当前源码为新基线，返回 claimed 和递增 attempt。接单端据新回执 prepare 全新执行目录后继续修复；旧目录、交付、接入和验证记录保留作历史，不能复用旧验证结果。此过程不回滚已接入源码；源已发生额外漂移时停止并报告。

## 6. 取消、转交、监测与交付报告

cancel 是请求取消与确认已停止的双阶段动作，handoff 要明确已停止且资源已释放，不能将超时/失联当确认。所有变更带稳定 request-id，重试相同调用复用该 ID，不用新 ID 重复副作用。通用变更通过 `--payload <json>` 传数据。

发起端换会话时，先读取 show，核实旧发起端已停止，再用 resume 更新 session 绑定，payload 为 `{"previous_session":"原session","stopped":true,"reason":"实际停止核验说明"}`。普通资源占用未结束时禁止转交；部分接入仅转移接入占用，按同一交付对应的 journal 恢复，不能释放后重做。旧会话失去继续操作资格。

watch 是带 requester/after/timeout 的有界只读轮询，最长 60 秒，不承诺后台唤醒；消费者处理后才显式 ack。暂停后保持暂停，不自动恢复。doctor、list、show、inbox、watch 不初始化或修复缺失 board。

最终报告入口绝对路径、真实状态、交付与接入结果、验收证据、未执行边界及下一步；任何状态均不得冒充 ICODE 主工单完成。
