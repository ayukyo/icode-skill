# 工程接入与超大仓扫描合同

本文件是所有 workspace-scoped ICODE 入口的工程根解析与静态画像真源。目标是让用户可以从工程根、工程子目录或“只包含一个实际仓库的容器目录”发起工作流，同时避免误选仓库、无界扫描和通过执行构建脚本来猜命令。

## 1. 触发时机

以下任一成立时，先执行本合同，再创建/查找工单或扫描源码：

- 当前路径不在 Git 仓库内；
- 用户给了一个工程路径，但实际 Git 根未知；
- 工程包含 SDK、内核、bootloader、RTOS、rootfs、工具链或固件镜像；
- 跟踪文件数超过 50,000，或普通全仓检索明显超时/输出截断；
- 构建、打包、烧录、OTA、清理命令的副作用边界未知。

## 2. 工程根解析（fail-closed）

优先调用 `icode-workspace.resolve_project(path, max_depth=2)`：

1. 路径位于 Git 仓内：使用当前 checkout 的 Git 根，模式为 `current_git`。
2. 路径不在 Git 仓，但深度 2 内只有一个 Git 根：使用该根，模式为 `unique_nested_git`，并向用户显示“请求路径 → 实际工程根”。
3. 路径自身是 `.repo/manifest.xml` 管理根：保留 repo 根，模式为 `repo_manifest`。
4. 发现多个 Git 根：L1 阻断并列出候选，禁止按目录名、mtime、分支名或“最像项目”的仓库猜测。
5. 扫描目录数/仓库数预算耗尽：L1 阻断；预算耗尽不等于“只有一个仓库”。
6. 无 Git/.repo：仅支持明确允许 non-git 的只读/资料类入口；代码实施、worktree、构建和交付验证不得伪装成 Git 工程继续。

这套“唯一嵌套 Git 根才自动选择”的规则不提供模糊匹配或名称猜测旁路。

解析完成后，后续 step 文档里的 `cwd`、`WORKSPACE_ROOT`、`GIT_ROOT`、`project_path` 和 `<project_root>` 默认都指已解析的实际工程根。若宿主不能切换 cwd，则每个命令显式使用该根作为 `workdir` 或 `git -C <root>`，不得继续在容器目录创建 `.icode_output/`。

MCP 不可用时可用只读 `git -C <path> rev-parse --show-toplevel` 与有界 `find <path> -maxdepth 3 -name .git` 复核，但仍执行同一“唯一才选、多根/截断即阻断”语义，并记录 `degraded_after_attempt`；禁止退化成 `pwd` 后静默把容器目录当项目。

## 3. 静态工程画像

嵌入式或大仓命中时调用 `icode-workspace.inspect_project_profile(path, max_depth=2)`。它只允许读取：

- 固定参数的只读 Git 命令（如 `rev-parse`、`status`、`ls-files`、`worktree list`、`submodule status`）；
- `git ls-files` 返回的受控路径清单；
- 数量受限、字节受限的根级/一级构建入口文本。

它不得运行仓内 `build.sh`、Make target、Python/Perl 工具、二进制、`--help`、`--version` 或所谓 dry-run。构建脚本可能在参数解析前复制配置、创建链接、清理目录或碰设备，`--help` 不是天然只读证明。

画像输出只提供：规模等级、跟踪文件数、顶层分布、平台层提示、工具链/固件提示、构建入口风险证据和建议扫描策略。平台层是候选边界，不是自动排除项；README、工程 limit、活动 defconfig/board config 和真实调用链仍是范围真源。

## 4. 扫描预算与范围

| 规模 | 默认策略 | 内容文件软预算 | 默认允许全树内容扫描 |
|---|---|---:|---|
| normal（<50k tracked） | 常规 `rg`，仍先精确关键词 | 2000 | 是 |
| large（50k~149,999） | 先 `git ls-files` 选路径，再按模块检索 | 500 | 否 |
| huge（≥150k） | tracked path 枚举 → README/构建根/活动配置 → 目标模块 | 200 | 否 |

软预算不是“读够数量就宣布完成”。达到预算时输出 `truncated/unobserved`，再按当前问题缩小目录、文件类型、符号或提交范围；不得自动扩大到整个 vendor SDK。二进制、生成目录和历史文档只在有明确证据用途时读取，且采用 hash/manifest/抽样，不把字符串命中当实现支持。

## 5. 嵌入式聚合仓的最小基线

进入 plan/code/deepcheck/audit/verify 前至少固定：

- 实际 Git 根、branch、HEAD、upstream、dirty；
- 产品/板型/SoC、活动 defconfig 或等价配置；
- build root、顶层编排入口、实际目标与交叉工具链；
- bootloader/kernel/DTB/rootfs/RTOS/TEE/应用中本次参与的层；
- 源码 → 构建输入 → 分区/镜像 → 部署槽位 → runtime loaded object 的可证明边界；
- Host 编译、镜像打包、设备送达、设备加载、业务消费/物理效果分别处于何种证据层。

画像命中嵌入式层时复用 `embedded-runtime-provenance`；命中厂商 SDK/工具链/镜像来源时复用 `multi-repo-artifact-provenance`；命中传感器/ISP/media/V4L2 时再路由 camera 类技能。静态画像只能触发路由，不能替代这些技能的证据合同。

## 6. 构建与清理安全

- 编译命令真源顺序：工程 limit → 根 README/构建说明 → 活动配置与脚本静态阅读 → 用户确认/受控执行。
- 任何 `clean/cleanall/distclean/rm -rf`、镜像重打包、配置同步、符号链接生成、烧录、OTA、设备重启都登记副作用和影响路径。
- 未证明输出边界前，不运行 clean；未固定制品 identity/目标设备/分区或槽位前，不部署或烧录。
- 构建成功只证明该 build root/目标在 Host 侧完成；不得据此升级为设备加载、摄像头出流、业务消费或现场验证通过。
