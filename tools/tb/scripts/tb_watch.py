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
  {工程}/.icode_output/tb_watch_report.md（工程根默认 = 运行目录 cwd，可用 --project-dir 覆盖）。

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
DEFAULTS = {"interval": 900, "claude_timeout": 6000, "claude_skip_permissions": False,
            "low_priority": True}
# probe 单项目拉取超时兜底（网络卡死/SMB 慢时防止守护每轮永久挂起变假死）
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
              "project_dir": 工程根, "projects": [{"url"/"pid"/"domain"/"lib"/"status_names"}...]}
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
    if cli_args.project_dir:
        cfg["project_dir"] = cli_args.project_dir
    cfg.setdefault("project_dir", os.getcwd())

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
            }
            if not proj["domain"] or not proj["pid"]:
                raise ValueError(f"项目配置缺 domain/pid：{p}")
            projects.append(proj)
    elif cli_args.pid:
        # 单项目快捷（兼容旧用法）：--domain --pid [--lib]
        projects = [{
            "url": "",
            "domain": cli_args.domain or "",
            "pid": cli_args.pid,
            "lib": cli_args.lib or "",
            "status_names": DEFAULT_STATUS_NAMES,
        }]
    else:
        raise ValueError("既无配置 projects 也无 --pid，无法检测（--config 或 --domain+--pid 至少给一个）")
    cfg["projects"] = projects
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


def locate_debug_meta(project_dir, workticket_dir, ts):
    """定位 debug 工单里该单的 <ID>_meta.json。

    优先级：metadata.tb_source.meta_path（可绝对/相对 project_dir）-> 递归找工单目录下 *_meta.json
    （排除 .prev 备份）。定位不到返回 None。
    """
    candidates = []
    mp = ts.get("meta_path")
    if mp:
        candidates.append(mp if os.path.isabs(mp) else os.path.join(project_dir, mp))
    for dirpath, _dirs, files in os.walk(workticket_dir):
        for fn in files:
            if fn.endswith("_meta.json") and not fn.endswith(".prev.json"):
                candidates.append(os.path.join(dirpath, fn))
        break  # 只扫第一层（tb_source/<LABEL>/ 结构）
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
        # 网络卡死/SMB 慢时不能卡死守护：转成检测失败，本轮跳过、下一轮重试
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
    """生成"打开/未完成单 + 分析最新状态"检索报告，覆盖写 {工程}/.icode_output/tb_watch_report.md。"""
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    lines = [f"# tb_watch 检索报告（打开/未完成单 · 分析最新状态）",
             "",
             f"- 生成时间：{stamp} | 轮询间隔：{cfg['interval']}s",
             f"- 工程根：{cfg['project_dir']} | 项目数：{len(cfg['projects'])}",
             "- 分析状态：**需增量**=有新增内容待 claude debug 增量分析；**无更新**=debug 孪生比对一致；**待新建**=尚无 debug 孪生（自动触发 debug 分析建基线，每轮一条）",
             ""]
    for proj, res in zip(cfg["projects"], results):
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
    report_path = os.path.join(cfg["project_dir"], ".icode_output", "tb_watch_report.md")
    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    with open(report_path, "w") as f:
        f.write("\n".join(lines))
    return report_path


