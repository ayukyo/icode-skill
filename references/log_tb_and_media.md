# 日志分析：TB 源、媒体附件与批量路径（按需加载）

> **本文件由 [steps/log.md](../steps/log.md) 按触发条件外提**，只在命中下列任一条件时读取；纯本地日志分析且无媒体附件时**整份跳过**。
>
> | 条件 | 读取段落 |
> |------|----------|
> | 零散输入含 TB 项目 URL 或 `<LIB>-<NUM>` | 「TB 缺陷源拉取」「TB 评论等回复机制」 |
> | 日志目录或 TB 附件含视频/图片 | 「附件分析…」 |
> | 步骤1 检测到同 `lib+num+pid` 旧工单且用户选复用 | 「同 TB 单复用流程」 |
> | 零散输入含批量意图（"分析所有TB/全部打开单"） | 「批量 TB 分析」 |

## TB 缺陷源拉取（可选前置，仅当零散输入含 TB 引用）

**触发条件**：零散输入里出现 `tb.example.com/.../project/<pid>/...` 这类 TB 项目 URL，或 `<LIB>-<NUM>` 形式（如 `DEMO-26`）。**无 TB 引用时本段整段跳过，log 步骤走纯本地日志路径，行为与改前 100% 一致**（纯本地日志分析，不调任何脚本/网络）。

**目标 UX**：`/icode log 分析 https://tb.example.com/project/<pid> DEMO-26 问题` -> AI 解析 -> 拉取 -> 进既有对抗根因分析流程 -> 出报告，**全程不回写 TB**。

执行（在阶段0 内，目录已建之后、强制思考之前）：

1. **解析三要素**：从零散输入抽 `<LIB>-<NUM>`（缺陷编号）、URL 里的 `pid`（项目 id）、项目名/label。项目解析优先级 `URL pid > config label 匹配 > config lib 前缀`
2. **调 tb_pull**：`python3 ~/.claude/skills/icode/tools/tb/scripts/tb_pull.py --domain <域名> --pid <pid> defect <LIB>-<NUM> --out {ICODE_OUT_DIR}/tb_source`。**domain+pid 从 URL 抽**（pid 取 /project/<pid>/ 那段；支持不配 config；`--pid` 权威，支持未在 config 登记的项目）。**domain 抽纯域名（剥掉 https:// 和路径）**；即便误传带 scheme 的 URL，tb_pull 也会自动剥掉并打 [warn]，拉取失败时会给出 DNS/连接/超时的可读原因
3. **落盘与三要素绑定**：附件 + `<ID>_meta.json` 落到 `{ICODE_OUT_DIR}/tb_source/<ID>/`；置 日志目录=`{ICODE_OUT_DIR}/tb_source/<ID>/`、问题描述=缺陷 title+note、时间点=从拉取日志内容推断
4. **鉴权失败（401）**：提示用户跑 `python3 ~/.claude/skills/icode/tools/tb/scripts/tb_cookie.py`（或手动把浏览器 cookie 粘进 `~/.claude/skills/icode/tools/tb/scripts/.tb_cookie`），**不阻塞**--若用户只想本地分析可放弃 TB 源、退回纯本地日志路径继续
5. **溯源**：把 TB 源信息（`lib`/`num`/`pid`/`label`/`url`/`meta_path`）记入步骤9 metadata 的 `tb_source` 字段；在 `keywords` 里带上缺陷编号（如 `DEMO-26`），便于后续历史检索按缺陷号命中相似工单

> 拉取产物（`{ICODE_OUT_DIR}/tb_source/<ID>/` 下的日志附件 + `<ID>_meta.json` 里的真实评论/描述）即作为阶段2「日志侦察 + 现场还原」的输入，走既有对抗根因分析流程，与本步骤下游各阶段无缝衔接。

**附件本地镜像（防 gvfs SMB 后端 fd 泄漏崩溃，长期方案；TB 源分析适用，纯本地日志路径跳过）**：
TB 附件已落盘后，若 `{ICODE_OUT_DIR}` 位于**网络挂载（SMB 等）**，将 `tb_source/<ID>/` **整体复制到本地临时镜像**，后续所有"**读附件**"动作一律读本地镜像——避免 claude 分析期对 SMB 单点代理 `gvfsd-smb` 的高频小文件读（其 fd 泄漏累积会耗尽并拖垮整个 gvfs 挂载，实测 gvfsd 主进程 `Too many open files` 崩溃、全部 SMB 挂载端点断开）。

