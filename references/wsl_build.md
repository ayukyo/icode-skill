# Windows 宿主上的 WSL 构建合同

仅在用户要求 WSL 构建，或 Windows 宿主需要 Linux 工具链/目标时加载。继续使用 code/verify 和已有工程接入、构建回执与验证记录；不增加公开命令、不替工程维护构建脚本，不自动安装/升级发行版或修改系统配置。

## 1. 固定执行环境与路径视图

区分代理宿主、命令执行环境和制品目标。例如 Windows 代理调用 WSL Linux 编译器，产物仍可能是 Linux x86_64 或 ARM64；运行于 WSL 本身不证明交叉编译。

先确认已安装发行版及其 WSL 版本，再按本次意图选择发行版与用户。多个候选且不能从用户/工程约束确定时停止选择，不依赖默认发行版。记录实际发行版、内核、host 架构、compiler 实际路径/版本/target triple、构建工具与目标架构。只查询已有工具；仓内构建入口仍必须静态读取，不能运行其 help/version 猜副作用。

路径记录保留 Windows 原始位置、对应 Linux 路径和实际构建 source/build/install 根。使用已选发行版中的 `wslpath` 转换并确认目标；不要自行替换盘符或假定 automount 根一定是 `/mnt`。UNC 的发行版必须与执行发行版一致。Windows MCP 仍使用 Windows 路径；WSL 进程使用 Linux 路径，不能把转换后的字符串冒充同一文件已核验。

命令通过参数数组传递；需 shell 编排时使用受控脚本/位置参数，不把用户路径拼进 shell 代码。固定工作目录、UTF-8 输入输出和实际进程退出码。按调用方向转换环境变量路径；所需编译环境只影响该进程，不覆盖用户 HOME、shell 配置或全局环境。

## 2. 源码、文件系统与快照

Linux 工具大量访问文件时，优先把构建目录放在发行版 Linux 文件系统。是否复制源码取决于构建副作用、性能需求和工程约束；不要给无写回的简单构建强加快照。若入口会改源码、生成链接或配置，或工程要求原 checkout 不变，则从经核验的当前候选建立独立 Linux 快照，包括需求内的未提交内容。

快照至少核对相对路径、普通文件内容 hash、符号链接及其目标、必要执行位和文件类型。区分真实 Linux symlink、Windows reparse point、普通文本占位和已解引用副本；不能只关闭 Git filemode 检查就认定迁移等价。发现大小写碰撞、CRLF 对脚本的影响、丢失链接或权限时明确阻塞对应路径，不转换原工程文件或静默排除参与构建的依赖。

ICODE 自身用 `.gitattributes` 固定 Shell/Python 脚本为 LF，避免 Windows Git 的 `autocrlf` 破坏 WSL shebang 或 Bash 语法。该规则只约束本技能仓库，不授予修改开发工程换行的权限；已有检出内容仍须核验实际字节。

固定原 checkout 的实际 Git 根/worktree/HEAD/dirty。Windows `.git` 指针、索引或 worktree 管理路径不一定可由 Linux Git 原样使用；保留原关联，可用 Windows Git 做只读来源查询，不能通过 repair/reset/重新初始化原仓来让构建通过。构建需要版本信息时按工程已有接口注入本次已核验身份，不把快照声称为实时工作区。

复制前后及构建结束核对原候选与快照输入。构建期间原源码变化、快照遗漏或链接越界时，本次产物不能认证当前候选；更新快照重新验证。外部链接须证明指向已允许的构建输入，生成写路径不得回流到原 checkout。

## 3. 工具链、缓存与测试

从工程约束/说明和已读构建入口取得实际 configure/build/install 目标。确认 Linux 编译器而非 PATH 中同名 Windows executable；分清 host 编译器、cross 编译器、sysroot 和 target 依赖。

Windows/MSVC 与 WSL/Linux 不共用构建缓存。发行版、source 根、生成器、编译器、工具链、sysroot 或目标架构变化时，使用新的构建目录或工程明确提供的安全重配置路径；不自动 clean 删除旧结果。核对实际 CMake cache、compile commands 和链接产物，不能只凭命令中写了 toolchain 判定选中。

