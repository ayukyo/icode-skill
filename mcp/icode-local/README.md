# icode-local

以一个 Python/FastMCP 进程提供五个本地模块的 27 个前缀工具及静态 `describe_capabilities`。业务代码仍在原兄弟目录，首次实际调用才加载，不启动旧 MCP 子进程。健康模块显式动态探针仍按原 trusted roots 规则执行。

`ICODE_LOCAL_CONFIG` 指向统一配置文件，格式为：

```json
{"module_configs":{"workspace":"/absolute/original/workspace/config.json"}}
```

模块键为 `workspace`、`evidence`、`index`、`health`、`policy`。各值仅引用原配置路径，独立保留 allowed roots、trusted roots 和索引 data dir；不合并配置权限。相对路径以统一配置文件目录为基准。显式原模块配置环境变量优先。未配置的模块读取原兄弟目录的 `config.json`，缺省时沿用原 defaults；显式指定的缺失、损坏或空路径配置均失败关闭。

迁移会拒绝原配置中依赖工作目录的相对 allowed roots、trusted roots 和 data dir。自定义相对 policy_file 在原业务目录发生迁移时也拒绝；标准 `policy.json` 仅在旧新权限等价时可使用新位置。请先在原配置中明确绝对路径，再重试。配置文件引用本身仍可按统一 profile 目录解析相对路径。

绝对路径也不能引用旧模块即将归档的 `.venv`、`.cache` 或 `__pycache__` 子树。preflight 会检查统一 profile、模块配置文件本身，以及活动索引 data_dir 和 policy_file；发现依赖后停止，不移动用户数据。父收敛器在归档前再次检查，避免准备之后配置变化造成数据库或规则文件失联。普通业务目录中的 `.cache` 不会仅因同名被禁止。

活动配置即使命名为 `config.example.json` 或其它发行文件，也不能在发布源码时被内容不同的文件覆盖。preflight 与实际 staging 均检查这些路径；发现冲突时停止，保留原配置与权限。`config.json` 的既有保留规则继续有效，policy 默认文件仅允许不改变权限的 transport 元数据更新。

安装继续使用共享 Python 安装器，只创建一份 venv。直接运行子安装器也会同步五个兄弟业务源码目录，保留原 config.json 和 runtime；`--full` 仅重建统一服务的 venv，保留统一 profile。父安装器将所选宿主冻结到 `ICODE_MCP_SELECTED_HOSTS`（逗号分隔）；独立安装默认 `claude`。准备接口：

```sh
python profile.py preflight --target /absolute/mcp/icode-local --hosts claude,codex
python profile.py setup-profile --target /absolute/mcp/icode-local --hosts claude,codex
```

`preflight` 只读，可用于首次安装不存在的目标目录。`setup-profile` 在注册前原子写入路径引用。两者只读取所选宿主，优先使用旧注册的原模块配置环境变量，其次从明确的原 Python 入口推导默认配置。宿主路径冲突时拒绝迁移，可逐宿主迁移到独立安装目录。原配置正文不复制、不写入日志。旧目录中可能被发行覆盖的 policy 权限若与新发行不一致，也拒绝迁移，须先将自定义规则放到独立配置数据位置。旧注册的退出由父收敛器在新注册验证后处理。

验证命令（需安装 requirements）：

```sh
python -m unittest discover -s mcp/icode-local/tests -v
python mcp/icode-local/tests/verify_stdio.py
```

stdio 验证采用临时 HOME，30 秒外层进程超时和 20 秒协议超时；检查 28 工具及有界代表调用。原 Windows 环境若 asyncio 的 socketpair 不可用，会明确报告超时，需在可用的 Python/WSL 环境验证，不能据此声明原宿主已验收。
