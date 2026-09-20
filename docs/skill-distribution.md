# ICODE 最小插件分发接入

核查日期：2026-09-20。本切片为 Claude Code、Codex、CodeBuddy 准备**可构建的完整技能资源包**，
不新增宿主运行适配，不安装宿主、不注册账号、不登记市场、不推送。
目录发现、安装入口、宿主执行、市场收录和推荐排名是不同验收层。

## 方案与根路径结论

只维护一个生成器 `tools/build_skill_distribution.py` 和三份薄清单模板。
构建结果共用一套 `skills/`，不为三个宿主复制三套工作流；生成包不提交 Git。

| 宿主 | 当前官方清单与发现依据 | 根 `skills: "./"` 与本次处理 |
| --- | --- | --- |
| Claude Code | `.claude-plugin/plugin.json`；`skills` 接受字符串或数组；常规布局是 `skills/<name>/SKILL.md` | **官方明确允许** `.`/`./` 指向直接含 `SKILL.md` 的目录；无 `skills/` 且无该字段时，根入口可作单技能插件自动加载。当前源码还含 16 个 `.template`，薄根清单不会将它们转换为共享技能，故生成标准多技能布局。[参考](https://code.claude.com/docs/en/plugins-reference#path-behavior-rules) |
| Codex | plugin-creator 生成兼容清单 `.codex-plugin/plugin.json`；当前官方也提供 Agent Plugins 1.0 根 `plugin.json` 格式 | 本次选 plugin-creator 格式。本机该技能的 `validate_plugin.py::validate_optional_contract_path` 要求 `skills` 归一化为 `skills`，拒绝 `./`。这是该校验合同的实际结论，不扩推为所有 Codex loader 永远拒绝根目录。[官方构建说明](https://learn.chatgpt.com/docs/build-plugins) |
| CodeBuddy CLI | `.codebuddy-plugin/plugin.json`；`skills` 字符串或数组覆盖默认目录，技能布局为 `<name>/SKILL.md` | 已确认标准 `skills/` 目录；**未确认根直接技能的 loader 分支**，不以相对路径语法合法推断能发现根入口。生成 `./skills/`。[参考](https://www.codebuddy.ai/docs/cli/plugins-reference) |
| Cursor | 当前接受 Agent Plugins 1.0 根 `plugin.json`，也有 `.cursor-plugin/plugin.json` | 已调查，暂不生成清单；目录读取不证明 ICODE 宿主/MCP 适配。[官方说明](https://cursor.com/docs/plugins) |
| GitHub Copilot CLI | Agent Plugins 1.0 根 `plugin.json`；legacy 另接受 `.plugin/plugin.json` 等 | 1.0 固定扫描 `skills/` 的直接子目录，无自定义 `skills` 字段。此次不生成 Copilot 清单，也不把 Claude 格式被识别当作运行支持。[参考](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-plugin-reference) |
| Gemini CLI | 根 `gemini-extension.json`，`name`、`version`；技能位于 `skills/` | 已调查，暂不生成扩展；未验证扩展安装与 ICODE 完整流程。[参考](https://geminicli.com/docs/extensions/reference/) |

依据为当前官方 loader 行为说明及本机 Codex 校验器；未运行三宿主的实际发现器。
只有 CLI 参数、JSON 可解析或清单校验通过，均不能标为宿主实际发现通过。

## 单一来源与资源完整性

```text
新目录/icode/
  .claude-plugin/plugin.json
  .codex-plugin/plugin.json
  .codebuddy-plugin/plugin.json
  README.md / LICENSE / SHA256SUMS
  skills/
    icode/
      SKILL.md                  # 原样复制根入口
      steps/ references/ tools/ schemas/ templates/ agents/
      mcp/ scripts/ agent_runtime/ docs/
      skill-packs/               # 原始模板及 manifest，保持相对资源链
      integrations/codebuddy/    # 原命令桥资源快照，不自动启用
      install.sh 等根资源
    <manifest 中每个技能>/
      SKILL.md                  # 由对应 SKILL.md.template 原样转换
      其它资源
```

生成器复用 `validate_skill_pack.py` 的 manifest/路由校验和 `iter_source_publish_files` 转换规则。
完整性范围是根工作流公开资源及 `skill-packs/manifest.json` 声明的全部共享技能，当前 1+16 个。根技能的 `agents/openai.yaml` 一并携带，包与普通安装使用同一份发现元数据。
某些共享技能会引用用户另装的窄技能，这些不在声明集合内；不可用时按原路由降级并记录缺口。

安全规则：仅选 Git 跟踪文件，结合显式顶层资源白名单；排除网站、tests、demo、Git、宿主配置与未跟踪文件。
对选中文件及路径祖先拒绝 symlink，拒绝非普通文件、未合并项、隐藏私人路径、private/secret/credential、cookie 数据和实际 config。
保留公开 `config.example.json`、`.template`、两份已知 cookie 工具源码；这不是对任意文件内容的秘密扫描。
发布清单、README 和工作流全部取自 `--source` 指定 checkout，版本取自根 `SKILL.md`。
先建立并校验临时快照，成功后才创建输出；输出必须不存在、名为 `icode`、位于源码之外，父目录须已存在且无 symlink。
拒绝合并覆盖，磁盘写入失败可能留下新建的未完成输出；不会删除用户旧目录，重试应另选新父目录。

## 构建与检查

要求 Python 3.10+、Git；不需要网络、安装宿主或下载 Python 包。新文件须先加入 Git 跟踪。

```bash
# 从源码根执行；mktemp 只创建本轮新的临时父目录。
distribution_parent=$(mktemp -d /tmp/icode-distribution.XXXXXX)
python3 -B tools/build_skill_distribution.py --output "$distribution_parent/icode"
(cd "$distribution_parent/icode" && sha256sum --check SHA256SUMS)
python3 -B -m unittest discover -s tests -p test_skill_distribution.py -v
```

有 plugin-creator 技能的开发机，还应运行该技能随附的
`python3 -B <plugin-creator技能目录>/scripts/validate_plugin.py <新目录>/icode`。
该工具不是本仓库依赖，不嵌入生成器或运行时。Claude/CodeBuddy 的清单字段对照上述官方合同；
本轮没有执行它们的 CLI 校验或 loader，不宣称已获其运行时认证。

## 可发现与安装入口

用户现在的完整安装路线继续为源码 checkout 的 `bash install.sh --client claude|codex|codebuddy`。
先用 `--dry-run` 查看，再由用户在授权环境实际安装。生成包为后续插件分发准备可审查目录，
本轮不更改原 installer 或全局配置，也不让 installer 隐式登记市场/上报安装量。

后续获授权的插件试验可按宿主官方入口进行：Claude 的 `claude --plugin-dir <包目录>`；
Codex 的自定义 marketplace 接入与 `/plugins`；CodeBuddy CLI 的自定义插件市场。
本次没有创建 marketplace 清单，不提供指向未发布路径的远程安装命令。
共享源码或插件包不会自动进入所有用户的搜索目录；官方市场还涉及发布、审核或目录配置。
推荐排序由目录控制，不能通过添加清单承诺推荐。
参考：[Claude 本地测试与分发](https://code.claude.com/docs/en/plugins)、
[Codex 插件发现](https://learn.chatgpt.com/docs/plugins)、
[CodeBuddy 市场](https://www.codebuddy.ai/docs/cli/plugin-marketplaces)。

Claude 插件名 `icode` 加技能名 `icode` 对应 `/icode:icode`；原技能正文中的 `/icode` 仍保持原样。
CodeBuddy/Codex 入口以宿主实际列出的名称为准。技能间裸名称调用、旧全局安装共存、命令桥优先级尚需实测。
后续若发现必须改 workflow 才能正确解释插件名称，应独立申请扩大范围，不在分发层偷换入口语义。

自包含仅指源码/资源。包不声明 `mcpServers`、Hook 或自动安装；Python、MCP、DOCX runtime 和外部命令需另行准备。
包内 `install.sh` 保留源码快照，**不应直接从插件缓存运行**：旧同步程序依赖 Git 与全局安装约定。
完整安装请回到原源码 checkout；`--skip-mcp` 仍会准备 DOCX runtime。
源码工具的默认用户状态路径及插件缓存可写性也属于后续实测范围，不在本轮宣称已适配。

## 两阶段自审与验收边界

阶段一：先写失败测试，再实现最小生成器；检查标准布局、原样资源、模板转换、执行位、
拒绝已有输出、源/目标 symlink、敏感路径、缺资源、未跟踪排除、可重复构建。

阶段二：从源码与包结构反向检查来源和安全边界。发现 `--source` 未覆盖清单来源、
`private-data` 路径、`.py` 敏感命名、cookie 目录及模板后缀绕过后，以失败用例复现并修正。

本轮实测结果：新增测试 11/11，通过既有共享技能模板合同 6/6；真实 checkout 输出版本 2.32.0、
17 个技能，公开工作流文件原样复制，16 个共享技能经现有校验器摘要比对通过。
整合至 `9330c6c` 后复建含 354 个工作流文件、375 项 SHA256SUMS，摘要全部通过、包内 symlink 为 0；
该数量只是此提交快照，后续增补文档或工具时以实际包为准。Codex plugin-creator 随附校验器通过；
Claude/CodeBuddy 仅完成官方字段合同对照，不称为宿主 CLI schema 认证。

【架构级自检报告】

- ✅ 语法/编译：Python AST、JSON 解析、Codex 清单校验通过。
- ✅ 依赖/调用链：复用共享技能校验/转换；工作流公开资源与 16 个共享技能的完整性用例通过。
- ✅ 逻辑/边界：新目录、源码隔离、来源选择、重复构建用例通过。
- ✅ 异常处理：已测试缺资源、坏 manifest、symlink、敏感路径、已有目标拒绝；磁盘故障不宣称实测。
- ✅ 关联模块：只新增授权分发文件，原工作流、installer、网站和全局配置未改。
- ✅ 兼容安全：模板合同 6/6，包不注册 MCP/Hook；宿主入口兼容性仍待实测。
- ✅ 可运行性：离线生成器实际运行通过；不将此结论扩推到宿主执行。

允许交付结论：本地测试、静态清单/包结构和资源完整性通过。
不允许交付结论：任一宿主已实际安装/执行通过、MCP 已连通、已上架、搜索一定命中或一定被推荐。
