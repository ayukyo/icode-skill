# icode-workspace

免 Key、只读的工作区来源 MCP。统一观测 Git 根、分支、upstream、dirty 状态、worktree、submodule、`compile_commands.json` 和 ELF/二进制身份；不会 fetch、merge、checkout、commit 或 push。

新增两类工程接入能力：

- `resolve_project`：当前路径不在 Git 仓时，只在有界深度内选择“唯一嵌套 Git 根”；多根、扫描预算耗尽都拒绝猜测。
- `inspect_project_profile`：只读 `git ls-files` 与少量构建入口文本，输出仓库规模、平台层提示、工具链/固件提示、构建副作用证据和扫描预算。它不会运行 `build.sh`、`make` 或任何仓内程序。

平台目录只作为候选边界，不会自动排除；真正修改范围仍须结合 README、工程约束和当前需求确认。超大仓默认先枚举 tracked paths，再对目标模块做限量内容检索。

默认允许 `$HOME`（当前用户目录）。Windows 未设置 `HOME` 时会使用系统用户目录，通常由 `USERPROFILE` 决定；无需手动设置 `HOME`。

工程在用户目录以外时，需要在安装后的 `config.json` 的 `allowed_roots` 中显式添加工程根目录，例如 `"D:/Projects"` 或 `"/srv/projects"`。路径列表只允许访问这些目录及其子目录，不会自动开放整个磁盘。共享路径规则见 [配置路径说明](../_lib/README.md)。