# ---------------------------------------------------------------------------
# 触发 claude 无头 debug 增量分析
# ---------------------------------------------------------------------------
PROMPT_TMPL = """你是 icode log 定时增量监控触发的分析会话，执行该 TB 单的**完整深度 debug 增量分析**（只处理这**一个** TB 单）。

目标：TB 单 {label}（lib={lib}，num={num}），domain={domain}，pid={pid}。
工程根：{project_dir}。本轮触发原因：{reason}。

**必须执行完整 `/icode log --debug {label}` 深度分析（与人工单独 `/icode log` 同等完整，禁止因无人值守/无头环境而精简流程）**：
- **下载并解压 TB 日志附件**做日志实证根因分析（**不用 --meta-only**，日志是根因分析的核心输入）；
- 走完整 log 流程：limit 前置红线检查点（读留痕 limit_checkpoint.md）→ 历史工单检索 → 工程知识库/cheap-research 检索 → 需求初稿（00_init.md）→ 完整对抗根因分析（log_analysis.md）；
- debug 语义：产物在 {project_dir}/.icode_output/.debug/，不写全局 index.json，自动判定复用该单旧 debug 孪生、不询问。

若 `/icode` 命令在当前无头环境不可用，则按 icode debug 语义**手动**执行同等完整分析：
1. 在 {project_dir}/.icode_output/.debug/ 下定位该单旧 debug 孪生（metadata 的 tb_source 匹配 lib={lib} num={num} pid={pid}）；
   无则新建 debug 工单，metadata 写 debug=true / indexed=false / status=debug_done /
   project_path={project_dir} / tb_source 完整 {{lib,num,pid,label,url,meta_path}}
2. 用 tb_pull.py --domain {domain} --pid {pid} defect {num} **下载全部 TB 附件**（日志 tgz 解压到
   tb_source/<label>/extracted/）并重拉该单最新评论
3. 对比 debug 工单旧 meta 识别新增评论/附件/状态变化；基于**日志实证**做增量对抗分析（确认/补充/推翻旧根因），
   更新该 debug 工单的 log_analysis.md；补 limit_checkpoint.md 读留痕
4. 更新该 debug 工单的 meta（并入新数据）与 metadata（续期），**绝不写全局 index.json**

硬约束：只处理这一个 TB 单、不混单、绝不回写 TB、自动判定复用 debug 孪生不新建不询问、
所有产物只落 {project_dir}/.icode_output/.debug/ 域。
"""


def _low_priority_preexec():
    """子进程降级：nice 10 + ionice idle。监控分析不抢占交互资源（失败静默，无副作用）。

    仅在 low_priority=true 时挂到 claude 子进程；nice/ionice 优先级被子进程继承。
    """
    try:
        os.nice(10)
    except OSError:
        pass
    try:
        subprocess.run(["ionice", "-c", "3", "-p", str(os.getpid())],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def trigger_claude(cfg, proj, num, label, reason):
    """拉起 claude -p 无头会话执行该单 debug 增量分析，返回 (rc, 耗时秒)。"""
    prompt = PROMPT_TMPL.format(lib=proj["lib"] or "(未知，按 pid 匹配)", num=num, label=label,
                                domain=proj["domain"], pid=proj["pid"],
                                project_dir=cfg["project_dir"], reason=reason)
    cmd = [cfg.get("claude_cmd", "claude"), "-p", prompt]
    if cfg.get("claude_skip_permissions"):
        cmd.append("--dangerously-skip-permissions")
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] 触发 claude debug 增量分析 {label} ...")
    t0 = time.time()
    preexec = _low_priority_preexec if cfg.get("low_priority", True) else None
    try:
        # cwd 强制 = 工程根：claude 的 /icode log --debug 在该目录运行，产物才落 {工程}/.icode_output/.debug/
        proc = subprocess.run(cmd, timeout=cfg["claude_timeout"], capture_output=True, text=True,
                              cwd=cfg["project_dir"], preexec_fn=preexec)
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
def watch_dir_of(cfg):
    """watch 运行目录（日志/pid/probe 中间产物），放工程 .icode_output/tb_watch/ 下。"""
    return os.path.join(cfg["project_dir"], ".icode_output", "tb_watch")


def acquire_lock(cfg):
    watch_dir = watch_dir_of(cfg)
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
    watch_dir = watch_dir_of(cfg)
    with open(os.path.join(watch_dir, "watch.pid"), "w") as f:
        f.write(str(os.getpid()))
    print(f"[pid] {os.getpid()} 已写入 {os.path.join(watch_dir, 'watch.pid')}")


