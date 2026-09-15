# 审查工作清单与定位证据（v1）

补强现有审查，不增加第二套 Agent/模型接口，不替代 LIMIT、TDD、四维复检、Reverse/Fixed/Free、Audit、Dedup 或交付验证。机器实现：[inspection_worklist.py](../tools/inspection_worklist.py)；数据合同：[inspection-worklist.schema.json](../schemas/inspection-worklist.schema.json)。借鉴 open-code-review 的确定性预处理、关联审查和定位校验思路，无需安装该工具。

## 范围与关联审查

`metadata.code_files` 是必审种子，不是影响面穷尽证明。先 Read 定稿/patch，按符号检索调用方、实现/头文件、测试与配置消费者，以可重复的 `--related <项目根相对路径>` 纳入真实关联文件。默认在种子父目录内枚举受影响 Git 仓 staged/unstaged/untracked 差异，联审同名头文件/实现/test；跨目录显式关联也必审。范围外改动列 exclusions，不吸收其它工单；扩大 Git 边界用可重复 `--scope <相对目录>`。

已知工单/上游基线时必须用 `--baselines-json '{"<仓库相对根>":"<真实ref>"}'`（根仓键 `.`），不得把默认 HEAD 冒充工单原基线。错误 ref 不回退；多仓独立解析，删除文件读取基线原文并标记 deleted。无 Git、不可读/binary/过大文件、预算或超时均记录 partial/unobserved，默认最多 200 文件、每文件 1MiB。LIMIT 优先，rules 是检查提示、不是已执行 lint。

仅工程内相对路径；拒绝越界、符号链接、工单控制目录和凭据/密钥。不执行构建、hooks、外部 diff driver 或模型请求。

每轮基线绑定已解析的 commit OID：同轮符号分支/tag 消失不改变证据版本；若该 commit 本身不可读，则保留失效边界重新入轮。crosscheck 新轮无显式覆盖时保留已声明的工单基线 OID，绝不静默退回另一 HEAD；新请求的错误 ref 仍硬拒绝。冻结清单先验普通文件/hash，再判断真实源码漂移，篡改不能伪装成 stale_input 来跳过。

## 正式工单接线

完成代码落盘并登记完整 code_files 后，在本 step 未结束 attempt 和真实 before_write check 下准备：

```bash
python3 tools/icode_control.py inspection --dir <out_dir> --step <code|deepcheck|audit> \
  --attempt <attempt> --phase prepare [--related <file>] [--scope <dir>] [--baselines-json '<object>']
```

生成 `code_worklist.json`、`deepcheck_worklist.json` 或 `audit_worklist.json`。prepare 不填 Read，不证明理解；同 attempt 恢复保留记录。逐单元共同审查所有 files，实际重新 Read 本阶段原文（删除文件读基线）后才登记：

```bash
python3 tools/icode_control.py inspection --dir <out_dir> --step <step> --attempt <attempt> \
  --phase read --read-phase <phase> --path <相对文件>
python3 tools/icode_control.py inspection --dir <out_dir> --step <step> --attempt <attempt> --phase check
```

phase：code=`code_review`；deepcheck effective full=`reverse/fixed/free`，实际 fast=`reverse`（风险升级 full 不可减阶段）；audit=独立 `audit`。本轮每文件每阶段阅读绑定冻结 sha256，不能复用上一步、上一轮或另模型阅读。prepare/read 自动登记 artifact 回执；手改 findings/债务后须重新 `artifact --path <step>_worklist.json`。成功 finish 强制完整清单+当前 attempt 真实回执；源码/基线漂移需 blocked 重入并重新审查，不可只改 hash。

遗漏保持 `coverage_status=partial|degraded` 和 `unobserved=[{"path":"<遗漏范围>","debt_reason":"<原因>"}]` 或明确全局 debt_reason，不允许 success/“全审完成”。degraded 可保留明确债务，但身份/路径/版本违规不能降级。deepcheck/audit 原有 coverage.json 与 Dedup 声明仍必需，清单不能代替它们。无 execution_model 事件的历史工单保持 untracked 可读，不追溯补写证据。

## Finding 定位与版本

本轮发现写入清单 findings，与 Markdown 用相同稳定 finding_id。确认的源码 finding 给 `verification_status=confirmed` 和 `locations`，每项含 `path,start_line,end_line,source_sha256,excerpt`。行号从 1 起；excerpt 与对应完整行段逐字相同（含换行）。校验单元成员、行段、hash 和原文；定位正确不等于语义正确，主代理仍复核因果/建议。

无法定位的源码猜测必须 `verification_status=needs_more_evidence`、`locations=[]`、明确 `evidence_boundary`，不能写为确认缺陷。非源码设计/日志/文档建议可空位置但须说明证据和边界，禁止编造 file:line。历史 resolved 不借旧位置冒充本轮确认；其余历史生命周期需当前定位或 needs_more_evidence。

## 独立 crosscheck

每新 round 独立生成 `crosscheck_round_N.worklist.json`，仅写 crosscheck 容器，不调用工单 writer。start 返回 inspection_worklist；实际独立 Read 后：

```bash
python3 tools/icode_crosscheck.py inspection --dir <crosscheck_dir> --round <N> --phase read --path <file>
python3 tools/icode_crosscheck.py inspection --dir <crosscheck_dir> --round <N> --phase check
```

fresh findings 同样要求定位/边界。freeze 同时冻结清单 hash；缺阅读仅能 blocked，不当完整通过。冻结后不改清单；历史比较新增确认位置须来自本轮已审单元，否则 needs_more_evidence 保留，下一轮扩范围。新轮清空阅读，未冻结同轮恢复保留。旧轮无 inspection_version 时只读兼容、不补写；历史证据验自身 hash，不拿今天源码否定历史。原工单/索引/代码零回写，UI/Runtime 不新增 crosscheck。
