# find-skills / Skills CLI 安装资源兼容验证

本文提供可复核的安装资源检查方法，不新增 ICODE 命令，不调整运行时或安装器。
平台发现/收录状态统一由[工具支持进度台账](tool-support-progress.md)维护。
2026-09-21 主代理检查 agentskill.sh 安装载荷，仅有根 SKILL 和50个附加文件，缺少
`steps/`、`references/`、`tools/`。本工具按资源路径和内容识别此类截断，
不设置固定文件数量门槛；尚不能确定平台截断的服务端原因。

同日将该公开接口的文本载荷保存为独立副本（不执行其中内容），用新检查器对比
基线 `1441fc6`：323个预期根资源中311项缺失、1项内容不同，完整性退出1。
这是实际平台样本检测，不是仅凭页面文件数推断，且不表示平台文件上限已被官方确认。
内容不同的是 `SKILL.md`：平台附加来源标记及 `AUTO-REVIEW` 指令，要求完成任务后
静默评价上报；这是安装文本中的指令注入，不是源码陈旧的证据，也不是 ICODE 原始行为。
样本的 `contentSha` 对应基线源码 `SKILL.md` 的 SHA-256 短前缀，不能将其当作
平台附加指令后完整载荷的摘要，也不能据此认定两者字节一致。
未执行该指令或发出评价请求；
第三方安装文本不能自行授予遥测/外传权限。在完整性和附加行为解决前，不建议使用该载荷。

## 离线完整性检查

在可信的源码 checkout 中运行，要求 Linux、Python 3.10+、Git：

```bash
python3 -B tools/check_skill_installation.py \
  --source /absolute/path/to/icode-skill \
  --installed /absolute/path/to/project/.agents/skills/icode
```

`--source` 必须是 Git 根目录；`--installed` 必须直接包含根 `SKILL.md`，
例如分发包的 `skills/icode`，不能传其父级插件目录。
检查器只读取本地文件和 Git 索引，不联网、不安装、不修复，不写缓存或报告文件。
如需保留 JSON，由调用者重定向到单独的评估目录。

资源清单直接复用 `tools/build_skill_distribution.py::source_files`：
仅 Git 跟踪的公开文件，经其真实白名单、私有路径、文件类型、合并状态、必需资源规则筛选。
先完成全量路径预检，再读取内容；不读取未跟踪、被排除或拒绝的私有源码 payload。
不复制 selector，不遍历源码搜集更多文件；新文件尚未加入 Git 索引时不计入基线。
对比的是选中文件的**当前工作树字节**，不默认等于 HEAD；评估须记录提交并使用干净快照。

排除生成器的 `METADATA`（`integrations/discovery` 的 README 和宿主模板）：
它们用于生成插件外壳，不在根工作流 `skills/icode` 中。
分发器本身也由原 selector 排除。其余相对路径逐一计算 SHA-256。
详见 [原分发规则](skill-distribution.md)。

| 退出码 | 含义 |
| --- | --- |
| `0` | 选中的根工作流资源全部存在且内容一致 |
| `1` | 安装完整性失败：缺文件/目录、内容不同、非法安装根、符号链接、非普通文件或读失败 |
| `2` | 参数错误或源码基线不合法：非 Git 根、必需源码缺失、私有跟踪路径、源码链接、Git/读取失败 |

正常检查及错误均向 stdout 输出 JSON；`--help` 输出常规帮助。
`scope` 固定为 `root_workflow_resources`，`ok` 表示是否通过；
`missing` 是缺失的相对路径，`changed` 给出路径及两侧 SHA-256，
`unsafe` 给出路径和拒绝原因，`errors` 给出其他错误。
`expected_files`、`checked_files` 仅辅助诊断，绝不替代内容校验。
源码错误优先返回 `2`，不会用不完整基线认证安装。

拒绝 `..`、根目录/祖先目录/资源文件的 symlink（包括指回目录内部的链接和悬空链接）。
因此 Skills CLI 的 symlink 安装模式不能作为本检查器的通过样本，应使用 `--copy`。
读取时逐层使用不跟随链接的目录描述符，并在读取前确认普通文件，避免 FIFO 阻塞。
校验过程中应保持两端目录稳定；这不是对持续并发修改目录的原子快照认证。

