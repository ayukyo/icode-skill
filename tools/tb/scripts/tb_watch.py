#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""定时增量监控（v2）：周期轮询多个 TB 项目（打开/未完成）列表，按单号倒序（新单优先）检查，
发现某单有新增内容（评论/附件/状态变化）→ 自动拉起 claude 无头会话做 **debug 增量分析**
（产物落 `{工程}/.icode_output/.debug/`，不写全局 index.json，不污染正式索引）。

**只读·严禁回写 TB**：检测环节调用 tb_pull.py probe（零附件下载，纯 GET）；触发的 claude 分析
受 icode「不回写 TB」约束。本脚本自身无任何写 TB 逻辑。

**配置化·多项目**：JSON 配置文件列出多个项目，每轮遍历全部项目。用户最小配置只需给 URL：
  {"projects": [{"url": "https://tb.example.com/project/<pid>"}]}
  （url 自动解析 domain + pid；可选 "lib"/"status_names"/"interval" 等覆盖）

**debug 语义（核心）**：触发的增量分析一律走 icode debug 变体——产物只在
  {工程}/.icode_output/.debug/ 域（每单独立 debug 工单），**绝不写全局 index.json**；
  检测"有更新"的比对对象 = debug 域里该单的旧 debug 孪生（扫 debug 工单 metadata 的 tb_source
  按 lib+num+pid 匹配），与正式工单/正式索引完全脱钩。

**报告（检索列表）**：每轮生成"打开/未完成单 + 分析最新状态"报告到
  {工程}/.icode_output/tb_watch_report.md，且**每次触发 claude 分析完成后立即刷新**（重 probe 重判，
  该单不再"待新建"）；工程根默认 = 运行目录 cwd，可用 --project-dir 覆盖。

**调度语义 = 自循环守护（非 cron）**：每轮 = 检测（全部项目）->（有需增量单则触发 claude 分析、
等它结束）-> sleep interval。"分析完才计时下一个周期"，分析耗时多长都不撞下一轮；flock 单实例锁 + pid 文件。

**启动 / 停止**：
  启动：cd <工程目录> && nohup python3 tb_watch.py --config watch.json > /tmp/tb_watch.log 2>&1 &
  停止：python3 tb_watch.py --config watch.json --stop   （读 pid 文件 SIGTERM 优雅退出）