def stop_daemon(cfg):
    """--stop：读 pid 文件 SIGTERM 优雅停止常驻 watch。"""
    watch_dir = watch_dir_of(cfg)
    pid_file = os.path.join(watch_dir, "watch.pid")
    if not os.path.exists(pid_file):
        print(f"无 pid 文件（{pid_file}），没有在跑的 watch。")
        return 0
    with open(pid_file) as f:
        pid = int(f.read().strip())
    try:
        os.kill(pid, signal.SIGTERM)
        print(f"已向 {pid} 发送 SIGTERM（优雅退出：当前轮结束后停）。")
    except ProcessLookupError:
        print(f"进程 {pid} 不存在（可能已退出），清理 pid 文件。")
    os.remove(pid_file)
    return 0


def _append_log(log_file, line):
    """追加一行 watch.log；写失败（如 SMB 断开）只降级到 stderr，绝不抛异常杀守护。"""
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


def main_loop(cfg, once=False):
    """自循环守护：每轮遍历全部项目 -> 检测 -> 取全局第一条需增量 -> 触发 claude -> sleep。"""
    watch_dir = watch_dir_of(cfg)
    os.makedirs(watch_dir, exist_ok=True)
    log_file = os.path.join(watch_dir, "watch.log")
    round_no = 0
    while True:
        # 顶部统一检查退出标志：检测失败分支的 continue / sleep 被信号中断后
        # 都会回到这里，确保 SIGTERM(优雅 stop) 在任何路径下都能让守护退出
        if _STOP:
            break
        round_no += 1
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{stamp}] round#{round_no} 开始检测（{len(cfg['projects'])} 项目）...")
        results = []
        try:
            for proj in cfg["projects"]:
                res = detect_project(cfg["project_dir"], proj, watch_dir)
                results.append(res)
        except Exception as e:
            # 网络异常/probe 超时/SMB 断开等单轮检测失败：记日志后下一轮重试，绝不杀守护
            print(f"[{stamp}] round#{round_no} 检测失败：{e}", file=sys.stderr)
            _append_log(log_file, f"[{stamp}] round#{round_no} 检测失败：{e}")
            if once:
                break
            _interruptible_sleep(cfg["interval"])
            continue

        try:
            report_path = write_report(cfg, results)
            print(f"[{stamp}] round#{round_no} 报告已写：{report_path}")
        except Exception as e:
            # 报告落工程 .icode_output/，SMB 断开等写失败不崩守护，仅降级提示
            print(f"[{stamp}] round#{round_no} 报告写入失败：{e}", file=sys.stderr)
            _append_log(log_file, f"[{stamp}] round#{round_no} 报告写入失败：{e}")

        # 候选 = 需增量 + 待新建（待新建自动建 debug 基线），跨项目合并按单号倒序取单号最大的一条
        candidates = []
        for proj, res in zip(cfg["projects"], results):
            for item in res["need_incremental"] + res["pending_new"]:
                candidates.append((proj, item))
        candidates.sort(key=lambda x: x[1][0], reverse=True)
        try:
            if candidates and not cfg.get("detect_only"):
                proj, (num, label, _it, _wdir, reason, _mp) = candidates[0]
                rc, cost = trigger_claude(cfg, proj, num, label, reason)
                _append_log(log_file, f"[{stamp}] round#{round_no} 触发 {label}（{reason}）：{'成功' if rc == 0 else '失败/超时'}，耗时{cost}s")
            elif candidates:
                print(f"[{stamp}] round#{round_no} (detect-only) 候选 {candidates[0][1][1]}，未触发 claude")
            else:
                print(f"[{stamp}] round#{round_no} 全部项目无新增内容且无待新建")
        except Exception as e:
            # 触发 claude 意外异常（含写 watch.log 失败）不崩守护，仅降级提示
            print(f"[{stamp}] round#{round_no} 触发/记录失败：{e}", file=sys.stderr)
            _append_log(log_file, f"[{stamp}] round#{round_no} 触发/记录失败：{e}")

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
            pid_file = os.path.join(watch_dir_of(cfg), "watch.pid")
            if os.path.exists(pid_file):
                os.remove(pid_file)


if __name__ == "__main__":
    main()
