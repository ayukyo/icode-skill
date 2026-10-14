# DOC 工作区版本与发布契约

本文件是 DOC 的版本身份、模块存储、依赖绑定、续跑和缓存身份真源。生成入口见 [doc.md](../steps/doc.md)，段零入口见 [dir_and_metadata.md](dir_and_metadata.md)。正文格式仍按 [doc_template.md](doc_template.md)，文档模板版本与存储版本分开。

## 1. 身份与目录

默认描述**当前工作区**，包含已暂存、未暂存、未忽略的未跟踪文件、已删除文件及可读取子仓库的实际内容。不得用 `git archive HEAD` 替代工作区；提交号不代表工作区字节。没有有效 HEAD 或输入读取失败时，记录未解析原因，不编造版本。忽略文件不自动纳入；若正文依赖它们，须另行记录实际输入及 SHA，不能声称已被默认扫描覆盖。

辅助工具 [doc_snapshot.py](../tools/doc_snapshot.py) 只做身份、存储、工程绑定和查找，不生成正文或判断描述是否真实：

- `module_key`：沿用模块名加 `sha256(url + ":" + branch)[:12]` 的族键。保留原 URL 与分支，不擅自把不同 URL 的旧目录合并。
- `source_fingerprint`：工作区文件清单、路径、Git 模式、实际内容 SHA、删除标记及可读取嵌套仓库内容的完整指纹；时间和 checkout 绝对路径不参与。
- `source_id`：完整 HEAD、工作区指纹、已声明依赖的版本身份共同计算。同 HEAD 不同内容分别保存；不同本地路径的同身份可复用。外部消息头、构建输入或契约来自其他仓库时，用 `--dependency-identity` 声明，不能只核本模块 HEAD。
- `snapshot_id`：`source_id`、模板版本、完整/部分范围、正文 SHA 清单和审计回执共同计算的不可变文档修订。代码相同但修订文档或升级模板时创建新快照，不修改已有正文。

```text
module_docs/<module_key>/
  _meta.json / *.md                  # 原平铺库；保留，不冒充新快照
  snapshots/<snapshot_id>/
    _meta.json                      # storage_schema=2，完整 source_identity 与 review
    00_overview.md
    <其他已审章节>.md
    90_glossary.md
    99_code_facts_audit.md
```

这让多个工程使用同 URL/分支的不同提交或 dirty 工作区时同时有匹配正文。工程分支、模块族、源码身份和文档修订是不同维度，不能拿一个替代另一个。

URL以实际origin或已核manifest声明为准；声明分支/tag与实际checkout分开记录，完整HEAD和实际内容才是源码版本。无remote仓库保留无remote事实，可用`local:<实际Git common-dir>`作本机身份命名空间；不要编造网络URL或宣称跨机器同族共享。

同一工程/模块同时消费同族的两个版本时，给依赖身份加明确input_role，CLI用`--dependency-identity <角色>=<identity.json>`，例如left-sdk与right-sdk。身份包含角色→族/版本映射，角色版本互换会改变source_id；重复的同身份副本不改变指纹，但每个声明副本仍重核。无角色却要求同族两个不同版本时报歧义，不虚构分支规避。

## 2. 工作区捕获与生成

使用宿主可用的 Python 3.9+ 和 Git；路径含空格时作为完整参数。工具不依赖 MCP 或 Unix 锁，不修改源码，不 fetch/checkout/commit。原始 JSON 输出保留 ASCII 转义，可在 Windows 默认终端编码下读取。

```text
python tools/doc_snapshot.py capture --repo <解析后的模块Git根> --url <真实URL> --branch <有效上游分支>
```

把输出保存到本次隔离工作目录的 `identity.json`，然后：

1. **先读旧值再做判断**：读旧工程/模块元信息、旧章节与旧依赖；保存比较基线，不先写 `current_commit` 或新 `module_deps`。
2. 按 `(module_key, source_id)` 去重，保留全部实际来源路径。族键相同、源码身份不同必须分别进入 worklist。Git 根之外的工程内模块仍走工程章节，但同样记录工作区指纹。
3. 先在隔离 stage 中生成、逐章合并旧有用内容及兼容章节名；人工备注不自动丢弃。完整生成的章节清单在规划时固定，不能靠删待办宣称完成。
4. 正文引用当前工作区源码，记录直接依赖的实际版本。远程 README 仅是参考，不替代当前实现。
5. 逐章完成代码事实和语义核验后，独立审计回执绑定本次 `source_id`、模板、范围、**全部已审正文的 SHA**。只核引用存在不算语义通过；编译/设备未运行必须说明。

审计回执的最小形状：

```json
{
  "source_id": "<capture的完整source_id>",
  "template_version": "v2.0.0",
  "scope": "full",
  "status": "verified",
  "chapter_sha256": {"00_overview.md": "<完整SHA256>", "90_glossary.md": "<完整SHA256>", "99_code_facts_audit.md": "<完整SHA256>"},
  "evidence": ["<本次实际审查记录；不得编造>"]
}
```

示例只展示字段；真正 full 清单须包括规划中的所有章节。`scope=partial` 可保留已审小批，不能伪装为模块全量完成。工具只验证回执与输入/输出字节一致，回执中的语义结论仍由实际审查负责。

## 3. 发布与工程绑定

```text
python tools/doc_snapshot.py publish --identity <identity.json> --stage <正文stage> --review <review.json> --library-root <module_docs根>
```

发布前和复制后重新捕获实际源码/声明依赖，身份变化则停止，保留草稿并重新分析差异。库和父工程元信息必须在实际源码输入之外；发布/绑定写入前核实际物理路径，包含junction/symlink指向的模块、可读嵌套仓库及递归声明依赖。只把完整 staging 目录原子发布到新快照位置；复制中断或审核后改稿不能推进旧版本。相同不可变快照可幂等复用，发现已有正文被人工改动则拒绝原位覆盖，新审稿作为另一修订保存。并发发布不共写正文、族元信息或单一“最新版本”指针。

