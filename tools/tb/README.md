# tools/tb - Teambition 缺陷单拉取（icode log 步骤的可选数据源）

通用、参数化的 Teambition 缺陷拉取层，供 icode `/icode log` 步骤在**零散输入含 TB 引用时**可选调用，
把缺陷单的标题/描述/评论/日志附件拉到本地作为日志根因分析的输入。

> 本目录做「拉取 + 定时监控」，**不做回写**：不向 TB 发任何 POST，不生成评论草稿。
> 分析结论（`/icode log` 调用产出 `{ICODE_OUT_DIR}/log_analysis.md` + `00_init.md`；定时监控走 debug 语义
> 产出 `{工程}/.icode_output/.debug/`）供人工审阅，绝不自动回写 TB。

## 文件

| 文件 | 职责 |
|------|------|
| `scripts/tb_pull.py` | `list` 列缺陷；`defect <LIB-NUM>` 拉详情+真实评论+下载日志附件，写 `<ID>_meta.json`；`probe` 批量探测（list 全量+每单状态名/评论/附件元数据，不下载附件，按状态名过滤写 probe.json） |
| `scripts/tb_cookie.py` | 解密 Chrome cookie -> `scripts/.tb_cookie`（也可手动粘贴 cookie） |
| `scripts/tb_watch.py` | 定时增量监控（自循环守护）：周期 probe 打开/未完成单，按单号倒序检查，发现"有更新"的单自动拉起 claude 无头会话做**完整 `/icode log --debug` 深度分析**（详见下方「定时增量监控」节） |
| `scripts/tb_watch_ctl.sh` | 守护控制脚本：`start` / `stop` / `stop --force` / `status`（工程路径配在配置 `project_dir` 字段） |
| `config.example.json` | tb_pull 配置模板（占位）。复制为 `config.json` 后填真实项目 |
| `tb_watch.config.example.json` | **tb_watch 配置模板（占位）**。复制为 `~/.claude/icode_data/tb_watch.json` 后填 `project_dir` + 项目 url |

## 依赖

```
pip install requests cryptography secretstorage
```
- `requests`：tb_pull 必需。
- `cryptography` + `secretstorage`：仅 tb_cookie.py 的 Chrome 自动解密需要；手动粘贴 cookie 可不装。

## 首次配置

**config 可选**：`/icode log <TB URL> <单号>` 用法不配 config 也行--AI 从 URL 抽 domain+pid 传 `--domain --pid`。config 仅在多项目 lib 快捷（`--lib`）或固定 domain 时更省事。

1. **建 config（可选）**：`cp config.example.json config.json`，填 `domain` 和 `projects`（见下）。不建也行，用 `--domain` 传域名。
2. **取 cookie**：在 Chrome 登录 `https://<你的TB域名>/` 后，二选一：
   - 自动：`python3 scripts/tb_cookie.py --domain <你的TB域名>`（纯域名，不带 `https://` 和路径）
   - 手动：从浏览器 DevTools 复制 `<你的TB域名>` 的 Cookie 请求头，粘进 `scripts/.tb_cookie`（单行 `name=value; name=value; ...`）

## 多项目配置（文本配，加项目不改代码）

`config.json` 的 `projects` 是字典，**加一个项目 = 加一个 key**：

```json
{
  "domain": "<你的TB域名>",
  "projects": {
    "DEMO": {
      "pid": "<项目ID，从TB项目URL取>",
      "label": "<项目名称，如 XXX项目测试缺陷管理库>",
      "url": "https://<你的TB域名>/project/<项目ID>"
    },
    "PROJ": {
      "pid": "<另一个项目ID>",
      "label": "<另一个项目名称>",
      "url": "https://<你的TB域名>/project/<项目ID>"
    }
  }
}
```

- `key`（如 `DEMO`）：缺陷库前缀，须大写字母，用于 `<LIB>-<NUM>` 形式（如 `DEMO-26`）。
- `pid`：项目 id，从 TB 项目 URL 里取（`<TB域名>/project/<pid>/...`）。
- `label`：项目名称，人读 + 按名匹配用。
- `url`：TB 项目地址，可选，登记与溯源用。

## 命令（调试 / 单独用时）

```bash
# 列缺陷
python3 scripts/tb_pull.py --lib DEMO list
python3 scripts/tb_pull.py --lib DEMO list --status all --json
python3 scripts/tb_pull.py --lib DEMO list --with-status   # 状态列显示真实任务流状态名（逐单拉详情，较慢）

# 拉单个缺陷（下到 config.log_root/<ID>/）
python3 scripts/tb_pull.py defect DEMO-26
python3 scripts/tb_pull.py defect DEMO-26 --meta-only      # 只写 meta.json 不下载附件（批量探测/复用预筛用）

# 批量探测：枚举全量 + 每单状态名/评论/附件元数据（不下载附件），按状态名过滤写 probe.json
python3 scripts/tb_pull.py --lib DEMO probe --status-names 打开,未完成

# 拉一个 URL 带来、未在 config 登记的项目（--pid 权威）
python3 scripts/tb_pull.py --domain <你的TB域名> --pid <项目ID> defect DEMO-26 --out ~/work/log
# --domain 传纯域名（不带 https:// 和路径）；即使误带，脚本也会自动剥掉并警告
```

