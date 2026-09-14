# /icode ui — 本地 ICODE 工作台

**定位**：启动 ICODE Agent Runtime v0.4 的本地 Web UI。它是全局工单管理器和受控步骤入口，不参与步骤 0~6 的状态推进，也不创建第二套工作流引擎。

**风险等级**：L1（启动本机长驻进程；不直接写工单）。

## 1. 默认启动

在 ICODE 技能根执行：

```bash
python3 tools/icode_agent.py ui
```

默认监听 `127.0.0.1:8765` 并打开浏览器。8765 被占用时自动选择另一个空闲 loopback 端口，终端会打印实际 URL。无参数重复调用 `/icode ui` 时，通过私有实例登记、启动锁和带 `instance_id` 的健康回执复用现有全局实例，不再启动第二个随机端口。登记过期或进程异常退出时，健康探测失败，下一次启动会安全覆盖过期登记。UI 进程随当前终端任务结束而停止，不安装系统服务、不开放远程监听。

Codex 使用可持续读取输出的终端会话运行；Claude Code 使用 Bash 工具的后台运行能力。启动命令返回 URL 后立即报告给用户，不等待服务器自然退出。若自动打开浏览器失败，保留服务并把 URL 明确交给用户。

## 2. 高级参数

```bash
python3 tools/icode_agent.py ui --no-browser
python3 tools/icode_agent.py ui --ticket <ticket_id>
python3 tools/icode_agent.py ui --project <project_id>
python3 tools/icode_agent.py ui --dir <ticket_dir> --port 9010
```

- `--dir`：兼容 v0.2 的固定单工单模式；旧 `/api/v1/status` 与 `/api/v1/run` 语义保持不变。
- `--ticket` / `--project`：只指定初始选中项，不把浏览器输入当文件路径。
- `--port`：显式端口。显式端口被占用时直接报错；`--port 0` 仍表示由系统选择空闲端口。
- `--no-browser`：只启动服务并打印 URL。

无参数全局模式才启用单实例复用；`--dir`、`--ticket`、`--project` 或显式 `--port` 保留高级精确语义，不会暗中复用上下文不同的实例。

## 3. UI 操作合同

1. 首页从全局索引只读加载项目和工单；legacy/歧义/损坏工单可见但不可执行。
2. 手动“刷新”重新读取目录册、所选工单事件链/动作策略、主机能力和任务状态。刷新失败后所有写动作禁用，不能用旧视图继续执行。
3. 推荐下一步和允许动作必须来自 `icode_control.py action-policy`；前端不得自行推断状态机。
4. 执行时浏览器只提交 `ticket_id + action + note + request_id + expected_revision`。服务端重新解析工单并比较 revision；漂移返回冲突，不启动 Agent。
5. Host 进程启动前后必须分别形成 `record-agent-spawn` / `record-agent-result` 回执；`exclusive_key=ui-step-run` 防止双 UI 对同一工单并发重放。
6. 自动主机优先 Codex，不存在时才回退 Claude Code；进程已尝试启动或结果不明确后绝不切换主机重放。
7. “新建工单”只接收不透明 project ID 与需求文本，服务端解析可信工程根并调用 `create-next`；先出生、入索引，再由用户显式运行 `plan`，不得在项目级盲启模型。
8. 自动刷新频率是非秘密设置，默认 30 秒、允许 5~300 秒；保存后立即重排下一次刷新。手动刷新不受该间隔限制，前端不得对活动任务偷偷切换为更高频率。
9. 执行进度通过 `/api/v1/job-events` 的单调游标增量读取；浏览器约 1.2 秒拉取一次短事件，30 秒全量刷新仍是最终一致性兜底。事件只含脱敏短消息，不下发 prompt、执行路径或未登记 JSON 字段。
10. Runtime 仅把任务身份、状态与短摘要原子写入 `~/.claude/icode_data/ui_jobs.json`，不持久化原始输出。重启时未终结任务统一恢复为 `outcome_unknown`，必须人工核实且禁止自动重放。
11. 工单驾驶舱只投影控制面 metadata、`ticket_snapshot.json`、验证记录与白名单产物名；步骤进度、交付结论和验证债务均不得由前端另建状态真源。
12. 简单模式使用中文动作名称，原始 `/icode <step>` 只作为高级解释；选择值仍是服务端白名单中的既有步骤枚举。

## 4. 安全边界

- 固定 `127.0.0.1`，无 `--host`、无 CORS、无外链前端资源。
- UI 不接收 API Key、任意 shell、环境变量或浏览器提供的文件路径。
- `ui_instance.json` 只保存实例 ID、PID 和 loopback 端口，权限为 `0600`；会话 token 不落盘，复用前必须同时匹配登记与健康回执。
- 控制面只读策略返回的执行根须通过 checkout 拓扑校验；Runtime 不自行相信 metadata 路径。
- Runtime 不自动 commit、push、deploy、关闭工单、删除 checkout/branch 或操作硬件。
- 关闭/清理类动作不在本版 HTTP 动作集合中；后续加入时仍须 G4 和完整 ticket ID 二次确认，永不 force。
- 固定模式的旧有 bounded model `/run` 仅用于兼容；全局模式禁止暗选工单调用旧 `/run`。

## 5. 结束服务

用户要求停止 UI 时，只终止本次启动的 UI 服务进程并报告端口已释放；正常退出仅清理由自身 instance ID 持有的登记。不得通过模糊进程名批量杀死其它 Python、Codex 或 Claude Code 进程。