- 定义：`LOCAL_TB_SRC=/tmp/icode_attach_mirror/<project_id>/<outdir basename>/tb_source/<ID>`（`project_id` 见前置逻辑；`<outdir basename>` 取 `{ICODE_OUT_DIR}` 的 basename，如 `.icode_output_7`）
- 执行：`mkdir -p "$(dirname "$LOCAL_TB_SRC")" && cp -r "{ICODE_OUT_DIR}/tb_source/<ID>" "$(dirname "$LOCAL_TB_SRC")/"`
  - 复用/续跑场景附件已下载同样复制本地（「同 TB 单复用流程」重拉后同样重建镜像，见该段步骤3）
- **替换规则**：本段之后凡引用 `{ICODE_OUT_DIR}/tb_source/<ID>/` 的**读取**（Read 日志/评论/附件、解压 tar/grep、ffmpeg/vision-bridge 输入）一律改用 `LOCAL_TB_SRC`；**写产物**（`log_analysis.md`、简报、关键帧、metadata）仍写回 `{ICODE_OUT_DIR}`（低频少量，SMB 压力可忽略）
- 清理：步骤9 metadata 落盘后 `rm -rf "$(dirname "$LOCAL_TB_SRC")"`（/tmp 重启即失，防残留占盘）`<ID>_meta.json` 的评论文本在 `comments[].content.comment`（**非 `comments[]` 直接为字符串**，每条评论是对象），`note` 为缺陷描述，均可作为症状补充证据（评论里常含复现步骤/现象描述/关键时间点/日志原文片段，须逐条研读不漏、回捞进现场时间线，详见步骤6「TB 评论研读」；**评论里的根因结论/修复手段只作参考启发、不得直接采信为根因事实**，见步骤6「⚠️ 评论不盲信」）。

## TB 评论等回复机制（防"过早出报告"）

**触发条件**（TB 缺陷单含以下任一信号即适用）：
- 标题/描述含"软件协助分析/需要排查/近期出现多次"等求助性表述
- 评论中有"@XX 看下"等指向特定工程师的指派
- 已上传日志但缺关键模块（log 缺失 = 还需要补）

**执行动作**：
1. 拉取 TB 数据后，**先看评论时间戳**--若最后一条评论距今 < 2 小时，提示用户"工程团队可能还在分析，建议等待 N 小时后再生成完整报告（治本根因可能在后续评论中补充）"
2. 给用户两个选项：
   - **A. 立即出报告**（当前信息可能不完整，治本根因可能尚未浮现）
   - **B. 延迟出报告**（等工程团队回复后增量拉取再生成，按「同 TB 单复用流程」增量对抗）
3. 若用户选 A，在 `log_analysis.md §0` 硬约束段标注：
   ```
   ⚠️ 本报告基于 TB 评论截至 <时间戳> 的数据；治本根因可能在后续评论中补充，
   建议在 <建议时间> 后重拉 TB 增量分析
   ```
4. 若用户选 B，中止本次出报告，提示"待工程团队回复后重跑 `/icode log` 复用旧目录增量分析"

> **为什么必须等回复**：对于需工程团队深度分析的缺陷单，过早出报告等于把自己锁在没有完整信息的盒子里（实战教训：v1 报告因未等 TB 新评论，治本根因留空缺，后续被增量补全部分推翻）。

## 附件分析（含本地路径 + TB 源）、能力路由与 ffmpeg 抽帧

**核心原则**：先枚举媒体并固定 hash，再按 [media_routing.md](media_routing.md) 选择 native/bridge/dual/text_only。明确支持视觉的 GPT 等 session 不因 bridge 已安装而降级；纯文本或能力未知 session 禁止试探性注图，使用 bridge 或保留视觉缺口。视频先抽帧，密集图片按需分块。

**为什么必须先完成媒体路由**：

1. **附件视频/图片是"零号病人"指引**——含明确时间点（界面时钟）和现象描述，先看附件再看日志可避免按错误时段跑多轮误判
2. **通道能力不同**——bridge 已安装只说明可调用，不说明比当前 session 更强；路由必须保留强 native 并保护纯文本会话
3. **ffmpeg 省钱**——视频不直接传 vision-bridge（费 API 额度），先用 ffmpeg 本地提取关键帧（免费），再传图片帧（省额度）

**前置判定（两步）**：