交叉编译的 target ELF 未必能在 WSL host 执行。测试发现阶段也可能执行目标程序，例如 GoogleTest 动态发现；先核查 target 架构与已授权的 emulator/runner，不能把 Exec format error 当业务失败，也不能通过关闭所需测试制造通过。模拟执行、主机逻辑测试、目标设备实测分别记录，保留零用例/跳过的真实状态。

## 4. 制品与交付结论

复用 `build-start` 和 build provenance 绑定原候选、开始回执、实际命令、compiler、architecture、产物 SHA256；快照清单、配置和环境观测作为真实证据文件引用，不另造工单状态或未经定义的 metadata 字段。

回执中的文件路径必须能被控制面所在系统实际读取：Windows 控制面使用已映射的本地路径或 WSL 本地 UNC，WSL 控制面使用 Linux 路径；实际 configure/build argv 仍记录执行端使用的路径。两种视图间须有转换与文件身份观测，不把 Windows 不可见的 `/home/...` 字符串当作已核验制品路径。

核验制品真实格式、机器架构、导出与动态依赖；需要部署时再核对目标系统的 ABI/运行库及实际加载路径。打包/导出保留 Linux 链接及其目标，并确认传输后的 hash。Windows 可见文件不等于 Windows 可运行制品。

WSL 编译/host 测试只支持对应层结论，不能证明目标设备驱动、硬件节点、GPU/CUDA、USB/GMSL 或物理同步效果。设备、挂载、系统设置、重启、部署和故障注入仍走原授权边界。

## 5. 最小记录与缺失处理

| 记录 | 必要内容 | 缺失时的边界 |
|---|---|---|
| execution context | 宿主、发行版/WSL版本/用户、内核、host与target架构 | 不猜执行环境或目标 |
| path mapping | 原路径、Linux路径、source/build/install根及转换观测 | 不执行未核验路径 |
| source identity | 原候选、HEAD/dirty、快照内容/链接清单和前后比较 | 不认证当前源码 |
| build inputs | configure/build argv、compiler/triple、sysroot、cache与参与配置 | 仅保留已观察的局部构建结果 |
| test evidence | 实际二进制/runner、模拟标记、报告计数与退出码 | 不把编译/空跑当通过 |
| artifacts | 格式/架构/hash、必要依赖、导出/部署观测 | 不宣称设备运行或兼容通过 |

宿主本地 MCP 的平台默认路径策略见 [MCP 路径访问合同](mcp_path_access.md)；执行环境改变时，MCP 和构建进程各自使用自己的路径视图和权限配置，不能自动绕过另一端的显式白名单。

## 官方依据与验证边界

核对日期：2026-10-14。以下为平台行为依据，具体版本仍须读取本次环境；本文不是所有 WSL/发行版/工程的实测认证。

- Microsoft 建议 Linux 命令密集工作使用 Linux 文件系统，说明跨文件系统性能与环境变量桥接：[文件系统协作](https://learn.microsoft.com/en-us/windows/wsl/filesystems)。
- Windows/Linux 路径视图、UNC、`wslpath` 和可配置 automount 根：[WSL interop](https://learn.microsoft.com/en-us/windows/dev-environment/wsl-interop)。
- Windows ACL、DrvFS metadata、执行位与链接类型影响：[WSL 文件权限](https://learn.microsoft.com/en-us/windows/wsl/file-permissions)。
- 发行版、WSL版本和环境只读查询：[WSL 基本命令](https://learn.microsoft.com/en-us/windows/wsl/basic-commands)。
- CMake 的 cross 标志来自配置且不能独自证明目标架构；GoogleTest 动态发现需要能执行测试程序：[CMAKE_CROSSCOMPILING](https://cmake.org/cmake/help/latest/variable/CMAKE_CROSSCOMPILING.html)、[GoogleTest](https://cmake.org/cmake/help/latest/module/GoogleTest.html)。
