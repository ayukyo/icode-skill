# 共享 SKILL 路由契约

> 机器真源：`mcp/workflow-gate/skill-routes.json`；发布真源：`skill-packs/manifest.json`。ICODE 只判断触发、传递输入、消费输出，不复制独立技能正文。

## 路由流程

1. 当前步骤开始时读取路由表，只评估 `steps` 含当前步骤的条目。
2. 任一 `triggers` 与现有事实匹配才读取对应共享 SKILL；无匹配不加载。
3. 调用前准备 `input_contract` 要求的证据。输入不足时先补证，无法补齐则执行 `fallback` 并诚实降级。
4. 技能输出必须覆盖 `output_contract`，主代理重新核对决定性证据后才能写入工单结论。
5. 记录技能命中、结果和采纳情况；技能建议不自动扩大需求范围，也不替代用户语义决策。

嵌入式、摄像头或技术文档/原理图/MCU 分析仍使用既有步骤入口，不增加公开命令。命中运行身份、camera pipeline、性能稳定性、IQ/标定、异构文档接入、跨文档规格冲突、原理图接口或 MCU pin/AF/clock/register/IRQ/DMA 事实时，按路由表加载相应技能；只提供普通软件事实时不加载这些领域技能。

技术文档能力按层组合且不可越权：`technical-document-intake` 先用 `tools/document_intake.py` 固定 magic/container、结构、hash、归档风险、重复/变体、可读覆盖和受保护边界；视觉区域再按 [media_routing.md](media_routing.md) 调用 `tools/media_router.py` 选择 native/bridge/dual/text_only 并记录页/裁剪/DPI/模型来源；`hardware-spec-contract-audit` 按字段/变体/修订/状态对齐 PRD、datasheet、原理图、代码、测试与现场值；`schematic-interface-audit` 审计页/网表与电气链；`mcu-hardware-software-contract-audit` 继续追踪 package pin→AF/remap→clock/reset→register/SDK→IRQ/DMA→build/programmed image→runtime。接入成功只证明资料可用于后续分析，不证明规格一致、电气连通或板上运行正确。

## 路由项契约

每项必须且只能包含：

- `skill`：`skill-packs/manifest.json` 中已发布的技能名。
- `triggers`：可由当前上下文实判的症状或场景，至少一项。
- `steps`：适用的 ICODE 步骤名，至少一项。
- `input_contract`：调用前必须具备的输入证据，至少一项。
- `output_contract`：主流程允许消费的结构化结果，至少一项。
- `fallback`：技能不可发现、不可读取或输入不足时的明确降级动作。

## 边界

- 共享技能只写宿主无关的方法论和输出合同；工具差异见 `references/host_adapters.md`。
- 共享技能不得硬编码 `mcp__*`、`TaskOutput`、`functions.exec`、`collaboration.*` 等宿主调用语法。
- 领域窄技能仍保持独立；组合技能只做识别、编排和边界收敛，不复制窄技能全文。
- 路由失败不能阻塞主代理继续用 Read/rg/命令行完成事实核查，但必须标记未加载原因。

## 运行观测与候选提炼

每次实际加载或诚实降级后，用 `record-skill-run` 把 skill、trigger、result、adopted、evidence_refs、elapsed_ms、estimated_tokens、unique_findings 和 agent_id 记录到 `extensions.skills.runs`。记录只衡量路由效果，不自动改变工单 verdict。

`tools/propose_skill_candidates.py --root <workspace>` 只读聚合未路由或持续 degraded 的重复模式；默认同一 trigger 至少出现 3 次才输出候选。候选报告不是 SKILL，也不自动写文件、发布或修改 manifest。