1. **枚举附件**（按来源分两种模式）：
   - **TB 源模式**：Read `<ID>_meta.json` 的 `files[]` 字段筛出视频(`*.mp4`/`*.mov`/`*.avi` 等)/图片(`*.png`/`*.jpg`/`*.jpeg` 等)
   - **本地路径模式**：`ls` 日志目录及子目录下视频/图片文件（`find <log_dir> -type f \( -name '*.mp4' -o -name '*.mov' -o -name '*.avi' -o -name '*.png' -o -name '*.jpg' -o -name '*.jpeg' \)`），用户直接给视频/图片文件路径时直接分析该文件
   - **无条件记录附件清单**(文件名+大小+类型)到 `log_analysis.md §1`「附件清单」小节（可空章节，无视频/图片时该小节不存在）
2. **选择媒体通道**：
   - 从宿主可靠能力信息判 `native=supported|unsupported|unknown`，不得按模型名称猜测或试传图片；检查 bridge MCP/CLI 与三件套，并用 `describe_capabilities`/`--capabilities` 取得不含密钥的能力画像。
   - 执行 `python3 tools/media_router.py route --mode auto --native <state> --bridge <state> --native-max-images-per-message <宿主证明值，未知填4> --task <general|small_text|video> --risk <normal|high>`；结果写入附件分析记录，并读取 `image_batching.selected_max_images_per_message` 作为当前通道的单消息硬上限。
   - `native` → 当前 session 直接分析；`bridge` → 优先 `analyze_media_evidence`/`--analyze-evidence`；`dual` → 两通道独立分析、分歧未决；`text_only` → 只落关键帧和视觉缺口。只有 selected mode 含 native 时才允许原生媒体输入。

**分析流程**（路由选择 native/bridge/dual 时，在阶段0 内、附件枚举后、强制思考之前）：

1. **视频抽关键帧**（ffmpeg 本地免费处理，省钱）：
   - 检查 ffmpeg 是否可用：`which ffmpeg` 或 `ffmpeg -version`
   - **ffmpeg 可用**：对每个视频附件用 ffmpeg 提取场景切换关键帧

     ```bash
     # 提取场景变化 >10% 的关键帧（典型 1-2 分钟视频产出 3-15 帧）
     # TB 源：输出到 {LOCAL_TB_SRC}/frames_<video_basename>_%03d.jpg（避免抽帧写 SMB 拖垮 gvfs）
     # 本地路径：输出到 {ICODE_OUT_DIR}/frames_<video_basename>_%03d.jpg
     ffmpeg -i <video> -vf "select='gt(scene,0.1)',scale=1280:-1" -vsync vfr -q:v 2 -frame_pts 1 \
       "<对应输出路径>/frames_<video_basename>_%03d.jpg"
     ```

   - **ffmpeg 不可用**：只有 selected channel 明确支持视频且用户接受额度/体积风险时才直接分析视频；否则只处理图片附件并记录视频覆盖缺口
2. **枚举关键帧**：`ls {LOCAL_TB_SRC}/frames_*.jpg`（TB 源）或 `{ICODE_OUT_DIR}/frames_*.jpg`（本地路径）列出所有关键帧文件
3. **按 selected mode 分析图片帧**：native 由当前 session 分析；bridge 调 `analyze_media_evidence` 或 CLI `--analyze-evidence`；dual 对同一 hash/帧独立执行两路并记录分歧。对每个图片附件和关键帧至少一次，统一提示模板提取界面时钟、显示内容、操作序列、状态栏和错误提示。图片数超过 `selected_max_images_per_message` 时保持原顺序串行分批：一批完成并保存文本结果后再处理下一批，禁止并行调用；最终只聚合各批文本，不得再次携带原图。
4. **整理分析结果**(写入 `log_analysis.md §1`「附件分析结果」小节，可空章节允许留空)：
   - **时间点清单**：视频界面时钟时间(从关键帧图片读取) + 对应设备端日志时段推断(**视频时间点与设备端时间往往有偏移,需在阶段1 §2.1 状态链路图阶段估算**)
   - **现象描述**：视频/图片里直接可见的症状(界面显示/状态栏/错误提示/控件状态等)
   - **用户操作序列**：视频里观察到的用户操作(点击/输入/切换等),用于定位触发事件
   - **关键帧标注**：关键帧文件路径 + 对应时间点(供报告与代码事实验证门交叉引用)