"""
import argparse, fcntl, json, os, re, signal, subprocess, sys, time

_STOP = False


def _stop_handler(signum, frame):
    global _STOP
    _STOP = True
    print(f"[signal] 收到 {signal.Signals(signum).name}，当前轮结束后退出", file=sys.stderr)


signal.signal(signal.SIGTERM, _stop_handler)
signal.signal(signal.SIGINT, _stop_handler)

PROBE_PY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tb_pull.py")
DEFAULT_STATUS_NAMES = "打开,未完成"
# claude_context_window 默认 256000：适配 AI 模型兼容性——深层架构类模型声明 1M context
# 但实测长上下文触发分类器超时（与会话无关注册/会话关联成本有关），256K 是稳定的甜蜜点。
# 子进程通过 env CLAUDE_CODE_MAX_CONTEXT_TOKENS 传给 claude，强制上下文窗口硬切到该值。
DEFAULTS = {"interval": 900, "claude_timeout": 6000, "claude_skip_permissions": False,
            "low_priority": True, "claude_context_window": 256000,
            "mount_required": False}
# mount_required: True 时 project_dir 必须在网络挂载上（sshfs/gvfs SMB）。
# 用于 NAS 工程：防止重启后挂载未恢复时，守护把 project_dir 当成普通本地目录，
# 在本地空目录上生成假的 .icode_output/ 报告与 debug 工单（与 NAS 真实产物对不上）。
# probe 单项目拉取超时兜底（网络卡死/挂载慢时防止守护每轮永久挂起变假死）
PROBE_TIMEOUT = 600


# ---------------------------------------------------------------------------
# URL / 配置解析
# ---------------------------------------------------------------------------
def parse_project_url(url):
    """从 TB 项目 URL 提取 (domain, pid)。支持 https://host/project/<pid>/... 或纯 host。

    示例：https://tb.example.com/project/abc123 -> ("tb.example.com", "abc123")
    解析不到 pid 时 pid 为 None（须由配置补 --pid）。
    """
    u = str(url or "").strip()
    m = re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", u)
    if m:
        u = u[m.end():]
    domain = u.split("/", 1)[0] if u else ""
    pm = re.search(r"/project/([0-9a-fA-F]+)", u)
    pid = pm.group(1) if pm else None
    return domain, pid


def load_config(path, cli_args):
    """读配置文件并叠加 CLI 覆盖，返回规范化配置 dict。

    配置结构：{"interval": 秒, "claude_timeout": 秒, "claude_skip_permissions": bool,
              "projects": [{"url"/"pid"/"domain"/"lib"/"status_names"/"project_dir"/"mount_required"}...]}
    规范写法：**每个 project 都写自己的 project_dir**（工程根，产物落该工程 .icode_output/）。
    顶层 project_dir 为可选（仅作全局运行时锚点/pid 目录，及单工程旧配置兼容缺省）；
    未给顶层时全局锚点取第一个 project 的 project_dir。
    """
    if path and os.path.exists(path):
        with open(path) as f:
            cfg = json.load(f)
    else:
        cfg = {}
    cfg = {**DEFAULTS, **cfg}
    if cli_args.interval is not None:
        cfg["interval"] = cli_args.interval
    if cli_args.claude_timeout is not None:
        cfg["claude_timeout"] = cli_args.claude_timeout
    if cli_args.claude_skip_permissions:
        cfg["claude_skip_permissions"] = True
    if cli_args.claude_context_window is not None:
        cfg["claude_context_window"] = cli_args.claude_context_window
    if cli_args.project_dir:
        cfg["project_dir"] = cli_args.project_dir

    raw_projects = cfg.get("projects")
    if raw_projects:
        projects = []
        for p in raw_projects:
            domain, url_pid = parse_project_url(p.get("url", ""))
            proj = {
                "url": p.get("url", ""),
                "domain": p.get("domain") or domain,
                "pid": p.get("pid") or url_pid,
                "lib": p.get("lib", ""),
                "status_names": p.get("status_names", DEFAULT_STATUS_NAMES),
                # 多工程支持：每项目带自己的工程根（规范写法，缺省继承顶层 project_dir 作旧兼容）。
                # 一份配置可列多个工程，一个守护进程每轮遍历全部工程（各自 probe/报告/debug 工单
                # 落各自工程 .icode_output/，减少守护进程数 = 减性能消耗）。
                "project_dir": p.get("project_dir") or cfg.get("project_dir"),
                # mount_required 每项目可覆盖（缺省继承顶层）：该工程路径必须在网络挂载上
                "mount_required": p.get("mount_required", cfg.get("mount_required", False)),
            }
            if not proj["domain"] or not proj["pid"]:
                raise ValueError(f"项目配置缺 domain/pid：{p}")
            if not proj["project_dir"]:
                raise ValueError(f"项目配置缺 project_dir（应在该项或顶层给工程根）：{p}")
            projects.append(proj)
    elif cli_args.pid:
        # 单项目快捷（兼容旧用法）：--domain --pid [--lib]；工程根 = --project-dir 或 cwd
        cfg.setdefault("project_dir", os.getcwd())
        projects = [{
            "url": "",
            "domain": cli_args.domain or "",
            "pid": cli_args.pid,
            "lib": cli_args.lib or "",
            "status_names": DEFAULT_STATUS_NAMES,
            "project_dir": cfg["project_dir"],
            "mount_required": cfg.get("mount_required", False),
        }]
    else:
        raise ValueError("既无配置 projects 也无 --pid，无法检测（--config 或 --domain+--pid 至少给一个）")
    cfg["projects"] = projects
    # 全局运行时锚点（pid/lock/watch 目录）：顶层给了用顶层，否则用第一个工程
    cfg.setdefault("project_dir", projects[0]["project_dir"])
    return cfg


# ---------------------------------------------------------------------------
# 有更新判定（口径对齐 steps/log.md 批量步骤3 / 单 TB 复用流程）
# ---------------------------------------------------------------------------
def _comment_key(comment, is_probe):
    """归一评论为比对键 (created, 评论文本)。probe 与 meta 的评论结构不同，须分口径。"""
    created = comment.get("created", "")
    if is_probe:
        return (created, comment.get("comment", "") or "")
    content = comment.get("content") or {}
    if isinstance(content, dict):
        text = content.get("comment", "") or ""
    else:
        text = comment.get("comment", "") or ""
    return (created, text)


def has_update(probe_item, meta):
    """机械判定该单相对旧工单 meta 是否有新增内容。

    返回 (has_update, reason_text)。无旧工单（meta 为 None）不算"有更新"，由调用方按待新建处理。
    """
    if meta is None:
        return False, "无旧工单(待新建，非增量)"
    probe_comments = probe_item.get("comments", []) or []
    meta_comments = meta.get("comments", []) or []
    probe_keys = {_comment_key(c, True) for c in probe_comments}
    meta_keys = {_comment_key(c, False) for c in meta_comments}
    new_comments = probe_keys - meta_keys

    probe_files = probe_item.get("files", []) or []
    meta_files = meta.get("files", []) or []
    probe_fkeys = {(f.get("name", ""), f.get("ext", "")) for f in probe_files}
    meta_fkeys = {(f.get("name", ""), f.get("ext", "")) for f in meta_files}
    new_files = probe_fkeys - meta_fkeys

    status_changed = False
    if "status" in meta and meta.get("status") != probe_item.get("status"):
        status_changed = True

    reason = []
    if new_comments:
        reason.append(f"新增评论{len(new_comments)}条")
    if new_files:
        reason.append(f"新增附件{len(new_files)}个")
    if status_changed:
        reason.append(f"状态变化 {meta.get('status')} -> {probe_item.get('status')}")
    return bool(reason), "、".join(reason) or "无"


# ---------------------------------------------------------------------------
# debug 域扫描 / 旧 debug 孪生定位
# ---------------------------------------------------------------------------
def scan_debug_worktickets(project_dir):
    """扫 {project_dir}/.icode_output/.debug/ 下 debug 工单，返回 [(工单目录名, metadata, tb_source)]。

    只收 metadata.debug=true 的工单；debug 工单不写 index，只能靠 metadata.tb_source 匹配复用。
    """
    dbg_root = os.path.join(project_dir, ".icode_output", ".debug")
    if not os.path.isdir(dbg_root):
        return []
    found = []
    for name in sorted(os.listdir(dbg_root)):
        md_path = os.path.join(dbg_root, name, ".ico_metadata.json")
        if not os.path.exists(md_path):
            continue
        try:
            with open(md_path) as f:
                meta = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        if not meta.get("debug"):
            continue
        ts = meta.get("tb_source")
        if not isinstance(ts, dict):
            continue
        found.append((name, meta, ts))
    return found


def scan_debug_halfdone(project_dir):
    """扫 .debug 域下"中断半成品"debug 工单：目录存在、有 tb_source 附件、但无 .ico_metadata.json。

    半成品没有 metadata，scan_debug_worktickets 收集不到，检测会误判"无基线"、
    每轮重复触发同一单重建 → 死循环（实测 root cause：某单分析被超时杀在写 metadata 前）。
    识别归属单：优先读 *_meta.json 的 uniqueId，兜底从 tb_source/<LIB>-<NUM>/ 目录名解析。
    返回 [(工单目录名, meta_path, lib, num_str)]；meta_path 可为 None（无 meta.json 的半成品）。
    """
    dbg_root = os.path.join(project_dir, ".icode_output", ".debug")
    if not os.path.isdir(dbg_root):
        return []
    found = []
    for name in sorted(os.listdir(dbg_root)):
        d = os.path.join(dbg_root, name)
        if not os.path.isdir(d) or os.path.exists(os.path.join(d, ".ico_metadata.json")):
            continue  # 正常 debug 工单走 scan_debug_worktickets
        num, lib, mp = None, None, None
        # 1) tb_source/<LIB>-<NUM>/ 目录名解析（提供 lib + 兜底 num）
        ts_root = os.path.join(d, "tb_source")
        if os.path.isdir(ts_root):
            for sub in sorted(os.listdir(ts_root)):
                m = re.match(r"^([A-Za-z0-9]+)-(\d+)$", sub)   # <LIB>-<NUM>
                if m:
                    lib, num = m.group(1), m.group(2)
                    break
        # 2) *_meta.json 的 uniqueId 作为 num 权威（若存在，覆盖目录名解析）
        for dirpath, _dirs, files in os.walk(d):
            for fn in files:
                if fn.endswith("_meta.json") and not fn.endswith(".prev.json"):
                    mp = os.path.join(dirpath, fn)
                    break
            if mp:
                break
        if mp:
            try:
                with open(mp) as f:
                    uid = json.load(f).get("uniqueId", "")
                if uid:
                    num = str(uid)
            except (json.JSONDecodeError, OSError):
                pass
        if num:
            found.append((name, mp, lib, num))
    return found


def locate_debug_meta(project_dir, workticket_dir, ts):
    """定位 debug 工单里该单的 <ID>_meta.json。

    优先级：metadata.tb_source.meta_path（可绝对/相对 workticket_dir）-> 完整递归找工单目录下 *_meta.json
    （排除 .prev 备份）。定位不到返回 None。
    """
    candidates = []
    mp = ts.get("meta_path")
    if mp:
        # 相对路径以工单目录为基准（claude 落盘 meta_path 时格式不统一：绝对/相对两种写法并存）
        candidates.append(mp if os.path.isabs(mp) else os.path.join(workticket_dir, mp))
    for dirpath, _dirs, files in os.walk(workticket_dir):
        for fn in files:
            if fn.endswith("_meta.json") and not fn.endswith(".prev.json"):
                candidates.append(os.path.join(dirpath, fn))
        # 完整递归（不 break）：meta 可能在 tb_source/<LABEL>/ 深层，只扫首层会漏
    for c in candidates:
        if c and os.path.exists(c):
            return c
    return None


def find_debug_prev(project_dir, proj, num, dbg_tickets):
    """在 debug 域找该单的旧 debug 孪生，返回 (workticket_dir, metadata, meta_path)。

    匹配键 = tb_source 的 pid(+lib) + num（对齐批量步骤3 debug 变体）。
    """
    for name, meta, ts in dbg_tickets:
        if str(ts.get("pid")) != proj["pid"]:
            continue
        if proj["lib"] and ts.get("lib") != proj["lib"]:
            continue
        if str(ts.get("num")) != str(num):
            continue
        wdir = os.path.join(project_dir, ".icode_output", ".debug", name)
        mp = locate_debug_meta(project_dir, wdir, ts)
        return (wdir, meta, mp)
    return (None, None, None)


# ---------------------------------------------------------------------------
# probe / 一轮检测
# ---------------------------------------------------------------------------
def _num_of(item):
    try:
        return int(item.get("uniqueId", "0") or "0")
    except (TypeError, ValueError):
        return 0


def run_probe(proj, watch_dir):
    """调 tb_pull.py probe 拉线上最新（打开/未完成），返回 probe 条目 list。零附件下载。"""
    out_dir = os.path.join(watch_dir, "probe")
    os.makedirs(out_dir, exist_ok=True)
    cmd = [sys.executable, PROBE_PY, "--domain", proj["domain"], "--pid", proj["pid"],
           "probe", "--status-names", proj["status_names"], "--out", out_dir]
    try:
        subprocess.run(cmd, check=True, timeout=PROBE_TIMEOUT)
    except subprocess.TimeoutExpired as e:
        # 网络卡死/挂载慢时不能卡死守护：转成检测失败，本轮跳过、下一轮重试
        raise RuntimeError(f"probe 超时（>{PROBE_TIMEOUT}s）：{proj['pid']}") from e
    probe_file = os.path.join(out_dir, f"{proj['pid']}.json")
    with open(probe_file) as f:
        items = json.load(f)
    if not isinstance(items, list):
        raise RuntimeError(f"probe 输出异常：期望 list，实得 {type(items).__name__}")
    return items


def detect_project(project_dir, proj, watch_dir):
    """检测一个项目：probe -> 按单号倒序 -> 逐单对 debug 孪生做有更新判定。

    返回 {"probe": items, "need_incremental": [...], "no_update": [...], "pending_new": [...]}，
    每类元素为 (num, label, probe_item, workticket_dir, reason, debug_meta_path)。
    """
    items = run_probe(proj, watch_dir)
    items.sort(key=_num_of, reverse=True)          # 倒序：新单（号大）优先
    dbg_tickets = scan_debug_worktickets(project_dir)
    halfdone = scan_debug_halfdone(project_dir)

    need_inc, no_upd, pend_new = [], [], []
    for it in items:
        num = _num_of(it)
        label = it.get("uniqueId") or str(num)
        wdir, meta, mp = find_debug_prev(project_dir, proj, num, dbg_tickets)
        old_meta = None
        if mp:
            with open(mp) as f:
                old_meta = json.load(f)
        if wdir is None or old_meta is None:
            # 无 metadata 但可能有"超时中断的半成品"（附件已下载）→ 按该单续跑而非新建，防死循环
            hd = [h for h in halfdone
                  if h[3] == str(num) and (not proj.get("lib") or not h[2] or h[2] == proj["lib"])]
            if hd:
                hname, hmp, _hlib, _hnum = hd[0]
                hwdir = os.path.join(project_dir, ".icode_output", ".debug", hname)
                need_inc.append((num, label, it, hwdir, "中断续跑(半成品复用,附件已下载)", hmp))
            else:
                pend_new.append((num, label, it, None, "无 debug 基线(自动建基线)", None))
            continue
        updated, reason = has_update(it, old_meta)
        if updated:
            need_inc.append((num, label, it, wdir, reason, mp))
        else:
            no_upd.append((num, label, it, wdir, reason, mp))
    return {"probe": items, "need_incremental": need_inc, "no_update": no_upd, "pending_new": pend_new}


# ---------------------------------------------------------------------------
# 报告（检索列表，落工程 .icode_output/）
# ---------------------------------------------------------------------------
def write_report(cfg, results):
    """按工程根分组生成"打开/未完成单 + 分析最新状态"报告。

    多工程支持：一份配置可列多个工程（每个 project 带自己的 project_dir），
    每个工程一份报告，覆盖写各自 {工程}/.icode_output/tb_watch_report.md。
    返回写出的报告路径 list。
    """
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    # 按 project_dir 分组：同工程根的多个 TB 项目合并进同一份报告
    groups = {}
    for proj, res in zip(cfg["projects"], results):
        groups.setdefault(proj["project_dir"], []).append((proj, res))
    written = []
    for project_dir, pairs in groups.items():
        lines = [f"# tb_watch 检索报告（打开/未完成单 · 分析最新状态）",
                 "",
                 f"- 生成时间：{stamp} | 轮询间隔：{cfg['interval']}s",
                 f"- 工程根：{project_dir} | 项目数：{len(pairs)}",
                 "- 分析状态：**需增量**=有新增内容待 claude debug 增量分析；**无更新**=debug 孪生比对一致；**待新建**=尚无 debug 孪生（自动触发 debug 分析建基线，每轮一条）；**中断续跑**=该单有上次超时遗留的半成品 debug 工单（附件已下载），复用续跑完成基线",
                 ""]
        for proj, res in pairs:
            lines.append(f"## {proj['url'] or proj['domain'] + '/project/' + proj['pid']}")
            lines.append(f"- 状态集合：{proj['status_names']} | 枚举 {len(res['probe'])} 单（按单号倒序）")
            lines.append("")
            lines.append("| 单号 | 状态 | 标题 | 评论 | 附件 | 分析状态 | debug 工单 |")
            lines.append("|------|------|------|------|------|----------|-----------|")
            for num, label, it, wdir, reason, mp in res["need_incremental"]:
                lines.append(f"| {label} | {it.get('status')} | {str(it.get('title') or '')[:30]} | "
                             f"{it.get('comments_count') or len(it.get('comments') or [])} | {len(it.get('files') or [])} | "
                             f"需增量({reason}) | {os.path.basename(wdir)} |")
            for num, label, it, wdir, reason, mp in res["pending_new"]:
                lines.append(f"| {label} | {it.get('status')} | {str(it.get('title') or '')[:30]} | "
                             f"{it.get('comments_count') or len(it.get('comments') or [])} | {len(it.get('files') or [])} | "
                             f"待新建·建基线 | - |")
            for num, label, it, wdir, reason, mp in res["no_update"]:
                lines.append(f"| {label} | {it.get('status')} | {str(it.get('title') or '')[:30]} | "
                             f"{it.get('comments_count') or len(it.get('comments') or [])} | {len(it.get('files') or [])} | "
                             f"无更新 | {os.path.basename(wdir)} |")
            lines.append("")
        report_path = os.path.join(project_dir, ".icode_output", "tb_watch_report.md")
        os.makedirs(os.path.dirname(report_path), exist_ok=True)
        with open(report_path, "w") as f:
            f.write("\n".join(lines))
        written.append(report_path)
    return written


# ---------------------------------------------------------------------------
# 触发 claude 无头 debug 增量分析
# ---------------------------------------------------------------------------
PROMPT_TMPL = """你是 icode log 定时增量监控触发的分析会话，执行该 TB 单的**完整深度 debug 增量分析**（只处理这**一个** TB 单）。