`defect` 产物：下载根目录（`--out`，缺省 `config.log_root`，如 `~/work/log`）下 `<ID>/` 里是日志附件 +
`<ID>_meta.json`（title/note/真实评论原文/附件清单/下载清单/`status` 任务流状态名）。

## ⚠️ 状态过滤必须按任务流状态名，不能用 isDone

`list`/`defect`/`probe` 的 task 对象里 `isDone` **与任务流状态不同步**：实测存在 `isDone=True`（`accomplished` 有值）但任务流状态仍是「未完成」的单（如 `DEMO-47`），按 `isDone` 过滤会把这类"未完成"单误判为完成漏掉。

- 真实状态名 = 任务详情内嵌的 `taskflowstatus.name`（list 接口不含，须 `--with-status`/`defect`/`probe` 逐单拉详情）
- "打开" 与 "未完成" 是**不同任务流**里的状态名（缺陷流程的状态叫"打开"，任务流程的状态叫"未完成"），按需用 `--status-names` 指定集合
- `list --status open/done` 仍是 isDone 布尔过滤（仅通用枚举用），**状态过滤请用 `probe --status-names`**

## 被 icode log 步骤调用时

`/icode log` 在阶段0 输入收敛时，若零散输入含 Teambition 项目 URL（含 `/project/<pid>/` 路径）或 `<LIB>-<NUM>`，会自动：

```
python3 tools/tb/scripts/tb_pull.py --pid <URL里的pid> defect <LIB>-<NUM> --out {ICODE_OUT_DIR}/tb_source
```

附件落到 `{ICODE_OUT_DIR}/tb_source/<ID>/`，作为本次日志根因分析的输入目录，并在 `.ico_metadata.json`
记 `tb_source` 溯源字段。**零散输入无 TB 引用时整段跳过，log 步骤走纯本地日志路径，行为不变。**

## 定时增量监控（tb_watch.py）

**用途**：定时监控一个或多个 TB 项目（"打开/未完成"单），周期检测各单是否有新增内容（评论/附件/状态变化），
有则自动拉起 claude 无头会话做 **完整 `/icode log --debug` 深度分析**（**下载并解压 TB 日志附件**做日志实证根因分析，
完整 log 流程：limit 红线检查点 → 历史工单检索 → 工程知识库/cheap-research 检索 → 00_init 需求初稿 → 对抗根因分析；
产物落 `{工程}/.icode_output/.debug/`，**不写全局 index.json，不污染正式索引**）。适合"缺陷单持续有更新、想自动跟进分析"的长期监控场景。

**debug 语义（核心）**：监控触发的分析一律走 icode debug 变体——每单独立 debug 工单在
`{工程}/.icode_output/.debug/.icode_output_N/`，metadata 写 `debug=true`/`indexed=false`/`tb_source` 完整版，
**绝不写全局 index.json**；检测"有更新"的比对对象 = debug 域里该单的旧 debug 孪生（按 `tb_source` 的 lib+num+pid 匹配），
与正式工单/正式索引完全脱钩。想要正式修复分析请用不带 `--debug` 的 `/icode log`。

**配置化·多项目**：JSON 配置文件列出多个项目，每轮遍历全部项目。**最小配置只需给 URL**（自动解析 domain+pid）：

```json
{
  "interval": 900,
  "projects": [
    {"url": "https://tb.example.com/project/<项目ID>"},
    {"url": "https://tb.example.com/project/<项目ID2>", "lib": "DEMO", "status_names": "打开,未完成"}
  ]
}
```

- `interval`：轮询间隔秒（默认 900 = 15 分钟；**分析完才计时**）
- `claude_timeout`：单次 claude 分析超时秒（默认 6000 = 100 分钟）。触发的是**完整深度分析**（下载解压日志附件 +
  limit 红线/历史检索/cheap-research/00_init/对抗分析，单次可达 1 小时+），6000 只是**防挂死兜底上限**，按需调大；
  超时则本轮放弃该单（meta 未变）、下一轮重试，不阻塞守护
- `project_dir`：工程根（可选，报告与 debug 工单落点 `{工程}/.icode_output/`；默认 = 启动时 cwd）。
  配了之后 `tb_watch_ctl.sh start/stop/status` 免传 `--project-dir`
- `claude_skip_permissions`：true 时给 claude 加 `--dangerously-skip-permissions`（无人值守所需，见风险）
- `low_priority`：true（默认）时 claude 分析进程**降级**（nice 10 + ionice idle，子进程继承），
  监控分析只在系统空闲时占用资源、不抢交互操作；false 关闭。配合"每轮只跑 1 个、串行、分析完才下一轮"，
  **同一时刻最多 1 个 claude 分析进程，不会并发堆叠**
- 每项目：`url`（必填，自动解析 domain+pid）、`lib`（可选，缺陷库前缀，用于 debug 孪生匹配）、
  `status_names`（可选，默认 `打开,未完成`）