5. **降级明示**（selected mode=`text_only`）：写「附件清单 + native 能力未证明/不支持 + bridge 不可用或不合格，视觉区域未分析；关键帧已落盘 `<path>`，请人工查看」。禁止把“已抽帧/已 OCR”写成“已完成视觉分析”。

**完整性自检**（selected mode 为 native/bridge/dual 时适用）：

- **TB 源模式**：研读完成后立即核对「已分析附件数 == meta.json 中视频/图片附件数」（写报告前再兜底一次）
- **本地路径模式**：研读完成后立即核对「已分析附件数 == 本地目录枚举的视频/图片文件数」（写报告前再兜底一次）
- 漏个视为不合规（对应反偷懒第 23 条违规）
- **text_only**：不适用“已分析数”断言，只要求附件清单、关键帧和视觉缺口完整记录

> **与阶段2「TB 评论研读」的关系**：评论文本逐条研读；媒体附件按路由逐个分析。selected mode 有视觉通道时两者并列、缺一不可；text_only 时附件必须清点并明确未分析边界。本地路径模式无 TB 评论，仅走附件研读。

## 同 TB 单复用流程（步骤1 检测到旧工单且用户选复用时）

当步骤1 检测到同 `lib+num+pid` 的旧工单、且用户选择"复用旧目录继续"时：

1. **复用旧目录**：`ICODE_OUT_DIR` = 旧工单目录（如 `.icode_output/.icode_output_3`），不新建；读其 `.ico_metadata.json` 的 `ticket_id`，**步骤2 历史检索须排除此旧 ticket_id 防自参考**（与新建场景步骤2「本次工单尚未入索引故无需排除」不同）
2. **切回分析态**：metadata `status` 从 `log_done` 切回 `log_in_progress`（经 `python3 tools/icode_control.py transition --dir {ICODE_OUT_DIR} --to log_in_progress`）；`completed_steps` 仍含 `"log"`。legacy 工单需先显式迁移，不允许直写状态。
3. **重拉最新数据**：调 `python3 ~/.claude/skills/icode/tools/tb/scripts/tb_pull.py --domain <域名> --pid <pid> defect <LIB>-<NUM> --out {ICODE_OUT_DIR}/tb_source`。tb_pull 自动把旧 `<ID>_meta.json` 备份为 `<ID>_meta.prev.json`（不丢旧数据），再写最新全量 meta。**重拉后**若 `{ICODE_OUT_DIR}` 在网络挂载，按「TB 缺陷源拉取」段「附件本地镜像」重建 `LOCAL_TB_SRC`（同样 `cp -r` 整个 `tb_source/<ID>/` 到 `/tmp/icode_attach_mirror/...`，增量分析期间一律读本地镜像）
4. **识别新增**：读 `<ID>_meta.prev.json`（旧）与 `<ID>_meta.json`（新）对比，按 `created`（评论时间戳）+ `content.comment` 为键找出**新评论**（旧 `comments[]` 没有的，**新评论同样须逐条研读、回捞时间点/日志原文进现场时间线**，不得只看条数增量）；和**新附件**（旧 files 没有的、或同名新拉取的 `_1` 后缀文件）
5. **增量对抗**：读旧 `log_analysis.md` 的「核心结论 + 对抗分析记录」-> 把新增评论/附件作**新证据** -> 重跑对抗（复用 icode 步骤2 对抗模式）。新证据可能：① 确认旧根因（追加佐证）/ ② 补充旧根因遗漏环节 / ③ **推翻旧根因**（标注「本次增量推翻旧结论」+推翻理由+新结论，旧结论不删但标已推翻）
6. **追加增量段**：在 `log_analysis.md` 追加「## 增量分析（<日期>，TB 单更新）」段：新增评论/附件清单 + 新对抗结论（确认/补充/推翻）+ 最终根因
7. **收尾**：metadata `status` 经 `transition --to log_done` 切回完成态；更新 `tb_source.meta_path`、检索字段后经 `index-write` 刷新索引。legacy 工单需先显式迁移。

> 无新增评论/附件（prev 与 current 一致）时，提示"TB 单无新数据，旧根因仍成立"，不重跑对抗、保留旧结论。

## 批量 TB 分析（所有"打开/未完成"单）

