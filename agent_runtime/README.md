# ICODE Agent Runtime v0.4

可选的本地 Agent 执行层。它通过系统终端进程运行，为一个 v3 工单执行一个有界模型回合；不替代 `tools/icode_control.py`，不自动推进工单状态，也不改变 Codex/Claude Code 的既有 `/icode` 用法。

## 调用关系

```text
Codex / Claude Code / CI / 手工终端
                |
                v
      python3 tools/icode_agent.py
                |
       record-agent-spawn/result
                |
                v
       tools/icode_control.py
```

v0.4 把可选本地 Web UI 扩展为 ICODE Manager 2.0。它不是常驻服务：只在显式执行 `ui` 子命令后监听 `127.0.0.1`，当前终端退出即停止。Codex/Claude Code 的聊天界面和原有 `status/run` 继续可用；UI 只通过真实宿主 CLI 执行已获控制面许可的 `/icode <step>`。

## 本地 Web UI

```bash
python3 tools/icode_agent.py ui
```

命令默认监听 `127.0.0.1:8765` 并打开浏览器；端口被占用时自动选择空闲 loopback 端口。无参数重复调用会通过 `~/.claude/icode_data/ui_instance.json` 的私有登记和带实例 ID 的健康探测复用现有全局 UI，不会不断创建新端口。服务器会在终端打印实际 URL 和 `reused` 状态。不希望自动打开浏览器时：

```bash
python3 tools/icode_agent.py ui --no-browser
```

UI 可以浏览、筛选和排序全局项目/工单，创建并登记第一张工单，查看控制面推荐下一步、步骤进度、交付结论、验证记录和白名单关键产物，并通过 Codex 或 Claude Code CLI 执行受控 ICODE 步骤。简单模式显示“制定实施计划”“编码实现”等中文动作，高级模式保留 `/icode <step>` 身份。自动刷新默认 30 秒，可在设置中选择 5 秒到 5 分钟；约 1.2 秒一次的有界事件轮询只更新任务进度，手动刷新始终可用。新建工单不直接调用模型：控制面先原子分配编号并写索引，随后用户再运行推荐的计划步骤。安全边界：

- 固定监听 `127.0.0.1`，不提供 `--host` 或远程访问。
- 新增 API 必须携带页面会话令牌；写请求是 64 KiB 内的严格 JSON，并拒绝跨源 Origin、恶意 Host 和未知字段。
- 页面资源全部随仓提供，无 CDN、内联脚本或第三方前端依赖；动态内容只用 `textContent`。
- UI 不接收 API Key、ticket path、任意文件路径或任意 shell；浏览器只提交 ticket ID、动作枚举和 revision。
- 推荐动作、debug/关闭隔离、事件链 revision 与可信执行根来自 `action-policy`，刷新失败即禁用写动作。
- Host 启动前后由控制面记录 Agent spawn/result；同工单跨 UI 进程独占，结果不明确时不重放。
- 模型正文只保存在当前 Runtime 内存并设总量上限；常见 token/API Key 形态在进入 UI 前脱敏，事件链只写摘要 digest 与短证据引用。
- `ui_jobs.json` 只持久化任务身份、状态和脱敏短摘要，不保存 prompt 或原始输出；Runtime 重启后活动任务显示“结果待核实”，绝不自动重放。
- 实例登记不保存 UI token；重复启动须同时匹配 `0600` 登记和当前 loopback 健康回执。过期登记只会被新实例原子覆盖。
- 不自动 commit、push、deploy、关闭工单、删除工作区或操作硬件。

`--port <0..65535>` 可固定本机端口；显式 `--port 0` 保持由系统选择空闲端口。显式端口被占用时直接退出，不切换远程绑定。`--dir <ticket_dir>` 保留 v0.2 固定单工单模式及旧 `/status`、`/run` API。

## 离线测试

Runtime/UI 专项 20 轮七维自检不访问模型或包索引：

```bash
bash tools/selfcheck_agent_ui.sh 20
```

```bash
python3 tools/icode_agent.py run \
  --dir <ticket_dir> \
  --backend fake --model fake-v1 --capability text \
  --prompt 'summarize evidence' --fake-response 'offline answer' \
  --task-scope 'bounded review' --expected-artifact 'text answer' \
  --evidence-boundary 'ticket-only' --join-condition 'non-empty output' \
  --request-id demo-turn-1
```

FakeBackend 不访问网络，只用于合同测试和演示，不能作为真实模型验证证据。

## OpenAI Responses API

OpenAI SDK 是可选依赖，只在显式选择该 Backend 时导入：

```bash
python3 -m pip install --user openai
export OPENAI_API_KEY='<通过安全环境注入>'
python3 tools/icode_agent.py run \
  --dir <ticket_dir> \
  --backend openai-responses --model <configured-model> \
  --capability text --prompt 'perform one bounded task' \
  --task-scope 'bounded task' --expected-artifact 'text result' \
  --evidence-boundary 'declared inputs only' \
  --join-condition 'non-empty bounded output' --request-id turn-1
```

API Key 只由 SDK 从环境读取，禁止放入命令参数、metadata、事件、工单产物或日志。Runtime 不保存完整模型正文到事件链，只保存摘要和短 evidence ref。

图片输入必须同时提供 `--capability image`；否则 Runtime 在写 spawn 和网络调用前拒绝：

```bash
python3 tools/icode_agent.py run ... \
  --capability text --capability image --image <url-or-data-url>
```

因此纯文本模型不会再通过 Runtime 收到 PPT 渲染图。是否真实支持图片仍由部署配置负责，capability 不得虚报。

## 状态与恢复

```bash
python3 tools/icode_agent.py status --dir <ticket_dir>
```

返回 `schema_version/ticket_id/spawns/open_spawns`。同一个 `request-id` 已出现时，Runtime 不会重放模型调用：开放 spawn 表示上次调用结果未知，已终结 spawn 表示结果正文未缓存；两者都需要人工核对后使用新请求继续。

## v0.4 边界

- UI 本身不解释模型 tool call；真实 ICODE 工具使用由 Codex/Claude Code 及既有步骤规则约束。
- UI 不成为 metadata/status 第二写入器；步骤仍通过现有 ICODE 控制面推进。
- 不自动 commit、push、deploy、删除或操作硬件。
- 不安装 provider SDK；只有显式 `ui` 在当前进程生命周期内启动 loopback 监听。

后续工具循环必须经过 Tool Gateway、`operation` 回执和副作用审批后再启用。
