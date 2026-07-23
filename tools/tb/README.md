# tools/tb - Teambition 缺陷单拉取（icode log 步骤的可选数据源）

通用、参数化的 Teambition 缺陷拉取层，供 icode `/icode log` 步骤在**零散输入含 TB 引用时**可选调用，
把缺陷单的标题/描述/评论/日志附件拉到本地作为日志根因分析的输入。

> 本目录只做「拉取」，**不做回写**：不向 TB 发任何 POST，不生成评论草稿。分析结论产出在
> `{ICODE_OUT_DIR}/log_analysis.md` + `00_init.md`，供人工审阅，绝不自动回写 TB。

## 文件

| 文件 | 职责 |
|------|------|
| `scripts/tb_pull.py` | `list` 列缺陷；`defect <LIB-NUM>` 拉详情+真实评论+下载日志附件，写 `<ID>_meta.json` |
| `scripts/tb_cookie.py` | 解密 Chrome cookie -> `scripts/.tb_cookie`（也可手动粘贴 cookie） |
| `config.example.json` | 配置模板（占位）。复制为 `config.json` 后填真实项目 |

## 依赖

```
pip install requests cryptography secretstorage
```
- `requests`：tb_pull 必需。
- `cryptography` + `secretstorage`：仅 tb_cookie.py 的 Chrome 自动解密需要；手动粘贴 cookie 可不装。

## 首次配置

**config 可选**：`/icode log <TB URL> <单号>` 用法不配 config 也行--AI 从 URL 抽 domain+pid 传 `--domain --pid`。config 仅在多项目 lib 快捷（`--lib`）或固定 domain 时更省事。

1. **建 config（可选）**：`cp config.example.json config.json`，填 `domain` 和 `projects`（见下）。不建也行，用 `--domain` 传域名。
2. **取 cookie**：在 Chrome 登录 `https://<你的TB域名>/` 后，二选一：
   - 自动：`python3 scripts/tb_cookie.py --domain <你的TB域名>`
   - 手动：从浏览器 DevTools 复制 `<你的TB域名>` 的 Cookie 请求头，粘进 `scripts/.tb_cookie`（单行 `name=value; name=value; ...`）

## 多项目配置（文本配，加项目不改代码）

`config.json` 的 `projects` 是字典，**加一个项目 = 加一个 key**：

```json
{
  "domain": "<你的TB域名>",
  "projects": {
    "DEMO": {
      "pid": "<项目ID，从TB项目URL取>",
      "label": "<项目名称，如 XXX项目测试缺陷管理库>",
      "url": "https://<你的TB域名>/project/<项目ID>"
    },
    "PROJ": {
      "pid": "<另一个项目ID>",
      "label": "<另一个项目名称>",
      "url": "https://<你的TB域名>/project/<项目ID>"
    }
  }
}
```

- `key`（如 `DEMO`）：缺陷库前缀，须大写字母，用于 `<LIB>-<NUM>` 形式（如 `DEMO-26`）。
- `pid`：项目 id，从 TB 项目 URL 里取（`<TB域名>/project/<pid>/...`）。
- `label`：项目名称，人读 + 按名匹配用。
- `url`：TB 项目地址，可选，登记与溯源用。

## 命令（调试 / 单独用时）

```bash
# 列缺陷
python3 scripts/tb_pull.py --lib DEMO list
python3 scripts/tb_pull.py --lib DEMO list --status all --json

# 拉单个缺陷（下到 config.log_root/<ID>/）
python3 scripts/tb_pull.py defect DEMO-26

# 拉一个 URL 带来、未在 config 登记的项目（--pid 权威）
python3 scripts/tb_pull.py --domain <你的TB域名> --pid <项目ID> defect DEMO-26 --out ~/work/log
```

`defect` 产物：`{out_root}/<ID>/` 下是日志附件 + `<ID>_meta.json`（title/note/真实评论原文/附件清单/下载清单）。

## 被 icode log 步骤调用时

`/icode log` 在阶段0 输入收敛时，若零散输入含 Teambition 项目 URL（含 `/project/<pid>/` 路径）或 `<LIB>-<NUM>`，会自动：

```
python3 tools/tb/scripts/tb_pull.py --pid <URL里的pid> defect <LIB>-<NUM> --out {ICODE_OUT_DIR}/tb_source
```

附件落到 `{ICODE_OUT_DIR}/tb_source/<ID>/`，作为本次日志根因分析的输入目录，并在 `.ico_metadata.json`
记 `tb_source` 溯源字段。**零散输入无 TB 引用时整段跳过，log 步骤走纯本地日志路径，行为不变。**