**触发条件**：零散输入含**批量意图**（"分析所有TB/全部打开单/未完成的单"等自然语言，可带 URL/pid/状态名集合），如 `/icode log 分析所有打开/未完成的TB单`。**无批量意图时走单 TB 路径，行为不变**。**⚠️ "分析所有TB单"的默认语义 = 所有"打开/未完成"状态的单**——其它状态（已解决/已完成/已关闭等）**默认不纳入分析**，除非用户显式指定（如"所有TB单含已解决"或明确的状态名集合）；此默认即 probe 的 `--status-names` 默认集合。若用户既说了批量又点了具体单号（如"分析所有打开单，重点 DEMO-26"），仍走批量流程，具体单号只作为该单的优先级/关注提示，不改变"全量枚举+分流"框架。

**目标 UX**：`/icode log 分析所有打开/未完成的TB单 <URL/pid>` -> 枚举+探测（零附件下载） -> 分流三档 -> 汇总确认 -> 逐个分析 -> 批量汇总报告，**全程不回写 TB**。

**⚠️ 状态语义（关键，决定枚举方式）**：TB 任务流状态名与 `isDone` **不同步**——实测存在 `isDone=True`（`accomplished` 有值）但任务流状态为「未完成」的单（如 `DEMO-47`），按 `isDone` 过滤会漏掉这类未完成单；且"打开"与"未完成"是**不同任务流**里的状态名（缺陷流程="打开"、任务流程="未完成"）。故**批量枚举必须用 `tb_pull.py probe --status-names`（按真实任务流状态名过滤），不能用 `list --status open`（isDone 布尔）**；默认状态名集合 `打开,未完成`（**即"分析所有TB单"默认只分析这两态，其它状态不纳入——除非用户显式指定扩展**），可按项目差异覆盖。

**执行模型（每单独立工单，绝不混单——先读这里再执行）**：
- **每个目标 TB 单 = 一个独立工单**（非 debug）：独立 `ICODE_OUT_DIR`（无旧工单 -> 按「创建新目录」新建，`.icode_output_N` 编号正常自增；有旧工单 -> 按「同 TB 单复用流程」复用旧目录），独立 `log_analysis.md`/`00_init.md`/`<单号>_log_problem_brief.md`（批量每单都是 TB 单，简报按「对外简报」段动态命名、报告完成自动生成）、独立 `tb_source/<ID>/` 附件、独立 metadata 与 index 条目（`ticket_id` + `tb_source`），各自可独立 `/icode plan`/`start` 续修，**单与单之间互不共享产物目录**（debug 变体见下一条）
- **`--debug` 批量变体（所有 TB 单都在 DEBUG 目录，可确保）**：`/icode log --debug 分析所有打开/未完成的TB单 ...` 时，整批每单的独立工单都建在 `.icode_output/.debug/.icode_output_N`（N 在 debug 域独立递增，与正常工单互不干扰）、编排目录 `.icode_output/.debug/tb_batch_<pid>/`——**所有分析产物（含汇总报告）全部落在 `.icode_output/.debug/` 域内，绝不进入正常工单域**；每单 metadata `debug: true` + 完整 `tb_source`、**不入 index、L1 阻断主流程、独立状态名 `debug_in_progress`/`debug_done`**、忽略 `--worktree`（debug 语义继承，见 [references/debug_mode.md](debug_mode.md)）；**debug 批量的复用只在 debug 域内匹配旧 debug 孪生，不匹配正常工单**（debug 是独立孪生对照，见步骤3）
- **批量编排目录 `.icode_output/tb_batch_<pid>/` 不是工单**（`--debug` 变体为 `.icode_output/.debug/tb_batch_<pid>/`）：不占工单编号、不建 metadata、不入 index、不参与"检测最新目录"；只放批量汇总报告 `tb_batch_report.md`，不承载任何单的分析产物
- **混单后果（禁止）**：把多个 TB 单塞进一个工单会破坏"一工单一根因报告一修复链路"契约——各单根因无法区分、`/icode plan` 串单、index 检索失真、后续无法单独续修

执行（阶段0 内，作为「TB 缺陷源拉取」的批量变体，替代单 TB 拉取路径）：