目标：TB 单 {label}（lib={lib}，num={num}），domain={domain}，pid={pid}。
工程根：{project_dir}。本轮触发原因：{reason}。

**必须执行完整 `/icode log --debug {label}` 深度分析（与人工单独 `/icode log` 同等完整，禁止因无人值守/无头环境而精简流程）**：
- **下载并解压 TB 日志附件**做日志实证根因分析（**不用 --meta-only**，日志是根因分析的核心输入）；
- 走完整 log 流程（debug 语义）：limit 前置红线检查点（读留痕 limit_checkpoint.md）→ **跳过历史工单检索（debug 独立孪生对照，不参考历史正式工单，见 references/debug_mode.md §14）** → 工程知识库/cheap-research 检索 → 需求初稿（00_init.md）→ 完整对抗根因分析（log_analysis.md）；
- debug 语义：产物在 {project_dir}/.icode_output/.debug/，不写全局 index.json，自动判定复用该单旧 debug 孪生、不询问。

若 `/icode` 命令在当前无头环境不可用，则按 icode debug 语义**手动**执行同等完整分析：
1. 在 {project_dir}/.icode_output/.debug/ 下定位该单旧 debug 孪生（metadata 的 tb_source 匹配 lib={lib} num={num} pid={pid}）；
   **若定位到的是"中断半成品"**（目录存在、tb_source/ 附件已下载、但**无 .ico_metadata.json**——上次分析超时中断的残留）：
   **复用该目录，不要新建第二个工单**；无任何旧目录才新建 debug 工单，metadata 写 debug=true / indexed=false /
   status=debug_done / project_path={project_dir} / tb_source 完整 {{lib,num,pid,label,url,meta_path}}
