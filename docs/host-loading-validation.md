# 多宿主安装与真实加载验证

核查日期：2026-09-21。来源为干净提交 `eaa0780708eb0d321d674f97662491b185059552`，
复用现有分发器生成完整包：ICODE + 16 个共享技能，共 **17 个技能、344 项技能文件**。
数量只描述此快照，不是以后版本的固定门槛。
后续可复用测试按此基线加本轮测试/文档差异重新生成包，校验当前Git跟踪文件的实际字节，
不把未提交工作树称为原始干净提交；新增文档进入清单后数量会相应变化。

## 本轮关闭的缺口

| 验证层 | 真实结果 | 尚未证明的能力 |
| --- | --- | --- |
| Codex CLI 0.154.0 | 真实 app-server `skills/list` 两次识别17项、enabled=true、零解析错误；移走根入口后刷新为16项，恢复后回到17项 | 桌面 UI、模型自动触发、完整工单执行、MCP与设备待验 |
| Claude Code 2.1.270 | 真实 stream-json 初始化列出17项项目技能；重复启动无重复条目；空项目反例不出现这些技能 | 模型选择/工具执行、老版本兼容待验 |
| Claude 本地插件入口 | `--plugin-dir` 读取原分发包，17项均以 `icode:<技能名>` 列出，包括 `icode:icode` | 命名空间入口下实际工作流、与全局技能共存优先级待验 |
| Skills CLI 1.7.0 | 下列10个目标均完成首次安装、重复copy安装与list检查；每次344项SHA-256一致、零符号链接 | 除上列两宿主外，尚未运行各宿主自身加载器；本地来源不证明远程更新 |

| 官方 CLI 的 `--agent` | 本轮实际项目安装目录 |
| --- | --- |
| `claude-code` | `.claude/skills/` |
| `codebuddy` | `.codebuddy/skills/` |
| `codex`、`cursor`、`gemini-cli`、`opencode`、`github-copilot`、`cline`、`antigravity` | `.agents/skills/` |
| `windsurf` | `.windsurf/skills/` |