1. **解析目标**：pid/domain（URL > config label > config lib）；状态名集合（**默认 `打开,未完成`——即"分析所有TB单"的默认范围，其它状态单除非用户显式指定否则不分析**；用户可显式扩展或收窄，如"分析所有打开且待修复的单" -> `打开,待修复`、"所有TB单含已解决" -> `打开,未完成,已解决`，逗号分隔）
2. **枚举+探测（零附件下载）**：调 `python3 ~/.claude/skills/icode/tools/tb/scripts/tb_pull.py --domain <域名> --pid <pid> probe --status-names <集合> --out <探测目录>` -> Read `<探测目录>/<pid>.json`（每单 uniqueId/status/comments[]/files[]/updated，**不下载任何附件**；状态名不在集合内的单已过滤）
3. **分流三档**（**非 debug**：每单 Read `~/.claude/icode_data/index.json`，按 `tb_source` 的 `lib+num+pid` 匹配旧工单；**`--debug` 变体**：只匹配 debug 域内可校验的同 TB metadata，终态作对照、`debug_in_progress` 可续跑；无 metadata 的 legacy 残留只复用附件并新建受控工单，详见 [references/debug_mode.md](debug_mode.md) §12。**此步即批量场景的“复用/新建”决策，自动判定、不逐单弹问**）。**更新判定比对键**：probe.json 与旧工单 `<ID>_meta.json` 的 comments **按 `created+评论文本` 为键**比对（两文件 comments 结构不同——probe 简化 `{created,comment}`、meta 原始 `content.comment`，不能整数组直接比对，比对键口径对齐「同 TB 单复用流程」步骤4）、files 按附件名+扩展名比对、**status 按状态名比对——仅当旧 meta 含 `status` 字段时才比（旧版 tb_pull 拉的 meta 无 status 字段，缺失时跳过状态比对、视为一致，否则"无更新"会被误判"有更新"；实测旧版拉取的 meta 均无 status）**：
   - **待新建**：无匹配旧工单**且无中断半成品**（`--debug` 变体下两者皆无才走其 debug 变体）-> 走「创建新目录」（`--debug` 变体下走其 debug 变体）全新对抗分析（`ICODE_OUT_DIR` 编号自增新建）
   - **无更新跳过**：有旧工单且 probe.json 与旧工单 `<ID>_meta.json` 按上比对键全一致（含 status 兼容规则）-> 提示"TB 单无新数据，旧根因仍成立"，不重跑对抗、不新建
   - **有更新 -> 增量分析**：复用旧工单目录（`--debug` 变体下复用旧 debug 孪生目录），按步骤4 分级定动作
4. **增量影响度分级**（判断"有更新的单值不值得做增量对抗"：按 probe.json 与旧工单 meta 比对出的**新增内容实质**定级，非按条数）：
   - **高影响 -> 直接增量对抗**：新日志附件（.log/.txt/.zip 等日志类）/ 评论含新现象·复现步骤·日志原文片段·推翻旧根因表述 / 状态重新打开（回归）
   - **中影响 -> 汇总时问用户**：新图片/视频附件 / 评论补充旧根因佐证·追问细节
   - **低影响 -> 自动跳过**：无关评论（"已收到"/转发/无实质）/ 无实质变化
5. **汇总预览**：把分流+分级结果清单（待新建 / 高影响增量 / 中影响 / 跳过——"跳过"含"无更新跳过"与"低影响跳过"两类来源）**一次性**列给用户确认执行范围（可裁剪/分批），**中影响单此时一并列出等用户拍板**，**不逐个弹问**；**每单附附件概况**（probe files[] 已含 ext+size：日志类/图/视频 + 大小，**>50MB 级大附件标注"待确认"**）——供用户先裁剪再决定下载，避免盲目拉百 MB 级日志包（实测某缺陷单单个日志附件达 178MB）
6. **逐个执行**：按确认范围，**逐单串行**——同一时刻只处理一个 TB 单，前一个**正常完成或判定中止（见下方「单卡住/超时」）**后才开始下一个；**绝不并行开多个 TB 单分析**（并行会并发写各单 metadata + index 互相冲突、破坏"一单一根因报告"，且对抗分析走主会话无法同时承载多路）。**"高影响优先" = 串行队列排序（先做高影响增量单，待新建单视为必做、与高影响同优先级，其余按影响度/单号排序），不是并行**。待新建走完整对抗流程；增量走「同 TB 单复用流程」（重拉最新 -> prev/current 对比 -> 增量对抗 -> 追加增量段）。**每单完成后立即收尾**（**非 debug**：写 metadata + index 条目，新建则建索引、复用则续期；**`--debug` 变体**：只写 metadata（`debug: true`，**不写 index——debug 永不索引**），再进下一单）；每单对抗分析走主会话不委托子代理（高风险）
7. **批量汇总报告**：全部（或中止）后在批量编排目录写 `tb_batch_report.md`（**非 debug**：`.icode_output/tb_batch_<pid>/`；**`--debug` 变体**：`.icode_output/.debug/tb_batch_<pid>/`），逐单列 状态/更新内容/动作/最终根因/**独立工单目录绝对路径**/是否成功；**单中途失败或超时中止不阻塞后续单**，该单在报告中标「失败/超时·未完成」供人工补跑