2. 用 tb_pull.py --domain {domain} --pid {pid} defect {num} **重拉该单最新评论**；**附件复用优化**：半成品已下载的
   附件（tb_source/<label>/ 下的 tgz/mp4/已抽帧，日志已解压到 extracted/）**直接复用、跳过重复下载**，仅补拉缺失附件
3. 对比 debug 工单旧 meta 识别新增评论/附件/状态变化；基于**日志实证**做增量对抗分析（确认/补充/推翻旧根因），
   更新该 debug 工单的 log_analysis.md；补 limit_checkpoint.md 读留痕
4. 更新该 debug 工单的 meta（并入新数据）与 metadata（续期），**绝不写全局 index.json**；
   **中断半成品收尾**：若该目录原本无 .ico_metadata.json，本步补写为正式基线（debug=true / indexed=false /
   status=debug_done / project_path={project_dir} / tb_source 完整 {{lib,num,pid,label,url,meta_path}}）

硬约束：只处理这一个 TB 单、不混单、绝不回写 TB、自动判定复用 debug 孪生不新建不询问、
所有产物只落 {project_dir}/.icode_output/.debug/ 域。
"""


def _low_priority_preexec():
    """子进程温和降级：nice 5 + ionice best-effort 最低档（-c 2 -n 7）。监控分析不抢占交互资源（失败静默，无副作用）。

    仅在 low_priority=true 时挂到 claude 子进程；nice/ionice 优先级被子进程继承。
    温和档选型：不用激进 idle（-c 3）——idle 类 IO 只在系统无其它 IO 时才执行，
    会饿死远程挂载（SMB/sshfs）下载/解压/抽帧（实测百 MB 级日志包下载+解压被拖到超时）；best-effort -n 7 仍低于
    其它普通 IO，但不会被完全饿死。nice 5 比 nice 10 权重约翻倍，仍低于默认(0)。
    """
    try:
        os.nice(5)
    except OSError:
        pass
    try:
        subprocess.run(["ionice", "-c", "2", "-n", "7", "-p", str(os.getpid())],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# gvfsd-smb fd 兜底（防 SMB 单点代理 fd 累积拖垮整个挂载；长期方案）
# ---------------------------------------------------------------------------
# 触发阈值：单进程 fd 占比达此值（与 /proc/sys/fs/file-max 比）即视为危险，先 kill 重建 gvfsd-smb 再触发
GVD_FD_RATIO = 0.6
GVD_FD_ABS_MAX = 4096  # 绝对上限兜底（小机器 file-max 也可能很大，比例失真）
# 危险时连续 N 次检测命中才触发 kill（避免抖动误杀）
GVD_DANGER_HITS = 2


def _mount_fstype(path):
    """返回 path 所在挂载的文件系统类型（findmnt -T），失败返回 ''。"""
    try:
        out = subprocess.check_output(["findmnt", "-T", path, "-o", "FSTYPE", "-n"],
                                      stderr=subprocess.DEVNULL, text=True)
        return out.strip().splitlines()[0] if out.strip() else ""
    except (subprocess.CalledProcessError, OSError, IndexError):
        return ""


def _check_mount_health(project_dir, mount_required=False):
    """探测工程路径所在挂载的健康。返回 (ok, detail) — ok=False 表示挂载不可用/危险，本轮不触发。

    按 project_dir 所在挂载类型（findmnt -T）分流：
    - mount_required=True（配置声明工程路径必须在网络挂载上）：当前不在网络挂载
      （挂载丢失 → 退化成普通本地目录 / 路径不存在）→ 硬故障 hard=True，调用方应连检测/写报告
      都跳过——否则会在本地假目录上生成 .icode_output 假数据（与 NAS 真实产物对不上）
    - gvfs SMB（fuse.gvfsd-fuse 且路径含 smb-share:）：挂载端点 + gvfsd-smb fd 占比；
      fd 危险（占比达阈值）→ danger=True，main_loop 会 recycle 替代进程（防 SMB 单点 fd 累积拖垮挂载）
    - sshfs（fuse.sshfs）：挂载可访问性（statvfs）；断线/未挂载 → ok=False（本轮跳过触发，不计退避）
    - 本地目录 / 其它（且未要求挂载）：直接放行 (True)——避免误伤正在用的挂载，
      也避免无网络挂载时被"跳过触发"卡死
    """
    info = {"fstype": "", "mount_ok": True, "gvfsd_smb_fd": 0, "file_max": 0,
            "danger": False, "hard": False, "error": ""}
    gvfs_root = f"/run/user/{os.getuid()}/gvfs"
    fstype = _mount_fstype(project_dir)
    info["fstype"] = fstype
    is_gvfs_smb = f"{gvfs_root}/smb-share:" in project_dir
    is_sshfs = fstype == "fuse.sshfs"
    if mount_required and not (is_gvfs_smb or is_sshfs):
        # 配置声明必须网络挂载，但当前路径不在网络挂载上（挂载丢失 → 本地目录 / 路径不存在）
        info["mount_ok"] = False
        info["danger"] = True
        info["hard"] = True
        info["error"] = f"mount_required=true 但路径不在网络挂载上（fstype={fstype or '无挂载'}）"
        return False, info
    if not (is_gvfs_smb or is_sshfs):
        return True, info
    if is_sshfs:
        # sshfs 挂载可访问性：statvfs 失败 = 断线/未挂载 → 本轮跳过触发（环境问题，不计退避）
        try:
            os.statvfs(project_dir)
            return True, info
        except OSError as e:
            info["mount_ok"] = False
            info["danger"] = True
            info["error"] = f"sshfs 挂载不可用：{e}"
            return False, info

    # ---- gvfs SMB 分支（原逻辑，兼容 SMB 工程）----
    try:
        os.listdir(gvfs_root)
        info["mount_ok"] = True
    except OSError as e:
        info["mount_ok"] = False
        info["danger"] = True
        info["error"] = f"gvfs 挂载不可用：{e}"
        return False, info

    try:
        with open("/proc/sys/fs/file-max") as f:
            file_max = int(f.read().strip())
        info["file_max"] = file_max
    except (OSError, ValueError):
        file_max = 0
    total_fd = 0
    try:
        out = subprocess.check_output(["pgrep", "-f", "gvfsd-smb"],
                                       stderr=subprocess.DEVNULL, text=True)
        for pid_str in out.split():
            pid = pid_str.strip()
            if not pid:
                continue
            fd_dir = f"/proc/{pid}/fd"
            try:
                total_fd += len(os.listdir(fd_dir))
            except OSError:
                pass  # 进程已死/无权限
        info["gvfsd_smb_fd"] = total_fd
    except (subprocess.CalledProcessError, OSError):
        pass
    threshold = min(int(file_max * GVD_FD_RATIO), GVD_FD_ABS_MAX) if file_max else GVD_FD_ABS_MAX
    if total_fd >= threshold:
        info["danger"] = True
    return info["mount_ok"] and not info["danger"], info


def _recycle_gvfsd_smb():
    """危险时 recycle gvfsd-smb：SIGTERM 该进程让 gvfsd-daemon 重启它。失败降级记日志。"""
    out = subprocess.run(["pgrep", "-f", "gvfsd-smb"], capture_output=True, text=True)
    killed = []
    for pid_str in out.stdout.split():
        pid = pid_str.strip()
        if not pid:
            continue
        try:
            os.kill(int(pid), signal.SIGTERM)
            killed.append(pid)
        except (ProcessLookupError, PermissionError, ValueError):
            pass
    if not killed:
        return False, "无 gvfsd-smb 进程可 recycle"
    # 等 gvfsd-daemon 重新 fork 替代进程（最多 10s）
    for _ in range(10):
        time.sleep(1)
        try:
            new = subprocess.check_output(["pgrep", "-f", "gvfsd-smb"],
                                            stderr=subprocess.DEVNULL, text=True)
            if set(new.split()) - set(killed):
                return True, f"recycled ({killed} -> 仍有活跃进程)"
        except subprocess.CalledProcessError:
            pass
    return True, f"recycled {killed}（替代进程未必立即起来）"


def trigger_claude(cfg, proj, num, label, reason):
    """拉起 claude -p 无头会话执行该单 debug 增量分析，返回 (rc, 耗时秒)。

    cwd/prompt 用该项目的工程根（proj["project_dir"]）：多工程时每个项目产物落各自工程 .icode_output/。
    """
    prompt = PROMPT_TMPL.format(lib=proj["lib"] or "(未知，按 pid 匹配)", num=num, label=label,
                                domain=proj["domain"], pid=proj["pid"],
                                project_dir=proj["project_dir"], reason=reason)
    cmd = [cfg.get("claude_cmd", "claude"), "-p", prompt]
    if cfg.get("claude_skip_permissions"):
        cmd.append("--dangerously-skip-permissions")
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] 触发 claude debug 增量分析 {label} ...")
    t0 = time.time()
    preexec = _low_priority_preexec if cfg.get("low_priority", True) else None
    # env 构造：继承 os.environ + 注入 CLAUDE_CODE_MAX_CONTEXT_TOKENS（强制 claude 把上下文窗口切到目标值）
    # settings.json 里的 CLAUDE_CODE_AUTO_COMPACT_WINDOW 仍控制自动压缩阈值；本变量是上限，二者不冲突。
    # 用 .get() 容错：万一用户清掉默认值，env 不传这个键（claude 退回到 settings.json 默认）。
    ctx_window = cfg.get("claude_context_window")
    sub_env = os.environ.copy()
    if ctx_window:
        sub_env["CLAUDE_CODE_MAX_CONTEXT_TOKENS"] = str(ctx_window)
    try:
        # cwd 强制 = 该项目的工程根：claude 的 /icode log --debug 在该目录运行，产物才落 {工程}/.icode_output/.debug/
        proc = subprocess.run(cmd, timeout=cfg["claude_timeout"], capture_output=True, text=True,
                              cwd=proj["project_dir"], preexec_fn=preexec, env=sub_env)
        rc, timed_out = proc.returncode, False
    except subprocess.TimeoutExpired:
        rc, timed_out = 124, True
    except OSError as e:
        # claude 不存在/无法启动（含 cwd 不可达）：不能冒泡杀守护，按启动失败返回
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {label} claude 启动失败：{e}", file=sys.stderr)
        return 127, 0
    cost = int(time.time() - t0)
    status = "超时" if timed_out else f"退出码={rc}"
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {label} claude 完成：{status}，耗时{cost}s")
    return rc, cost


# ---------------------------------------------------------------------------
# 主循环 / 启动停止
# ---------------------------------------------------------------------------
def watch_dir_of(project_dir):
    """watch 运行目录（日志/pid/probe 中间产物），放工程 .icode_output/tb_watch/ 下。

    多工程：每工程各自的 watch 目录（probe 中间产物/watch.log 落各自工程），
    全局锁/pid 仍用顶层 cfg["project_dir"]（单守护 = 单锁单 pid）。
    """
    return os.path.join(project_dir, ".icode_output", "tb_watch")


def acquire_lock(cfg):
    watch_dir = watch_dir_of(cfg["project_dir"])
    os.makedirs(watch_dir, exist_ok=True)
    lock_path = os.path.join(watch_dir, ".watch.lock")
    fh = open(lock_path, "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print(f"[lock] 已有 tb_watch 实例在运行（{lock_path}），本次退出。", file=sys.stderr)
        sys.exit(3)
    return fh


def write_pid(cfg):
    watch_dir = watch_dir_of(cfg["project_dir"])
    with open(os.path.join(watch_dir, "watch.pid"), "w") as f:
        f.write(str(os.getpid()))
    print(f"[pid] {os.getpid()} 已写入 {os.path.join(watch_dir, 'watch.pid')}")


def stop_daemon(cfg):
    """--stop：读 pid 文件 SIGTERM 优雅停止常驻 watch。"""
    watch_dir = watch_dir_of(cfg["project_dir"])
    pid_file = os.path.join(watch_dir, "watch.pid")
    if not os.path.exists(pid_file):
        print(f"无 pid 文件（{pid_file}），没有在跑的 watch。")
        return 0
    with open(pid_file) as f:
        pid = int(f.read().strip())
    try:
        os.kill(pid, signal.SIGTERM)
        print(f"已向 {pid} 发送 SIGTERM（优雅退出：当前轮结束后停；pid 文件由守护进程退出时自动清理）。")
    except ProcessLookupError:
        print(f"进程 {pid} 不存在（可能已退出），清理残留 pid 文件。")
        os.remove(pid_file)
    return 0


def _append_log(log_file, line):
    """追加一行 watch.log；写失败（如挂载断开）只降级到 stderr，绝不抛异常杀守护。"""
    try:
        with open(log_file, "a") as f:
            f.write(line + "\n")
    except OSError as e:
        print(f"[tb_watch] watch.log 写入失败（{e}）：{line}", file=sys.stderr)


def _interruptible_sleep(seconds):
    """分片 sleep：SIGTERM 只设 _STOP 标志（handler 不抛异常），CPython 的 time.sleep
    被信号中断后仍会睡满剩余时间（PEP 475 自动重试），导致优雅 stop 要等满整个 interval。
    分片 + 每片检查 _STOP，让 stop 在最多一个分片(5s)内生效。
    """
    remaining = seconds
    while remaining > 0 and not _STOP:
        step = min(remaining, 5)
        time.sleep(step)
        remaining -= step


def _all_log_files(cfg):
    """多工程：每工程各自 watch.log；轮级事件写全部工程（每工程日志完整反映自身轮询状态）。"""
    logs = []
    for p in cfg["projects"]:
        lf = os.path.join(watch_dir_of(p["project_dir"]), "watch.log")
        os.makedirs(os.path.dirname(lf), exist_ok=True)
        logs.append(lf)
    return logs


def _append_all(log_files, line):
    """追加一行到全部工程 watch.log；写失败仅降级，不抛异常。"""
    for lf in log_files:
        _append_log(lf, line)


def _mount_req_dirs(cfg):
    """需要 mount_required 硬门的工程根集合（去重；每工程 mount_required 独立）。"""
    return sorted({p["project_dir"] for p in cfg["projects"] if p.get("mount_required")})


def pick_candidate(candidates, last_trigger_dir):
    """工程间公平轮转：从候选里选本轮要触发分析的一条。

    candidates 为 [(proj, item), ...]，item[0] = TB 单号 int。内部先按单号倒序（同工程内
    新单优先），再优先选"非上次触发工程"里单号最大的候选——两边都有活时严格 A→B→A→B 轮转；
    仅单工程/另一边本轮无候选时才回退到上次触发工程（仍取该工程单号最大）。
    返回 (proj, item, 本轮到次选中的工程根 last_trigger_dir)。
    约定：调用方保证 candidates 非空。
    """
    ordered = sorted(candidates, key=lambda x: x[1][0], reverse=True)
    proj, item = ordered[0]
    if last_trigger_dir is not None:
        for p, it in ordered:
            if p["project_dir"] != last_trigger_dir:
                proj, item = p, it
                break
    return proj, item, proj["project_dir"]


def main_loop(cfg, once=False):
    """自循环守护：每轮遍历全部工程/项目 -> 检测 -> 触发 claude -> sleep。

    多工程：一份配置可列多个工程（每 project 带自己的 project_dir），单守护每轮遍历全部工程，
    各自 probe/报告/debug 工单落各自工程 .icode_output/——少起守护进程 = 减性能消耗。
    触发顺序 = **工程间公平轮转**：两个工程都有候选时严格 A→B→A→B 交替（同工程内按单号倒序
    新单优先）；仅一个工程有候选时集中处理那个工程，不因轮转而空等。
    """
    # 全局锁/pid 用顶层工程（单守护单锁单 pid）；每工程 watch.log/probe 落各自工程
    top_watch_dir = watch_dir_of(cfg["project_dir"])
    os.makedirs(top_watch_dir, exist_ok=True)
    log_files = _all_log_files(cfg)
    project_dirs = sorted({p["project_dir"] for p in cfg["projects"]})
    round_no = 0
    # 连续"快速失败"计数（<600s 的触发失败 = 疑似连接/环境故障，如 API Connection refused）。
    # >=3 时退避：跳过触发 claude（检测/报告照常），直到某单触发成功才清零——防网关故障期每轮空转启动 claude。
    _trigger_fail_streak = 0
    # 工程间公平轮转游标：上次触发的是哪个工程根（None = 首次，直接取全局单号最大）。
    # 两个工程都有候选时，下轮优先从"另一个工程"里挑单号最大的，实现 A→B→A→B 严格交替。
    last_trigger_dir = None
    while True:
        # 顶部统一检查退出标志：检测失败分支的 continue / sleep 被信号中断后
        # 都会回到这里，确保 SIGTERM(优雅 stop) 在任何路径下都能让守护退出
        if _STOP:
            break
        round_no += 1
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        # mount_required 硬门（多工程逐工程独立）：配置声明某工程路径必须在网络挂载上；
        # 若当前挂载缺失（路径退化成普通本地目录），本轮整体跳过——不检测、不写报告、不触发，
        # 防止在本地假目录上生成 .icode_output 假数据（与 NAS 真实产物对不上）。
        # start 时 ctl 已有同款前置检查；此处兜底覆盖"启动后挂载中途丢失/重启后未恢复"场景。
        mount_bad = []
        for pd in _mount_req_dirs(cfg):
            _mok, _minfo = _check_mount_health(pd, mount_required=True)
            if not _mok:
                mount_bad.append(f"{pd}（{_minfo['error']}）")
        if mount_bad:
            msg = f"[{stamp}] round#{round_no} 挂载未就绪（mount_required）：{'；'.join(mount_bad)}，本轮跳过（不检测/不写报告/不触发）"
            print(msg)
            _append_all(log_files, msg)
            if once:
                break
            _interruptible_sleep(cfg["interval"])
            continue
        print(f"[{stamp}] round#{round_no} 开始检测（{len(cfg['projects'])} 项目 / {len(project_dirs)} 工程）...")
        results = []
        try:
            for proj in cfg["projects"]:
                res = detect_project(proj["project_dir"], proj, watch_dir_of(proj["project_dir"]))
                results.append(res)
        except Exception as e:
            # 网络异常/probe 超时/挂载断开等单轮检测失败：记日志后下一轮重试，绝不杀守护
            print(f"[{stamp}] round#{round_no} 检测失败：{e}", file=sys.stderr)
            _append_all(log_files, f"[{stamp}] round#{round_no} 检测失败：{e}")
            if once:
                break
            _interruptible_sleep(cfg["interval"])
            continue

        try:
            report_paths = write_report(cfg, results)
            print(f"[{stamp}] round#{round_no} 报告已写：{'、'.join(report_paths)}")
        except Exception as e:
            # 报告落各工程 .icode_output/，挂载断开等写失败不崩守护，仅降级提示
            print(f"[{stamp}] round#{round_no} 报告写入失败：{e}", file=sys.stderr)
            _append_all(log_files, f"[{stamp}] round#{round_no} 报告写入失败：{e}")

        # 候选 = 需增量 + 待新建（待新建自动建 debug 基线），跨工程合并
        candidates = []
        for proj, res in zip(cfg["projects"], results):
            for item in res["need_incremental"] + res["pending_new"]:
                candidates.append((proj, item))
        try:
            if candidates and not cfg.get("detect_only"):
                # 工程间公平轮转：优先选"非上次触发工程"里单号最大的候选，两边都有活时
                # 严格 A→B→A→B；仅单工程/另一边无候选才回退上次工程（同工程内仍按单号倒序）。
                proj, chosen, last_trigger_dir = pick_candidate(candidates, last_trigger_dir)
                num, label, _it, _wdir, reason, _mp = chosen
                proj_log = os.path.join(watch_dir_of(proj["project_dir"]), "watch.log")
                print(f"[{stamp}] round#{round_no} 轮转选中 工程={proj['project_dir']} 单号={num} {label}（{reason}）")
                # 挂载健康兜底：按该候选工程路径所在挂载类型（SMB/sshfs）检查，不可用或危险 → 本轮不触发
                mount_ok, mount_info = _check_mount_health(proj["project_dir"], proj.get("mount_required", False))
                if not mount_ok:
                    print(f"[{stamp}] round#{round_no} 挂载健康异常：{mount_info}，本轮不触发")
                    _append_log(proj_log, f"[{stamp}] round#{round_no} 挂载健康异常：{mount_info}，本轮不触发")
                    if mount_info.get("mount_ok"):
                        # 仅 fd 危险（gvfs SMB 挂载端点还活着）才 recycle 进程；挂载已断 recycle 无意义
                        rec_ok, rec_msg = _recycle_gvfsd_smb()
                        print(f"[{stamp}] round#{round_no} recycle gvfsd-smb：{rec_ok} {rec_msg}")
                        _append_log(proj_log, f"[{stamp}] round#{round_no} recycle gvfsd-smb：{rec_ok} {rec_msg}")
                    # 无论 recycle 是否成功，本轮不再触发 claude（避免 claude 满血 IO 把脆弱的挂载再次拖垮）
                if _trigger_fail_streak >= 3:
                    # 退避：连续快速失败（疑似网关/环境故障），本轮跳过触发；检测/报告照常，成功一次即清零
                    print(f"[{stamp}] round#{round_no} 触发连续快速失败 {_trigger_fail_streak} 次，本轮跳过触发（退避，疑似网关/环境故障），候选 {label}")
                    _append_log(proj_log, f"[{stamp}] round#{round_no} 触发连续快速失败 {_trigger_fail_streak} 次，跳过触发（退避），候选 {label}")
                elif not mount_ok:
                    # 挂载异常已记日志（上行），本轮不触发但也不计入退避（环境问题非网关问题）
                    print(f"[{stamp}] round#{round_no} 挂载健康异常，本轮跳过触发，候选 {label}")
                    _append_log(proj_log, f"[{stamp}] round#{round_no} 挂载健康异常，本轮跳过触发（不计退避），候选 {label}")
                else:
                    rc, cost = trigger_claude(cfg, proj, num, label, reason)
                    if rc == 0:
                        _trigger_fail_streak = 0
                    elif cost < 600:
                        # 快速失败（<10 分钟）= 疑似连接/环境故障（如 API Connection refused），计入退避
                        _trigger_fail_streak += 1
                    # >=600s 的失败（任务太重超时等）不计入退避——由"中断半成品续跑"兜底，不误伤
                    detail = f"，快速失败连续{_trigger_fail_streak}次" if (rc != 0 and cost < 600) else ""
                    _append_log(proj_log, f"[{stamp}] round#{round_no} 触发 {label}（{reason}）：{'成功' if rc == 0 else '失败/超时'}，耗时{cost}s{detail}")
                    # 触发分析完成后立即刷新检索报告：该单刚建基线/完成增量，report 若仍标"待新建"
                    # 会误导（须等下一轮才反映）。先等挂载缓存刷新（网络挂载对刚写入的 metadata
                    # 可能有目录缓存延迟），再重 probe 重判重写；刷新失败不崩守护（下轮自然刷新兜底）。
                    try:
                        time.sleep(3)
                        fresh = [detect_project(p["project_dir"], p, watch_dir_of(p["project_dir"])) for p in cfg["projects"]]
                        write_report(cfg, fresh)
                        print(f"[{stamp}] round#{round_no} 分析完成，report 已刷新")
                        _append_log(proj_log, f"[{stamp}] round#{round_no} 分析完成，report 已刷新")
                    except Exception as e:
                        # 刷新失败不崩守护（下轮自然刷新兜底），但须留痕 watch.log 便于追溯
                        print(f"[{stamp}] round#{round_no} 触发后 report 刷新失败：{e}", file=sys.stderr)
                        _append_log(proj_log, f"[{stamp}] round#{round_no} 触发后 report 刷新失败：{e}")
            elif candidates:
                print(f"[{stamp}] round#{round_no} (detect-only) 候选 {candidates[0][1][1]}，未触发 claude")
            else:
                print(f"[{stamp}] round#{round_no} 全部项目无新增内容且无待新建")
        except Exception as e:
            # 触发 claude 意外异常（含写 watch.log 失败）不崩守护，仅降级提示
            print(f"[{stamp}] round#{round_no} 触发/记录失败：{e}", file=sys.stderr)
            _append_all(log_files, f"[{stamp}] round#{round_no} 触发/记录失败：{e}")

        if once or _STOP:
            break
        _interruptible_sleep(cfg["interval"])


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description="定时增量监控 TB 打开/未完成单（自循环守护 · debug 语义 · 多项目配置）")
    ap.add_argument("--config", help="JSON 配置文件（多项目，用户只配 URL；见 tools/tb/README.md）")
    ap.add_argument("--once", action="store_true", help="只跑一轮即退出")
    ap.add_argument("--detect-only", action="store_true", help="只检测+写报告，不触发 claude")
    ap.add_argument("--stop", action="store_true", help="停止常驻 watch（读 pid 文件 SIGTERM）")
    ap.add_argument("--project-dir", help="工程根（报告与 debug 工单落点；默认 = 运行目录 cwd）")
    ap.add_argument("--interval", type=int, help="覆盖轮询间隔秒（默认 900 = 15 分钟）")
    ap.add_argument("--claude-timeout", type=int, help="覆盖单次 claude 超时秒（默认 6000 = 100 分钟防挂死兜底）")
    ap.add_argument("--claude-skip-permissions", action="store_true",
                    help="给 claude 加 --dangerously-skip-permissions（无头无人值守所需；慎用，见 README 风险）")
    ap.add_argument("--claude-context-window", type=int,
                    help="覆盖 claude 子进程上下文窗口 token 数（默认 256000；通过 env CLAUDE_CODE_MAX_CONTEXT_TOKENS 传给 claude）")
    # 单项目快捷（无 --config 时）：--lib/--domain/--pid
    ap.add_argument("--lib", default="", help="缺陷库前缀（可选，仅用于匹配）")
    ap.add_argument("--domain", default="", help="TB 域名（无 --config 时与 --pid 搭配）")
    ap.add_argument("--pid", default="", help="TB 项目 ID（无 --config 时单项目快捷）")
    return ap.parse_args(argv)


def main():
    args = parse_args()
    cfg = load_config(args.config, args)

    if args.stop:
        return stop_daemon(cfg)

    # mount_required 启动硬门（多工程逐工程独立）：任一声明必须在网络挂载上的工程
    # 当前不在网络挂载上（挂载丢失 → 本地目录/路径不存在）→ 拒绝启动（不写 pid / 不建任何
    # 本地目录，防重启后挂载未恢复时在本地空目录生成假的 .icode_output）。
    # ctl start 已有同款前置检查（mount_ready），此处兜底覆盖直接 python 运行入口。
    bad_dirs = []
    for pd in _mount_req_dirs(cfg):
        _mok, _minfo = _check_mount_health(pd, mount_required=True)
        if not _mok:
            bad_dirs.append(f"{pd}（{_minfo['error']}）")
    if bad_dirs:
        print(f"[tb_watch] 挂载未就绪（mount_required）：{'；'.join(bad_dirs)}，拒绝启动（未写任何目录）",
              file=sys.stderr)
        return 1

    cfg["detect_only"] = args.detect_only
    if args.detect_only or args.once:
        main_loop(cfg, once=True)
    else:
        lock = acquire_lock(cfg)
        try:
            write_pid(cfg)
            main_loop(cfg)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
            pid_file = os.path.join(watch_dir_of(cfg["project_dir"]), "watch.pid")
            if os.path.exists(pid_file):
                os.remove(pid_file)


if __name__ == "__main__":
    main()
