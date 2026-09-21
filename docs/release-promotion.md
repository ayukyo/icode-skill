# Release 自动推广（第一阶段）

ICODE 的第一阶段推广能力只由正式 GitHub Release 或人工预览触发，不在每次提交后发布，也不读取工单、demo、私有日志或任意 Release 正文来生成营销内容。公开文案由仓库内受审查的固定模板生成，Release 只提供版本、发布时间和官方链接。

## 工作方式

`.github/workflows/release-promotion.yml` 有两个相互分离的任务：

1. `plan`：无外部写入，校验 Release 身份，生成确定性的推广计划和 dry-run 回执。
2. `publish`：仅当 `PROMOTION_ENABLED=true`，并且事件是正式 `release.published`，或默认分支上的人工运行明确关闭 `dry_run` 时执行；任务绑定 `release-promotion` environment。

默认渠道为 `github,bluesky,mastodon`。每个渠道独立判断凭证：缺失凭证记录 `skipped/missing_credentials`，不会伪装成成功，也不会阻断其它已配置渠道。GitHub Discussion 使用计划中的隐藏 campaign marker 去重；Bluesky 使用稳定 rkey 覆盖同一条记录；Mastodon 使用稳定 `Idempotency-Key`。

发布回执只包含渠道、状态、公开 URL 和固定错误码，不包含手机号、邮箱、密码、令牌、响应正文或异常细节。`published` 只表示平台接口接受了内容，不代表搜索引擎已经收录、产生排名或获得推荐。

## 首次配置

优先使用平台提供的 GitHub OAuth 注册；不支持时再使用账户自己的邮箱或手机号。个人标识、验证码和令牌只在对应平台或 GitHub Secrets 中配置，不写入仓库文档。

仓库 Actions variables：

| 名称 | 值 | 说明 |
| --- | --- | --- |
| `PROMOTION_ENABLED` | `true` | 总发布开关；未设置时只有 dry-run |
| `PROMOTION_CHANNELS` | `github,bluesky,mastodon` | 可按已配置账户删减 |
| `PROMOTION_DISCUSSION_CATEGORY` | `Announcements` | GitHub Discussions 中已有的分类名 |
| `MASTODON_BASE_URL` | 例如 `https://mastodon.social/` | 必须是无端口、无子路径的公开 HTTPS 实例 |

仓库 Actions secrets：

| 名称 | 说明 |
| --- | --- |
| `BSKY_HANDLE` | Bluesky 账号标识；虽是公开值，也按 secret 管理以简化日志边界 |
| `BSKY_APP_PASSWORD` | Bluesky 专用 App Password，不使用账户主密码 |
| `MASTODON_ACCESS_TOKEN` | 仅授予发布状态所需权限的用户令牌 |

GitHub 渠道使用工作流自动提供的短期 `GITHUB_TOKEN`，不创建个人访问令牌。仓库必须先启用 Discussions，并存在所配置的分类。工作流只申请 `contents: read` 与 `discussions: write`，没有代码提交权限。

在仓库 Settings → Environments 创建 `release-promotion`。首次启用建议设置 required reviewer；完成若干轮真实发布验收后，才考虑取消逐次审批。外部平台凭证只暴露给这个 environment 的 `publish` 任务，PR 和普通 push 均不会触发该工作流。

## 本地零写入验证

```bash
python3 -m unittest tests.test_release_promotion tests.test_release_promotion_workflow -v

python3 tools/release_promotion.py plan \
  --release-json /tmp/release.json \
  --repository ayukyo/icode-skill \
  --site-url https://ayukyo.github.io/icode-skill/ \
  --output /tmp/promotion-plan.json

python3 tools/release_promotion.py publish \
  --plan /tmp/promotion-plan.json \
  --channels github,bluesky,mastodon \
  --output /tmp/promotion-receipt.json
```

不带 `--submit` 永远是 dry-run，代码路径不进行网络请求。真实写入只允许在受保护的 Actions 环境中显式增加 `--submit`；不要在开发机命令历史中传递凭证。

## 失败与重跑

- `github_category_not_found`：启用 Discussions，并创建或修正 `PROMOTION_DISCUSSION_CATEGORY`。
- `missing_credentials`：该渠道安全跳过；补齐变量/secret 后人工 dry-run，再执行发布。
- `network_error`、`http_*` 或 `*_publish_failed`：保留回执，检查平台状态和令牌权限；不要连续重跑制造重复内容。
- `plan_integrity_mismatch`：计划被改写或来源不一致，必须重新从 Release 元数据生成，不能绕过。

Bluesky 和 Mastodon 使用确定性标识，重跑同一 Release 不应创建新的重复消息；GitHub Discussion 根据隐藏 marker 复用已有公告。仍应先看已有回执和平台页面，再决定是否重跑。

## 当前边界

第一阶段不包含 X、LinkedIn、DEV、Hashnode、Reddit、Hacker News 或 Product Hunt。也不自动注册外部账户、不接受付费、不自动回复/点赞/关注、不抓取用户数据。后续阶段可在独立适配器、独立凭证和同样的 dry-run/幂等门禁下逐项接入。