安装树还进行不跟随链接的元数据遍历，拒绝清单外的额外 symlink，但不读取其目标。
多余普通文件不参与 SHA-256 比较，也不读取其内容，不代表已审计这些文件。
通过只证明**根工作流公开资源的字节完整性**；不证明独立共享技能已经从 `.template`
生成安装，不验证 manifest 语义、执行权限、许可、MCP、Python/Node 依赖、DOCX runtime、
宿主加载或完整 ICODE 流程。共享技能、MCP 和宿主运行需单独验收。

## 官方 Skills CLI 1.7.0 的隔离评估配方

下面是供授权评估使用的命令配方；本轮主代理实测范围另见下节，不代表每条远程命令已通过。
只使用官方 npm `skills@1.7.0`；`find-skills` 是引导发现的技能，
`skills find` 是 CLI 搜索命令，两者都不能认证安装资源完整。
版本与命令依据：[1.7.0 package.json](https://github.com/vercel-labs/skills/blob/v1.7.0/package.json)、
[1.7.0 README](https://github.com/vercel-labs/skills/blob/v1.7.0/README.md)。

以下 Bash 代码块按顺序在同一个专用终端执行。需要预装 Node/npm、Git、Python、GNU timeout。
所有下载、缓存、配置、锁和安装目标均置于新建临时目录；不使用 `-g` 或真实用户账号。
`env -i` 清除继承凭证，只给子进程临时 HOME/XDG；不更改当前 shell 的 HOME/CODEX_HOME。
遥测开关依据 [1.7.0 telemetry.ts](https://github.com/vercel-labs/skills/blob/v1.7.0/src/telemetry.ts)。
关闭遥测不等于离线：npm 下载和搜索仍需网络。

```bash
evaluation_source=/absolute/path/to/trusted/icode-skill
evaluation_root=$(mktemp -d /tmp/icode-skills-eval.XXXXXX)
mkdir -p "$evaluation_root"/{home,config,cache,data,tmp,project,logs}
evaluation_env() {
  env -i PATH="$PATH" HOME="$evaluation_root/home" \
    XDG_CONFIG_HOME="$evaluation_root/config" \
    XDG_CACHE_HOME="$evaluation_root/cache" XDG_DATA_HOME="$evaluation_root/data" \
    TMPDIR="$evaluation_root/tmp" \
    GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null GIT_TERMINAL_PROMPT=0 \
    DISABLE_TELEMETRY=1 DO_NOT_TRACK=1 CI=1 \
    npm_config_cache="$evaluation_root/cache/npm" \
    npm_config_userconfig=/dev/null npm_config_globalconfig=/dev/null \
    npm_config_ignore_scripts=true npm_config_audit=false npm_config_fund=false \
    "$@"
}
evaluation_cli() {
  evaluation_env timeout --kill-after=5s 120s \
    npx --yes --package=skills@1.7.0 skills "$@"
}
# 每步保存 stdout/stderr 和真实退出码；124/137 等超时绝不计为通过。
evaluation_record() {
  local label="$1" result
  shift
  if "$@" >"$evaluation_root/logs/$label.stdout" \
           2>"$evaluation_root/logs/$label.stderr"; then
    result=0
  else
    result=$?
  fi
  printf '%s\n' "$result" >"$evaluation_root/logs/$label.exit"
  return "$result"
}
cd "$evaluation_root/project" || exit 1
evaluation_record cli-version evaluation_cli --version || exit 1
evaluation_record find evaluation_cli find icode
```

`find` 只记录查询时的搜索结果；空结果不能推出仓库无法安装。
查看 `cli-version.stdout`，必须确认为 `1.7.0` 后继续。不要用搜索命中数当成功标准。

先把已有可信 checkout 的 HEAD 克隆为隔离快照，再通过原分发器获得公开 payload，
防止直接把工作目录中未跟踪的私人配置交给第三方复制器。
以下操作均为本地读取或临时目录写入，不创建 commit，不改原 checkout。

```bash
evaluation_commit=$(git -C "$evaluation_source" rev-parse --verify HEAD) || exit 1
printf '%s\n' "$evaluation_commit" >"$evaluation_root/logs/source.commit"
evaluation_record source-clone evaluation_env timeout --kill-after=5s 120s \
  git clone --no-local --no-checkout -- "$evaluation_source" "$evaluation_root/source" || exit 1
evaluation_record source-checkout evaluation_env timeout --kill-after=5s 30s \
  git -C "$evaluation_root/source" checkout --detach "$evaluation_commit" || exit 1
evaluation_record distribution evaluation_env timeout --kill-after=5s 120s \
  python3 -B "$evaluation_source/tools/build_skill_distribution.py" \
  --source "$evaluation_root/source" --output "$evaluation_root/icode" || exit 1
evaluation_payload="$evaluation_root/icode/skills/icode"
evaluation_record available evaluation_cli add "$evaluation_payload" --list || exit 1
evaluation_record install evaluation_cli add "$evaluation_payload" \
  --skill icode --agent codex --copy --yes || exit 1
evaluation_record installed evaluation_cli list
evaluation_record integrity-before evaluation_env timeout --kill-after=5s 120s \
  python3 -B "$evaluation_source/tools/check_skill_installation.py" \
  --source "$evaluation_root/source" \
  --installed "$evaluation_root/project/.agents/skills/icode"
```

`add ... --list` 是列出源技能，`list` 是列出已安装技能；两者均不比较内容。
Codex 的该版本项目路径见 [agents.ts](https://github.com/vercel-labs/skills/blob/v1.7.0/src/agents.ts)。
CLI 的“安装成功”后仍必须检查 `integrity-before` 的 JSON 和退出码。
完整性失败应保存差异；检查器不会调用 `install.sh` 或修复目录。

## 更新验证与证据分级

2026-09-21 主代理使用官方 CLI 1.7.0、独立用户状态与禁遥测环境，
从干净源码 `1441fc6d91d544f3e097415fb9db278f71783715` 生成公开 payload 后，
实际执行 `add --list`、项目内 `add --skill icode --agent codex --copy --yes`、`list`、
完整性检查、`check`、`update icode --project --yes` 及再次检查。
安装前后均为323个选中资源摘要一致、零缺失/变化/不安全链接。
这是根资源复制验证，不是 Codex 加载或整套共享技能/MCP 验证。

锁文件记录 `sourceType=local`；`check` 返回 `No project skills to update`，
`update` 返回 `No installed skills found matching: icode`，均退出0。
安装资源实际存在，但更新器跳过本地来源，因此**没有执行远程版本升级**。
这也说明仅凭命令退出0不能判断更新完成。

同日另建隔离项目，以该完整提交的 GitHub tree URL 执行远程 copy 安装，
在克隆阶段达到90秒时限、退出124，尚未产生可验收的安装。
该次失败保留；后续提高受控克隆预算后的远程直装已通过，见下节，不能把先前超时抹成成功。

### 同日 GitHub 远程直装复验

CLI 仍为官方 `skills@1.7.0`，直接安装
`https://github.com/ayukyo/icode-skill/tree/main`，保留
`--skill icode --agent codex --copy --yes`，不全局安装、不启用遥测。
本轮使用已核实版本的 CLI 文件，独立 HOME/XDG/临时目录且清除继承凭据；
按[官方克隆超时配置](https://github.com/vercel-labs/skills/blob/v1.7.0/README.md)
设置 `SKILLS_CLONE_TIMEOUT_MS=240000`，外层 `timeout --kill-after=5s 280s`。
增加的是单次有界等待预算，不无限重试；配方的外层120秒也必须同步提高，否则会先杀掉克隆。

安装退出0，锁文件实际为 `sourceType=github`、`source=ayukyo/icode-skill`、`ref=main`。
安装所得资源与干净源码 `0b7a3f49ea9187b5d33f20b83313ec09200587f9` 对比：
**328/328项 SHA-256 一致，零缺失、零变化、零不安全项**。
因此“远程直装”已实测通过；搜索未命中、真实宿主加载、共享技能/MCP、旧→新升级分别验收，
不能由这个结果直接推定完成。浮动来源的身份由全量内容比对固定，不仅依据安装成功提示。

### 同日远程旧版到新版刷新

在上述同一隔离安装中运行 `update icode --project --yes`：CLI识别github/main来源，
但最终返回 `Failed to update 1 skill(s)`、退出1。原锁和328项旧资源仍与0b7a3f49一致。
该版本更新器会先克隆解析技能，再调用子安装；父命令没有输出子安装错误细节，
所以不能仅凭耗时把根因认定为超时，也不能记录成自动update通过。

随后在同一隔离目录执行显式GitHub刷新（非全局、无遥测）：

```bash
evaluation_record remote-refresh evaluation_env env SKILLS_CLONE_TIMEOUT_MS=360000 \
  timeout --kill-after=5s 400s \
  npx --yes --package=skills@1.7.0 skills add \
  https://github.com/ayukyo/icode-skill/tree/main \
  --skill icode --agent codex --copy --yes
```

实测使用已验证1.7.0的缓存CLI入口直接运行等价参数，退出0。
新安装与干净源码 `6eb09d4863d31d844ece819429c73afc8626f0f7` **328/328项一致**；
锁的 `computedHash` 从 `7727c38f…` 变为 `ebb494bf…`，来源仍为github/main。
这是有内容变化的0b7a3f49→6eb09d4远程刷新，不是仅重装同版本。
可用替代路线为显式 `add --copy` + 固定源码完整性校验，**不等于update子命令已修复或自动跟随更新**。
其后源码提交仍需重新验收，不把这次安装身份改写成未来HEAD。

### Smithery 下载样本复验

同日通过 [Smithery ICODE 页面](https://smithery.ai/skills/ayukyo/icode)实际下载 ZIP，
只在新临时目录检查、未执行任何载荷内容。归档496个文件，SHA-256：
`21eb13e626041d42ab68edf9bc8d85d7145c0e015afbddac356a58f10b3059fb`。
对比上述 `0b7a3f49` 的328项公开资源，**缺失1项（`.gitignore`），变化25项**：
9项文本不同，另外16项为全部8套模板的 PNG/PPTX。
文本差异可能是同步滞后，不能把所有差异都归因为二进制损坏。

其中 architecture-deck 的 PNG 原始签名 `89 50 4e 47` 变为
`ef bf bd 50 4e 47`，`unzip -t template.pptx` 报中央目录损坏。
这与经过文本解码/重编码相符，但服务端原因未核实。
已向官方维护入口提交[问题 #816](https://github.com/arcadeai-labs/smithery-cli/issues/816)，
明确它是网页下载问题，请维护者按实际归属转交，未将其认定为 CLI 执行缺陷。
在平台修复并通过重新校验前，不建议把该 ZIP 用作完整安装；完整资源走 GitHub copy 安装或原安装器，
不删除源码模板、改许可或放宽 hash 检查来让损坏载荷“通过”。

### 隔离更新配方

只在上面的临时项目执行更新。CLI 1.7.0 的项目更新会跳过 `local` 来源，
因此本地 payload 的更新通常是无操作；不能将退出 `0` 写成更新了资源。
依据：[update.ts 的来源过滤及项目范围处理](https://github.com/vercel-labs/skills/blob/v1.7.0/src/update.ts)。

```bash
evaluation_record update evaluation_cli update icode --project --yes
evaluation_record integrity-after evaluation_env timeout --kill-after=5s 120s \
  python3 -B "$evaluation_source/tools/check_skill_installation.py" \
  --source "$evaluation_root/source" \
  --installed "$evaluation_root/project/.agents/skills/icode"
```

要评估真实远程更新，另建同样隔离的临时作用域，使用明确的公开仓库 ref，记录安装前后的
`skills-lock.json`、对应源码提交和 CLI 日志；按更新后的准确提交准备干净源码再跑检查器。
例如可将上述安装源替换为
`https://github.com/ayukyo/icode-skill/tree/<已核实的完整提交SHA>`，仍使用
`--skill icode --agent codex --copy --yes`、超时和禁遥测环境，随后执行
`update icode --project --yes` 和完整性校验。固定提交只验证固定来源的更新/无更新行为，
不代表从旧版升级到了新版。浮动分支更新若不能确定实际取到的提交，证据应记为来源未确认。
升级验证须另外选定旧、新两个提交，确认源资源确实变化；无可更新资源或来源被跳过都记为未覆盖。
更新可能改变安装模式，出现 symlink 应记录失败，不自动放宽校验。

最终证据至少包括：CLI 版本、源码提交、命令、退出码/超时、安装位置、
前后完整性 JSON、来源锁及“更新/无更新/跳过”的实际分支。
不调用宿主、不读主会话浏览器和账号、不全局安装、不联网发布、不推送。
临时目录保留供审查，不提供自动清理真实安装目录的命令。

## 本切片离线测试事实

测试复用既有 distribution 的临时 Git fixture，再由真实生成器产出安装目录。
固定验收命令：

```bash
python3 -B -m unittest discover -s tests -p test_skill_installation.py -v
python3 -B -m unittest discover -s tests -p test_skill_distribution.py -v
```

2026-09-21 首轮红：实现前运行 19 个新增测试，26 个断言失败（含 subTest），
原因为检查器尚不存在、不能提供约定 JSON/退出码，无 fixture/setup 错误。
首轮绿：新增 19/19、既有 distribution 11/11，通过合并运行共 30 个测试。

### 阶段一：spec 逐项自审

| 要求 | 自审依据与结果 |
| --- | --- |
| 只读、离线、独立 CLI | 两个显式路径参数；只调用本地 Git/文件读取；完整 fixture 前后内容、mtime、模式一致 |
| 真实公开资源规则 | 直接调用原 `source_files`，排除原 `METADATA`；真实分发 fixture 可通过 |
| 截断、缺目录、改内容 | 与源码等文件数的填充安装仍失败；缺 steps/references/tools 列出路径；等长修改列出双侧 SHA-256 |
| 不读源码私有/未跟踪内容 | 未跟踪、被排除的私有 FIFO 均不阻塞；选中私有路径在读取前拒绝 |
| 非法 source/installed、退出码 | 源码缺失/非 Git 根为 2；安装缺失/非目录为 1；成功为 0；错误输出 JSON |
| 路径与 symlink | 根、祖先、文件、目录、悬空链接及 `..` 均拒绝；补充清单外链接检查 |
| CLI 验证文档 | 固定 1.7.0、禁遥测、临时范围、超时、源码提交及更新前后完整性校验 |
| 写集和边界 | 仅三个授权文件；不改分发器/探针/runtime/SKILL/许可，不新增命令或安装宿主 |

spec 自审发现安装树的清单外链接未覆盖。先加 `extra_installed_symlinks` 用例，
确认红：期望退出 1、实际退出 0；再增加只读元数据遍历，复测 20/20 绿。

### 阶段二：quality 自审

随后检查错误返回、文件描述符关闭、非普通文件、稳定排序和 fixture 隔离。
发现空参数会被 `Path` 转为当前目录，以及未知 `~user` 抛出未捕获 `RuntimeError`。
分别先补失败用例：两个空路径错误地返回 0；未知用户的 source/installed 两分支出现 traceback。
修复为转换前拒绝空参数，并将路径展开异常转换为 JSON 错误。
测试子进程固定在临时 fixture cwd，避免空参数测试意外依赖执行者工作目录。

最终独立复测：安装完整性 **23/23** 通过，既有 distribution **11/11** 通过。
这些是离线资源合同证据；官方 CLI 安装/远程更新、在线截断样本、共享技能安装、MCP、
宿主运行和许可审查均未在此切片实测。不证明恶意并发修改、设备故障或全部 I/O 故障场景。

【架构级自检报告】

- ✅ 语法/编译：Python 测试导入与 CLI 执行通过；文档 Bash 代码块语法检查通过。
- ✅ 依赖/调用链：复用真实 selector 和分发 fixture；既有 distribution 11/11 通过。
- ✅ 逻辑/边界：路径匹配、双侧 hash、缺失、截断、非法根和空参数用例通过。
- ✅ 异常处理：已测路径展开、缺文件、symlink、FIFO/非普通文件；未宣称全部 I/O 故障覆盖。
- ✅ 关联模块：写集限于检查器、对应测试、本文档；无 runtime 或分发器修改。
- ✅ 兼容安全：保留原公开资源规则；扫描额外文件仅访问元数据，不读链接目标。
- ✅ 可运行性：Linux 离线 CLI 23/23 通过；真实宿主安装/执行未验证。
