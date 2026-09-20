# Skill 目录接入：自动发现与待提交材料

ICODE 仍只有一个公开真源和一套完整安装器。本文区分“已具备发现入口 / 可以检查 / 待提交 / 已收录 / 完整运行”，不是六个平台均已上架的声明。

## 已接入的发现与复查

| 渠道 | 仓库侧支持 | 不能由本地修改代替的环节 |
| --- | --- | --- |
| SkillsMP | 标准根 SKILL、双语能力描述、公开仓库与用途；探针核对 GitHub 来源 | 平台抓取与刷新；已有条目的关键词排名 |
| skills.sh / find-skills | 源仓库可被 Skills CLI 列出；标准元数据、完整安装说明与查询探针 | 平台自身收录与排序；不模拟安装或制造遥测 |
| Context7 Skills | 标准源与只读查询适配，无 ctx7 运行依赖 | 弃用中的 CLI/注册表未来可用性，平台收录 |
| 腾讯 SkillHub | 搜索适配；版本跟随源码的专用 frontmatter 草稿 | 账号、实名/发布资质、凭据、完整包与官方 dry-run、审核 |
| ClawHub | 搜索适配；同名条目不当作本仓库 | 登录发布、包/宿主验证、MIT-0 许可条件须权利人确认；不擅自修改本仓库 MIT |
| Smithery Skills | 搜索适配；公开 GitHub 来源的提交请求草稿 | 命名空间归属、API key、平台条款与发布授权 |

公开查询的端点、样本、弃用提示和官方来源见 [发现矩阵](agent-skill-discovery.md)。Glama 本轮未确认独立 Skill 提交合同，暂不适用；不为进入 MCP 目录制造空服务。原有第三方 awesome 目录的问题沿用已有上游 issue，不重复刷投稿。

## 生成专用资料草稿

```bash
# 无网络、无凭据读取、无文件写入；JSON 仅输出到终端
python3 tools/prepare_skill_listing.py
python3 tools/prepare_skill_listing.py --platform skillhub
python3 tools/prepare_skill_listing.py --platform smithery
```

工具只读取根 `SKILL.md`，复用公开输入的大小与 symlink 检查；版本必须是唯一的稳定 `X.Y.Z`。没有独立维护第二个版本号。公开描述源自同一入口；不会扫描工单、配置、环境变量或未跟踪资料。

- **SkillHub**：输出其官方要求的 `slug/version/displayName` 和建议的摘要、描述、标签、MIT、主页。这些字段只用于后续独立 staging 副本，不加到通用根技能中。草稿不含完整技能包；ICODE 依赖步骤、工具和共享技能，不能把仅有元数据的文件当成可安装技能上传。先解决完整资源与宿主入口验证，再使用官方 CLI 的 dry-run。[官方发布字段](https://skillhub.cn/ai/release.md)
- **Smithery**：输出方法、目标 URL 与 `gitUrl` JSON body，不生成 Authorization 头，不读取或要求用户把 key 发给 Agent。目标命名空间仅按公开仓库作者准备，必须先验证实际归属与可用性。工具不会发送该请求。[官方提交 API](https://smithery.ai/docs/api-reference/skills/create-or-update-a-skill)
- **ClawHub**：不生成会隐含同意 MIT-0 的发布包或命令。保留当前许可；若权利人未来明确同意，再独立检查所含代码/依赖的授权与包完整性。[官方许可与发布说明](https://github.com/openclaw/clawhub/blob/main/docs/cli.md)

每份输出明确标注 `draft_only`、`submitted: false`、`publish_ready: false`，并列出剩余前提。这不是保守地隐藏已完成工作，而是避免把本地合法 JSON 当成平台审核、宿主执行或收录成功。

CI 只做离线合同测试，不带市场凭据、自动投稿、注册、修改许可或定时刷查询。`main` 自动更新的是官网及公开阅读材料；目录抓取与推荐不受本仓库控制。

## 平台接入的完成条件

1. 官方格式与平台身份确认，实际 payload 来源与版本可追溯。
2. 完整资源/共享技能/宿主命名空间/外部依赖验证，不以元信息通过代替运行。
3. 用户提供相应发布授权；账号、凭据、实名、许可同意按平台要求处理。
4. 提交后获得真实条目 URL，再只读核对作者及源仓库。
5. 分别检查名称词与用途词；报告有限查询结果，不承诺推荐排名。

正式安装路线和分发包原型限制仍见 [技能分发](skill-distribution.md)，不会改变 Claude Code、Codex、CodeBuddy 的现有使用方式。