这些是 [Skills CLI 1.7.0 的目录映射](https://github.com/vercel-labs/skills/blob/v1.7.0/src/agents.ts)，
不是所有宿主版本的通用保证。WorkBuddy 复用 `.codebuddy/` 的官方依据见
[接入说明](workbuddy-support.md)；它没有在本轮作为第11个真实宿主运行，不虚构 `--agent workbuddy`。

## 可复现的禁网检查

前置条件：Linux、Python 3.10+、Git、bubblewrap；加载检查使用开发机已经安装的原生
Codex/Claude Code 可执行文件。测试**不调用模型**、不登录、不发送用户提问、不安装宿主，
不加载真实用户 HOME、凭据或全局技能。子进程禁网；仅挂载系统可执行文件/库、少量非秘密
运行时文件、显式受信任CLI包及临时评估目录，不挂宿主运行时socket、用户目录或设备工程。
Git初始化也在沙箱内执行，空继承环境、禁用用户配置/模板并限制超时。
不使用 Claude 的 `--safe-mode`，因为它会禁用技能；改用空用户状态、空工具集、
`--strict-mcp-config` 和离线初始化协议。该检查不同于之前禁工具的模型文本评估。

```bash
# 在可信 ICODE checkout 根目录执行；默认未启用时仅报告 SKIP。
ICODE_RUN_HOST_LOADING=1 python3 -B -m unittest discover \
  -s tests -p test_host_skill_loading.py -v
```

初始化接口依据：本机 `codex app-server generate-json-schema` 生成的
InitializeParams / SkillsListParams / SkillsListResponse；
Claude [官方 SDK 初始化与命令查询](https://github.com/anthropics/claude-agent-sdk-python/blob/main/src/claude_agent_sdk/client.py)。
测试只发初始化/列举请求，不创建模型回合。宿主版本变更时必须重新核对协议。

10目标安装测试额外需要受信任的 **skills@1.7.0** 本地 npm 包及兼容 Node.js
（本轮 Node 24.14.1），获取方法见[隔离安装配方](find-skills-compatibility.md)。
传入该包的真实 `bin/cli.mjs`；不是其他同名脚本，也不调用自动下载的 npx。

```bash
ICODE_RUN_HOST_LOADING=1 \
ICODE_SKILLS_CLI=/absolute/path/to/node_modules/skills/bin/cli.mjs \
python3 -B -m unittest discover -s tests -p test_host_skill_loading.py -v
```

完整命令本轮 **7/7测试通过**，其中安装测试遍历10个目标、每个目标2次。
独立审查发现的宿主socket暴露及Git环境重定向风险，均以失败反例复现后修复；
两项隔离反例纳入此7项测试。早期仅只读根挂载的试验不作为最终隔离认证。
读取/初始化有30秒限制，单次安装45秒限制；失败或超时不计通过。
没有安装CLI、原生宿主或隔离依赖时明确跳过，CI默认也不会自动下载宿主、使用账号或模型预算。
测试依赖分发器的现有公开资源选择器；新增资源应先进入Git跟踪再作为完整基线验收。

## 其他宿主的完整技能安装路线

直接 `skills add ayukyo/icode-skill --skill icode` 只安装根技能；
仓库中的16个 `SKILL.md.template` 不会因此自动生成独立技能。
需要完整技能集合时，复用现有分发器，无须给每个宿主维护一份工作流：

```bash
distribution_parent=$(mktemp -d /tmp/icode-distribution.XXXXXX)
python3 -B tools/build_skill_distribution.py --output "$distribution_parent/icode"
# 在已授权的目标项目内，用已固定版本的官方CLI安装；选择表中的一个agent。
# 下行以Cursor为例；保持 --copy，避免把链接误当完整副本。
npx --yes --package=skills@1.7.0 skills add "$distribution_parent/icode" \
  --skill '*' --agent cursor --copy --yes
```

只有安装这一行应在目标项目内运行；生成步骤在源码根运行。
需要全程隔离、关闭遥测或保存退出码时，使用上述隔离配方，不在真实工程里做试验。
本地生成包的锁来源是local，`skills update`会跳过；源码更新后重新生成到新目录，
经检查和授权再显式安装，不能称为自动跟随GitHub。
MCP、命令桥、外部依赖和模型执行权限不会由上述复制过程自动配置。
既有三宿主用户仍可使用原 `install.sh`，本次不更改其行为或Runtime。

## 剩余验收条件

宿主发现、模型真实触发、步骤工具行为、共享技能调用/MCP降级、重复工单恢复是独立层次。
本轮将“两个宿主加载未验证”和“十目标完整包复制未验证”补为实测，
不把它们提升为所有宿主端到端认证，也不覆盖先前模型失败记录。
市场损坏包、审核与搜索排序继续按[工具台账](tool-support-progress.md)处理。

## 同日追加：真实模型工具执行

源码 `1fd17493477d95806f0f2a878340ac4f52e00252`，复用完整17技能分发包，
只向模型提供公开合成工单和相对目录，不把字段答案放进提问。
这次是单独授权的在线实测，**不是上面的禁网测试**，也不会加入默认CI。

| 宿主 / 模型 | 实际行为 | 判定 |
| --- | --- | --- |
| Codex CLI 0.154.0，CLI默认模型（回执未报告模型ID） | 实际读取项目内ICODE入口、状态步骤及metadata；四字段正确；执行`trace`得到legacy退出3，明确轨迹未验证，不迁移/续跑 | 只读status及legacy降级通过；不证明全流程、MCP或其他模型 |
| Claude Code 2.1.270，回执模型`deepseek-v4-flash-0731-vl` | 初始化列出17项插件技能和Read/Glob/Grep/Skill，但没有成功读取；去掉ICODE的单Read对照亦失败，尝试未提供的Bash调用 | 工具执行受阻；CLI退出0/模型success不算业务成功，不归因ICODE提示词 |
| OpenCode 1.18.31 | 官方npm依赖下载在有界预算内未完成；首次残缺文件的版本探测失败，随后停止运行未完成包 | 不将残缺包失败归因ICODE；不计加载或模型通过 |

Codex使用`--sandbox read-only --ephemeral --ignore-user-config --ignore-rules`，
在临时项目内执行，沿用该CLI既有认证，没有复制凭据或改配置。
此组合**不隔离全局技能发现**：日志同时出现两个无关全局技能的frontmatter错误；
本轮未修改那些技能，也不把本次称为纯净HOME或全部全局技能兼容验收。
Claude使用`--restricted --strict-mcp-config --tools Read,Glob,Grep,Skill`、
`--permission-prompts none --no-session-persistence`和显式临时`--plugin-dir`，
单次预算/超时有界；没有放开Bash、写工具或MCP来掩盖失败。
宿主声明有工具与模型声称无工具相矛盾，具体模型接入/转换原因仍未确认；
没有读取或修改账号密钥、供应商配置来推测原因。

下一步应分别验收真实写步骤、共享技能/MCP和恢复停止点；
先前文本评估、初始化通过和本次失败均保留，不合并成“全宿主通过”。