**单卡住/超时（串行兜底，防一单挂死拖死全批）**：
- 逐单串行的代价是**一单卡住会阻塞后续所有单**，故每单设**单级墙钟硬截止**（建议 `BATCH_PER_TICKET_WALL_CLOCK_DEADLINE_SECONDS=1800`，30 分钟，可按 `metadata.batch_ticket_wallclock_seconds` 覆盖）：从该单开始计，到点仍未正常完成 -> 判定该单「超时/未完成」，**立即中止、不等它**，继续下一单
- 单内各等待点**仍受既有细粒度超时约束，单级墙钟只是兜底总闸、不另立档位**：对抗等待+整合阶段受 `INTEGRATION_WALL_CLOCK_DEADLINE_SECONDS=1200`（20 分钟）约束（见 [references/adversarial.md](adversarial.md)「整合墙钟硬截止」）；子代理 spawn 受 `TaskOutput(block=true, timeout=...)` 阻塞等约束（见 adversarial.md「显式等待 + 超时机制」）；单级墙钟只兜"单内无等待点但整体停滞 / 工具无响应 / 网络卡死"的极端情况
- **疑似卡住信号**：单内连续无实质进展输出（如 5 分钟无任何产出）-> 视为疑似卡住，用户可随时插话干预，或等单级墙钟到点自动中止；**禁止为等某一单无限延长**
- **超时/失败单的已产出分析产物保留**（不删，作下轮增量基础）；后续可对该单单独走「同 TB 单复用流程」补跑。vNext 因步骤 1 已先创建 metadata，应从 `debug_in_progress` 原目录续跑；若只有无 metadata 的 legacy 附件目录，则只复用证据并新建受控工单（见 [references/debug_mode.md](debug_mode.md) §12）。

**决策与交互边界（防歧义）**：
- 批量场景的**复用/新建决策 = 步骤3 分流自动决定**（待新建=新建、有更新=复用增量、无更新=跳过），**不逐单弹问"复用/新建"**（与单 TB 路径的询问机制不同）；**全程仅一次主动交互 = 步骤5 汇总预览**（一次性确认执行范围 + 中影响单拍板），之后默认全程不打断（阶段1-3 自主收敛），用户可随时插话重定向（同单 TB 追问机制 C）
- **`--worktree` 不与批量叠加**：批量逐单默认原地建（debug 忽略 worktree 是 debug 既有语义）；如需对单个 TB 单隔离验证，请走单 TB 路径
- **`--debug` 支持批量**：入口带 `--debug` 即整批全部 TB 单进入 DEBUG 域（见执行模型 debug 变体），**所有 TB 单均在 `.icode_output/.debug/` 下，可确保不混入正常工单域**；debug 批量每单结尾走 debug 对照说明（不引导进入修复流程，同单 TB debug 语义）

**成本控制**：探测阶段零附件下载（probe 只拉元数据）；仅确认要增量/新建的单才 `defect` 下载附件；批量大时按影响度分批、可裁剪。每单的评论研读/附件研读/完整性自检/评论不盲信/增量对抗（确认/补充/推翻）等规则**全部复用单 TB 路径，不另立平行规则**。

> **与单 TB 路径的关系**：批量 = 对每个目标单循环走既有"单 TB 分析 / 同 TB 单复用流程"，仅枚举、分流、汇总三步是新增；**每个 TB 单独立一个工单（独立 ICODE_OUT_DIR + 独立 `log_analysis.md`/`00_init.md`/`<单号>_log_problem_brief.md` + 独立 index 条目），绝不混入同一工单**——混单会破坏"一工单一根因报告一修复链路"的目录契约、无法区分各单根因、无法各自独立续修（`/icode plan` 串单）；单内 ICODE_OUT_DIR 确定（新建/复用）按**单 TB 路径步骤1「检测旧工单」**与「同 TB 单复用流程」既有逻辑执行（debug 变体按执行模型 debug 语义，不写 index）；批量编排目录只放汇总表，不承载任何单的分析产物。

