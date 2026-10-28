# 环境诊断与现场验证登记

## 四层事实

| 层 | 检查方式 | 结论边界 |
|---|---|---|
| 源码 | 当前仓库与 `SKILL.md` | 源码存在不表示安装已更新 |
| 安装 | `mcp_doctor.py --source <源码根>` 的核心文件 SHA-256 | 仅覆盖 `CORE_RESOURCES` 声明的核心资源；完整发行资源校验仍用 `check_skill_installation.py` 的受支持平台与官方同步校验 |
| 注册 | JSON `mcpServers` / Codex TOML `mcp_servers` 与导出条目静态比对 | `static_ok` 只检查启动命令与平台形态，不证明依赖包、握手或远端服务正常 |
| 会话 | 重载后真实工具发现与调用 | 同步文件、正确注册和 MCP 已加载分别报告；静态工具始终返回 `unverified` |

## 只读诊断

```bash
python3 tools/mcp_doctor.py --json
python3 tools/mcp_doctor.py --source <源码根> --installed <安装根> --json
python3 tools/mcp_doctor.py --config <宿主JSON> --entries-dir <导出目录> --ticket-dir <明确工单目录> --json
```

Windows 可使用 `py -3`。默认读取 Claude JSON，以及已存在的 CodeBuddy/WorkBuddy JSON 和 `~/.codex/config.toml`；`.toml` 使用 `mcp_servers`，JSON 使用 `mcpServers`。Python 3.11 以上用标准库 tomllib，Python 3.10 可用 health MCP 条件依赖 tomli；两者均不可用时报告 `toml_parser_unavailable`，不当作空配置或健康。`--config` 可重复并替换默认配置集合。`--source` 未传时不做安装比较；传入后默认检查检测到的三个技能根，`--installed` 可重复指定范围。

默认退出 0 表示查询已完成，问题数见 `summary.problems`；`--strict` 在有问题时退出 1。参数错误退出 2。所有输出为 schema_version=1 JSON。不读取/回传密钥字段值、命令参数或环境变量值，不启动注册命令、不下载依赖、不写配置、不生成备份。配置与单文件读取上限 4 MiB，默认最多 100 项服务；无法完整读取时保留问题，不宣称健康。

分类包含 `foreign_os`、`command_not_found`、`stale_entrypoint`、`wrapper_shebang`、`missing`、`invalid_entry`；`drift` 与 `different_fields` 单独展示。相对命令名按条目 PATH 或当前进程 PATH 解析，因此结果也受实际宿主启动环境影响。Windows 的绝对 Node + npx-cli.js 还需检查包启动器所用的子进程 `node` 是否可由 PATH 解析；缺失报告 `node_runner_not_found`。修复复用 `platform_entry.py` 的 Node PATH 构造，保留用户其他 env，不通过增加超时掩盖启动失败。`enabled=false` / `disabled=true` 标为 disabled；`remote_unprobed`、`relative_command_unresolved` 等状态不能改写成握手通过。

health MCP 提供相同的 `diagnose_registration(host_config, entries_dir=None)`，配置、目录与每个导出文件逐个应用 allowed_roots。CLI 是本机显式诊断入口，不扩展 MCP 的权限。

修复按问题定位：安装漂移走官方 `scripts/sync-to-global.sh` 预览→apply→摘要/配置/入口复核→二次预览；注册漂移先核对导出来源与对应安装器，展示具体目标后按授权重新注册。不存在通用 `--fix`；不得运行任意配置项或自动删除其他服务。重载宿主后再验证工具。

## 降级进入交付摘要

`icode_delivery.py --dir <工单>` 的 `environment_observations` 读取 thinking trace 和已有 `.mcp_gate_report.json`，输出记录条数、success/degraded/blocked 计数及 MCP 不可用计数；不回传自由文本原因。thinking 计数包含历史重试，不是唯一步骤数；MCP 来源是已有派生报告，未重新执行其门禁。缺文件为 `untracked`、坏文件为 `partial`、存在降级为 `attention`。

status 与 audit 必须展示该观测，降级原因在原始 trace 本地核实，给出本诊断入口和具体后续动作。观测不改变现有 gate、delivery_verdict 或证据门槛；严格门禁仍按原规则执行。不能把没有 trace 当作零降级或完整验收。

## 现场续跑的收尾对账

completed 只表示主流程结束。后续改代码继续走 patch，只验证走 verify；close_state 已冻结的工单仍需按原合同 reopen，不能绕过。沿用现有 operation、artifact 与 `record-verification`，不新增 field 状态或免哈希通道。

每个实际执行阶段结束时，对照已执行动作与 `verification_runs` / `verification_recorded`：成功、失败和 inconclusive 都按原接口记录；绑定当次候选、environment、真实设备/启动身份、制品、时间窗、日志和 baseline。新轮次使用唯一原始证据路径，保留旧 artifact 内容与回执；受保护产物需改写时先走其既有重开/重审流程。

`record-verification --request-id <唯一键>` 的重放只保证相同登记幂等，不能据此再次执行部署/OTA。历史 HANDOFF/现场文本仅是候选线索，先核对原始日志；登记时间不能冒充历史执行时间，不能从文字“成功”补造受信回执。证据不足保留债务，不自动升级 verified。关闭前补录仅限现有 `--close-repair` 合同，不能将其用作通用现场入口。

收尾输出实际阶段、登记 run_id/证据路径、未登记或缺身份项、剩余验证债务、开放操作和下一步。暂停监控保持暂停，未知宿主进程映射保持未知；只读查询失败明确写“状态不可验证”。
