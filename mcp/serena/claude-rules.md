<!-- serena-doctor:rules-start -->
   serena LSP 语言不全时（如 C++ 不可用）：
   a. 优先调 `serena-doctor fix <project_path>` 补全配置
   b. 修复后重试 serena 检索
   c. 修复失败或不可用时，才降级 ripgrep/grep 并显式声明

   serena 激活路径≠当前工作目录时：
   a. 先用 serena 的 activate_project 工具切换到当前项目
   b. 切换后仍不可用再调 serena-doctor 诊断

   目标代码在子仓库/嵌套 git 仓库（实际 git 根 ≠ 当前激活项目）时：
   a. 先 `serena-doctor init/fix <子仓库路径>` 补子仓库 serena 配置
   b. 再 `activate_project(<子仓库路径>)` 激活目标仓库后检索
   c. 激活失败才降级 ripgrep/grep 并显式声明（icode v2.12 多 git 根激活）

   serena-doctor 是项目级 serena 配置治理工具（~/.local/bin/serena-doctor）：
   - serena-doctor init <path>     新建项目配置
   - serena-doctor fix <path>      已有项目补全语言
   - serena-doctor --all           批量修复所有已注册项目
<!-- serena-doctor:rules-end -->