**首次使用（新用户）**：仓库已带占位模板 `tools/tb/tb_watch.config.example.json`，
复制为默认配置位置 `~/.claude/icode_data/tb_watch.json` 后，填两处即可：

```bash
cp tools/tb/tb_watch.config.example.json ~/.claude/icode_data/tb_watch.json
# 然后编辑：① 顶层 "project_dir" 改成监控工程的绝对路径；② "projects[0].url" 改成真实 TB 项目 URL
#（可选）③ "projects[0].lib" 改成缺陷库前缀（如 DEMO）；④ 按需调 interval / claude_timeout
```

配置存在与否由 `tb_watch_ctl.sh start` 自动判定：缺配置时给出"复制模板"提示而不是直接失败。
（tb_pull 自己的 `config.example.json` 结构不同，两者不要混用。）

**启动 / 停止**：`start` 幂等——重复 `start` 不会起多个实例：ctl 层先查 pid 文件（已在运行则提示"已在运行 PID=xxx"并退出、不重复启动；残留 pid 文件=进程已死则自动清理），python 层另有 flock 单实例兜底。
优雅 `stop`：**间隔等待可被中断**（可中断 sleep，无分析在跑时 ~5s 内退出）；若正在跑 claude 分析则等它结束（最长 `claude_timeout`）；要立即停用 `stop --force`。

```bash
# 启动（工程路径配在配置 JSON 顶层 "project_dir" 字段，产物落该工程 .icode_output/；缺省 = cwd）
tools/tb/scripts/tb_watch_ctl.sh start --config watch.json

# 查看状态（运行中/未运行 + 正在分析的子进程）
tools/tb/scripts/tb_watch_ctl.sh status

# 优雅停止（SIGTERM，当前轮结束后停）
tools/tb/scripts/tb_watch_ctl.sh stop

# 强制停止（中断正在跑的分析，杀守护+子进程；分析中需立即停时用）
tools/tb/scripts/tb_watch_ctl.sh stop --force
```

**检索报告（分析最新状态）**：每轮覆盖写 `{工程}/.icode_output/tb_watch_report.md`，
列出全部"打开/未完成"单（按单号倒序）及每单分析状态（需增量 / 无更新 / 待新建·建基线）、debug 工单位置；
轮询流水追加在 `{工程}/.icode_output/tb_watch/watch.log`。

**自动建基线**：首次监控时 debug 域无基线，单全部为"待新建"——watch 自动逐轮触发 claude 对该单做
debug 全量分析**建基线**（每轮一条，按单号倒序），全部建完后转入纯增量监控（仅对"有更新"的单做增量）。
多项目时跨项目合并按单号倒序处理。

**每轮动作**：
1. 每个项目 `tb_pull.py probe --status-names 打开,未完成` 拉线上最新（零附件下载）
2. 扫 `{工程}/.icode_output/.debug/` 下 debug 工单 metadata 的 `tb_source`，按 lib+num+pid 定位每单旧 debug 孪生
3. 按单号倒序逐单机械"有更新判定"（比对口径对齐 log.md「批量 TB 分析」步骤3：评论 `(created, 评论文本)` 键差集、
   附件 `(name, ext)` 键差集、状态仅当旧 meta 含 `status` 字段才比）
4. 取单号最大的需增量/待新建单 -> 拉起 `claude -p` 触发 debug 分析（优先 `/icode log --debug <单>`，
   不可用则按 debug 语义手动建/复用 debug 工单；每轮一条）

**幂等**：分析成功把新数据并入该 debug 工单 meta -> 下一轮变"无更新"不再重复触发；
失败/超时则 meta 未变 -> 下一轮重试（不阻塞）。

**异常韧性（守护不因外部异常退出）**：
- 网络异常 / SMB 断开：单轮检测失败 -> 记 `watch.log` -> sleep 后下一轮重试，守护常驻不退出
- probe 卡死：单项目拉取超时兜底 `PROBE_TIMEOUT`（600s），超时转检测失败，不会每轮永久挂起
- claude 分析报错/超时：按"失败/超时"记录，meta 未变下一轮重试；claude 命令不存在/无法启动也按失败记录，不中断守护
- 写 `watch.log` / 报告失败（SMB 断开）：仅降级到 stderr 提示，不抛异常
- 唯一例外：工程目录在**启动时**不可达（如 SMB 未挂载）守护起不来——此时 `ctl start` 的 console.log 可见 traceback，属"没起来"而非"运行中断开"，挂载后重新 start 即可

**演练 / 人工确认**：`--once --detect-only` 只跑一轮、只检测写报告、不触发 claude。

**⚠️ 权限风险**：无人值守触发 claude 需配置 `claude_skip_permissions: true`（等价 `--dangerously-skip-permissions`
全放行，高风险），仅在**受信工程 + icode 只读 TB 约束**下使用；更安全的做法是不开它、改用 claude `--allowedTools`
白名单（配置略繁，按需选用）。`--detect-only` 不触发 claude，无此风险。