**快照可见后才写工程绑定及状态**，用以下命令绑定工程全局文档目录的元信息，元信息路径必须位于源码工程之外：

```text
python tools/doc_snapshot.py bind --identity <identity.json> --snapshot-id <已发布snapshot_id> --project-meta <project_docs工程分支目录/_meta.json> --project-root <实际工程根> --path <实际模块相对路径> --name <模块名> --library-root <module_docs根>
```

工具核对实际模块路径和快照可读性，按本地相对路径分别绑定同族版本；保留未知字段及旧工程整体基线。写入前重读元信息，以独占文件锁串行化工具写者，核原内容字节并用临时文件原子替换。锁忙或外部编辑冲突时拒绝本次写入，调用方重读合并后重试，不直接覆盖。外部编辑器若不遵守锁，在最后比对与替换之间仍存在竞争窗口；这不是文件系统事务。中断遗留锁须确认无活动写者后由用户处理，不自动删锁。成功返回绑定后再推进工作清单，并回读父元信息确认。模块的输出字段映射到工程 `module_deps`：

```json
{
  "name": "module_a", "url": "<真实URL>", "branch": "main",
  "key": "<module_key>", "commit": "<完整HEAD>",
  "source_mode": "working-tree", "source_fingerprint": "<完整指纹>",
  "source_id": "<完整source_id>", "snapshot_id": "<完整snapshot_id>",
  "path": "<真实本地模块相对路径>/", "doc_path": "snapshots/<snapshot_id>",
  "generated": true
}
```

`generated=true` 只用于该绑定的 full 快照已验证可读；partial 或发布失败保持 false/待办，不能写成“只要族目录存在就生成”。`used_by` 是关联提示，**不是版本选择依据**；新版本关联以各父工程的完整绑定为准，旧族级 used_by 不证明其正文匹配所有工程。

工程章节增量时，记录每章的 `source_id`、`generation_commit`、已核输入清单/依赖及正文 SHA。跳过手改章或仍有未审章时保留其旧基线，**不推进全工程 last_generated_commit**。同 HEAD 也要比较工作区和依赖指纹；先结合文件清单差异选章，再沿实际调用边界核验。

## 4. 段零检索与缓存

段零先重新捕获当前实际源码身份，再取工程绑定的具体 `snapshot_id`。resolve默认还会重核identity中的实际repo_root及声明依赖，旧identity文件不能绕过dirty变化：

```text
python tools/doc_snapshot.py resolve --identity <当前identity.json> --snapshot-id <dep.snapshot_id> --template-version <当前模板> --library-root <module_docs根>
```

只有 `status=match` 才可把返回目录的正文加入候选；仍须按 KEYS 选小节并 Read 当前代码核行为。`source_mismatch`、`modified`、`partial`、`template_mismatch`、`missing`、`ambiguous` 都不能注入正文。缺绑定时至多查找同源码身份的唯一快照；多个修订不得按目录时间随便选。过期文档仅给不含 file:line 的概览和明确警告，不使用错误版本正文“加警告后照读”。

缓存命中**不能跳过上述校验**：

- 检索缓存键包含 cwd、关键词、当前工程/模块 `source_id`、精确依赖绑定及模板；值绑定实际文档 SHA。TTL、index mtime 仅为附加失效条件。
- 注入缓存的 `ref_id` 对模块须包含族键和 `snapshot_id`；对工程须包含章节源身份及正文 SHA。`slice` 再含正文/摘要策略和小节锚点。旧仅文件名缓存不能抑制新版本注入。
- `cache_key(..., template_version=<当前模板>)` 和 `can_resume` 可供宿主调用；工程无模块绑定时也显式传模板，防模板变化复用旧键。缓存要在 resolve、正文 SHA 和 current-source 校验通过后使用，不能把自构造的“match”当工具实证。

## 5. 续跑、旧库与人工编辑

worklist 中 module item 的身份为 `(module_key, source_id)`，另存 `snapshot_id` 与状态。恢复时先核当前 source_id、绑定与输出 SHA，verified/done 才能复用；基线变化则新增/重置待办，保留旧记录。request_id 相同不代表源码没有变化。

旧平铺模块库、缺 source_fingerprint 的旧章节、旧 worklist/cache 保持可读但不能冒充精确版本。相同 commit 仍不能证明旧文档描述了当前 dirty 内容。旧库只作概览和人工合并材料；不自动赋予指纹，不删除或移动到检索之外，不覆写旧 _meta 的未知字段。新绑定成功后父工程读新快照；旧正文继续保留，清理需用户明确选择。

有记录正文 SHA 时以内容差异判断手改，mtime 只作提示。旧库无 SHA 时保守视为待核；复制/迁移导致 mtime 变化不能断言手改，更不能称已识别“N处修改”。用户已选择合并/跳过时继续按该偏好逐章执行，不重复申请同一授权。

工程 v1→分支目录迁移先备份旧正文与**完整未知元信息**，只在新位置生成并核验，确认新绑定有效后切换检索。禁止建子目录后 `rmdir` 父目录，也不能在用户拒绝迁移时把旧正文移出可读布局。部分迁移/部分审计明确列未完成项。

## 6. 验证边界

[test_doc_snapshot.py](../tests/test_doc_snapshot.py) 用隔离 Git 样例与库验证版本并存、dirty/删除/未跟踪、依赖变化、审核改稿、发布中断、手改、精确检索和续跑/缓存身份。它不替代正文语义审查、宿主完整工作流或设备运行；不得把工具通过称为全部工程文档生成完毕。
