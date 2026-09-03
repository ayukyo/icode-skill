#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 Teambition（tb.example.com）拉取缺陷单：列缺陷 / 拉单个缺陷的详情+真实评论+下载日志附件。

**只读·严禁回写**：本脚本只用 GET 拉取数据，无任何 POST / 写评论 / 回写 TB 的逻辑；
分析结论只产出到本地，绝不自动回写 TB。禁止在本脚本中添加任何写 TB 的代码（包括 POST 评论、上传附件等）。

通用、参数化：项目与缺陷库通过 config 的 projects 字典配置（lib 前缀 -> pid/label/url），
不绑定任何具体项目。--pid 权威：传入则优先用它做 task 查询，支持从 TB 项目 URL 取 pid、
即使该项目未在 config 登记也能拉取。

子命令：
  list    列缺陷（uniqueId / 标题 / 附件数 / 状态 / 更新时间；--with-status 拉详情显示真实任务流状态名）
  defect  拉指定缺陷：拉 activities 拿真实评论 + 附件，下载附件（日志）到 {out}/{ID}/，写 {ID}_meta.json（--meta-only 不下载附件）
  probe   批量探测：list 全量 + 每单拉状态名/评论/附件元数据（不下载附件），按状态名过滤写 probe.json

依赖：requests。cookie 由 tb_cookie.py 生成（或手动粘贴到脚本同目录 .tb_cookie）。

用法示例：
  python3 tb_pull.py --lib DEMO list
  python3 tb_pull.py list --lib DEMO --json
  python3 tb_pull.py --lib DEMO list --with-status
  python3 tb_pull.py defect DEMO-26
  python3 tb_pull.py defect DEMO-26 --meta-only
  python3 tb_pull.py --lib DEMO probe --status-names 打开,未完成
  python3 tb_pull.py --pid <项目ID> defect DEMO-26 --out ~/work/log
"""
import argparse, json, os, re, shutil, socket, sys
import requests

# Windows 下 stdout/stderr 默认 locale 编码(gbk)，中文输出/JSON 会乱码或 UnicodeEncodeError；强制 UTF-8
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def normalize_domain(domain):
    """清洗 --domain / config.domain：去空白、剥 scheme(https:// http://)、去路径与尾部斜杠。

    防止误传 'https://tb.example.com' 导致 host 拼接错误。幂等：纯域名（含端口）原样返回。
    """
    if not domain:
        return domain
    d = str(domain).strip()
    had_scheme = bool(re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", d))
    if had_scheme:
        d = re.sub(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", "", d)
    d = d.split("/", 1)[0].rstrip("/")
    if had_scheme:
        print(f"[warn] domain 误带 scheme，已自动剥掉：'{domain}' -> '{d}'（--domain 应传纯域名，不带 https://）",
              file=sys.stderr)
    return d


def load_config():
    for rel in ("../config.json", "../config.example.json"):
        p = os.path.join(SCRIPT_DIR, rel)
        if os.path.exists(p):
            # 显式 UTF-8：配置含中文（如 status_names），Windows 默认 gbk 解码会 UnicodeDecodeError
            with open(p, encoding="utf-8") as f:
                return json.load(f)
    return {}


def cookie_file(cfg):
    return os.path.join(SCRIPT_DIR, cfg.get("cookie_file", ".tb_cookie"))


def session(cfg):
    cp = cookie_file(cfg)
    if not os.path.exists(cp):
        print(f"[error] cookie 不存在：{cp}\n        先跑：python3 {os.path.join(SCRIPT_DIR, 'tb_cookie.py')}", file=sys.stderr)
        sys.exit(1)
    s = requests.Session()
    s.headers["Cookie"] = open(cp, encoding="utf-8").read().strip()
    return s


def resolve_pid(cfg, lib, pid):
    if pid:
        return pid
    proj = cfg.get("projects", {})
    if lib in proj:
        return proj[lib]["pid"]
    print(f"[error] 未知库 '{lib}'，可用：{list(proj.keys())}（或用 --pid 指定）", file=sys.stderr)
    sys.exit(1)


def base_url(cfg):
    domain = normalize_domain(cfg.get("domain", "tb.example.com"))
    if not domain:
        print("[error] domain 为空或无效，请用 --domain 指定纯域名（如 tb.example.com）", file=sys.stderr)
        sys.exit(1)
    return "https://" + domain


def _is_dns_error(e):
    """沿异常链查是否有 socket.gaierror（DNS 解析失败）。"""
    cause = e
    while cause is not None:
        if isinstance(cause, socket.gaierror):
            return True
        cause = getattr(cause, "__cause__", None)
    return False


def api_get(s, cfg, path, **params):
    url = base_url(cfg) + path
    try:
        r = s.get(url, params=params, timeout=30)
    except requests.exceptions.ConnectionError as e:
        if _is_dns_error(e):
            print(f"[error] DNS 解析失败：{url}\n"
                  f"        检查网络，或 --domain 是否写错（如误带 https://、多了 / 或路径、域名拼错）", file=sys.stderr)
        else:
            print(f"[error] 连接失败：{url}（{type(e).__name__}）\n"
                  f"        检查网络/域名/端口，或稍后重试", file=sys.stderr)
        sys.exit(1)
    except requests.exceptions.Timeout:
        print(f"[error] 请求超时：{url}\n"
              f"        检查网络，或稍后重试", file=sys.stderr)
        sys.exit(1)
    if r.status_code == 401:
        print("[error] 401 鉴权失败--cookie 过期，请重跑 tb_cookie.py", file=sys.stderr)
        sys.exit(1)
    r.raise_for_status()
    return r.json()


def fetch_tasks(s, cfg, pid, status="open"):
    params = {"count": 300}
    if status == "open":
        params["isDone"] = "false"
    elif status == "done":
        params["isDone"] = "true"
    data = api_get(s, cfg, f"/api/projects/{pid}/tasks", **params)
    # 实测：该端点直接返回 list
    return data if isinstance(data, list) else data.get("result", data.get("data", []))


def fetch_activities(s, cfg, tid, count=100):
    data = api_get(s, cfg, f"/api/v2/tasks/{tid}/activities", count=count)
    return data.get("result", data) if isinstance(data, dict) else data


def fetch_task_detail(s, cfg, tid):
    """拉单个任务详情。list 接口的 task 对象不含任务流状态名，须用详情内嵌的 taskflowstatus.name（如"打开/未完成"）。"""
    return api_get(s, cfg, f"/api/v2/tasks/{tid}")


def status_name(task_detail):
    """从任务详情提取任务流状态名（taskflowstatus.name），无则空串。

    isDone 与任务流状态不同步（实测 isDone=True 但状态可为"未完成"），
    按状态过滤必须用状态名、不能用 isDone。"""
    ts = (task_detail or {}).get("taskflowstatus") or {}
    return ts.get("name") or ""


# ---------- list ----------

def cmd_list(args):
    cfg = load_config()
    if args.domain is not None: cfg["domain"] = args.domain
    s = session(cfg)
    pid = resolve_pid(cfg, args.lib, args.pid)
    if args.status == "all":
        tasks = fetch_tasks(s, cfg, pid, "open") + fetch_tasks(s, cfg, pid, "done")
    else:
        tasks = fetch_tasks(s, cfg, pid, args.status)
    tasks.sort(key=lambda t: (t.get("uniqueId") or 0))

    # --with-status：list 接口不含任务流状态名，逐单拉详情补真实状态名（如"打开/未完成"，不能用 isDone 推断）
    if args.with_status:
        for t in tasks:
            t["status_name"] = status_name(fetch_task_detail(s, cfg, t.get("_id")))

    if args.json:
        json.dump(tasks, sys.stdout, ensure_ascii=False, indent=2)
        print()
        return

    lib = args.lib or pid
    print(f"=== {lib} 缺陷（{len(tasks)} 条，status={args.status}）===")
    print(f"{'ID':10} {'附件':>4} {'状态':<6} {'更新':12} 标题")
    for t in tasks:
        uid = t.get("uniqueId", "")
        label = f"{args.lib}-{uid}" if args.lib and uid else str(uid)
        done = t.get("status_name") or ("完成" if t.get("isDone") else "进行")
        upd = (t.get("updated") or "")[:10] or "-"
        print(f"{label:10} {str(t.get('attachmentsCount', 0)):>4} {done:<6} {upd:12} {(t.get('content') or '')[:50]}")


# ---------- defect ----------

def find_task(s, cfg, ident, fallback_lib=None, fallback_pid=None):
    """ident 支持：LIB-NUM / 纯数字（需 --lib 或 --pid）/ task _id。返回 (task, lib)。
    --pid 权威：传入则优先用它做 task 查询（支持从 TB 项目 URL 取 pid、未在 config 登记的项目）。"""
    ident = str(ident).strip()
    m = re.match(r"^([A-Z]+)-(\d+)$", ident)
    if m:
        return _search_unique(s, cfg, m.group(1), int(m.group(2)), fallback_pid), m.group(1)
    if ident.isdigit() and (fallback_lib or fallback_pid):
        return _search_unique(s, cfg, fallback_lib, int(ident), fallback_pid), fallback_lib
    # 当作 _id：需要 pid（--pid 优先，否则按 --lib 解析）
    pid = fallback_pid or resolve_pid(cfg, fallback_lib, None)
    for t in fetch_tasks(s, cfg, pid, "open") + fetch_tasks(s, cfg, pid, "done"):
        if t.get("_id") == ident:
            return t, None
    print(f"[error] 找不到 task _id={ident}", file=sys.stderr)
    sys.exit(1)


def _search_unique(s, cfg, lib, num, pid_override=None):
    pid = pid_override or resolve_pid(cfg, lib, None)
    for st in ("open", "done"):
        for t in fetch_tasks(s, cfg, pid, st):
            if t.get("uniqueId") == num:
                return t
    print(f"[error] 找不到 {lib}-{num}" if lib else f"[error] 找不到 uniqueId={num}", file=sys.stderr)
    sys.exit(1)


def safe_filename(name, ext):
    name = (name or "file").strip()
    ext = (ext or "").strip().lstrip(".")
    if ext and not name.lower().endswith("." + ext.lower()):
        return f"{name}.{ext}"
    return name


def download(s, url, dest):
    with s.get(url, timeout=120, stream=True) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(65536):
                f.write(chunk)
    return os.path.getsize(dest)


def unique_path(dest):
    if not os.path.exists(dest):
        return dest
    base, e = os.path.splitext(dest)
    n = 1
    while os.path.exists(f"{base}_{n}{e}"):
        n += 1
    return f"{base}_{n}{e}"


def collect_files(activities):
    """从评论类 activity 抽取所有附件。兼容 action=activity.comment / activity.comment.attachments（两者 content 都可能含 files[]/attachments[]），content.files[]（name/url/ext/size）与 content.attachments[]（fileName/downloadUrl/fileSize/fileType，含图片 imageHeight/imageWidth）两种字段都抽。"""
    files = []
    for a in activities:
        if not (a.get("action") or "").startswith("activity.comment"):
            continue
        content = a.get("content") or {}
        for key in ("files", "attachments"):
            for f in content.get(key) or []:
                files.append(f)
    return files


def cmd_defect(args):
    cfg = load_config()
    if args.domain is not None: cfg["domain"] = args.domain
    s = session(cfg)
    task, lib = find_task(s, cfg, args.id, args.lib, args.pid)
    tid = task["_id"]
    uid = task.get("uniqueId")
    label = f"{lib}-{uid}" if (lib and uid) else (args.id if not uid else str(uid))
    print(f"=== {label}：{task.get('content', '')} ===")

    detail = fetch_task_detail(s, cfg, tid)
    st = status_name(detail)
    activities = fetch_activities(s, cfg, tid)
    comments = [a for a in activities if (a.get("action") or "").startswith("activity.comment")]
    files = collect_files(activities)

    out_root = args.out or cfg.get("log_root", "~/work/log")
    dest_dir = os.path.join(os.path.expanduser(out_root), label)
    os.makedirs(dest_dir, exist_ok=True)

    downloaded = []
    if args.meta_only:
        print(f"  [meta-only] 不下载附件（{len(files)} 个附件仅记录清单），批量探测/复用预筛用")
    else:
        for f in files:
            fname = safe_filename(f.get("name") or f.get("fileName"), f.get("ext") or f.get("fileType"))
            dest = unique_path(os.path.join(dest_dir, fname))
            url = f.get("url") or f.get("downloadUrl") or ""
            try:
                sz = download(s, url, dest)
            except Exception as e:
                # token 可能过期 -> 刷新 activities 重取同 (name,ext) 的 url
                print(f"  [retry] {fname} 下载失败（{type(e).__name__}），刷新 token 重试...")
                url = _refresh_url(s, cfg, tid, f.get("name") or f.get("fileName"), f.get("ext") or f.get("fileType"))
                if not url:
                    print(f"  [fail] {fname}：刷新后仍无可用 url")
                    continue
                try:
                    sz = download(s, url, dest)
                except Exception as e2:
                    print(f"  [fail] {fname}：{e2}")
                    continue
            downloaded.append({"name": fname, "path": os.path.relpath(dest, dest_dir), "size": sz})
            print(f"  [ok] {fname}（{sz:,} bytes）")

    meta = {
        "id": label, "title": task.get("content"), "note": task.get("note"),
        "uniqueId": uid, "_id": tid,
        # 真实任务流状态名（list 接口不含；isDone 与状态名不同步——实测 isDone=True 但状态可为"未完成"）
        "status": st, "_taskflowstatusId": detail.get("_taskflowstatusId"),
        "isDone": detail.get("isDone"), "updated": detail.get("updated"),
        "attachmentsCount_cache": task.get("attachmentsCount"),
        "comments": [{"action": a.get("action"), "created": a.get("created"),
                      "content": a.get("content")} for a in comments],
        "files": [{"name": f.get("name") or f.get("fileName"),
                   "ext": f.get("ext") or f.get("fileType"),
                   "mimeType": f.get("mimeType") or f.get("contentType") or f.get("fileType"),
                   "size": f.get("size") if f.get("size") is not None else f.get("fileSize"),
                   "url": f.get("url") or f.get("downloadUrl")} for f in files],
        "downloaded": downloaded,
        "dir": dest_dir,
    }
    meta_path = os.path.join(dest_dir, f"{label}_meta.json")
    prev_meta_path = os.path.join(dest_dir, f"{label}_meta.prev.json")
    if os.path.exists(meta_path):
        shutil.copy2(meta_path, prev_meta_path)
        print(f"  [backup] 旧 meta 已备份 -> {prev_meta_path}（供同单续分析时增量对比：prev vs current）")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    print(f"\n[ok] 日志目录：{dest_dir}")
    print(f"[ok] 元信息：{meta_path}")
    print(f"[ok] 状态：{st} / 评论 {len(comments)} 条 / 附件 {len(files)} 个 / 下载 {len(downloaded)} 个")


# ---------- probe ----------

def cmd_probe(args):
    """批量探测：list 全量 + 每单拉状态名/评论/附件元数据（不下载附件），按任务流状态名过滤，写 probe.json。

    用途：/icode log 批量 TB 分析（分析所有"打开/未完成"单）的枚举+探测阶段，零附件下载；
    每单 items[] 含 status/comments/files/updated，供与 index.json 旧工单 meta 对比分流。"""
    cfg = load_config()
    if args.domain is not None: cfg["domain"] = args.domain
    s = session(cfg)
    pid = resolve_pid(cfg, args.lib, args.pid)
    status_names = [x.strip() for x in (args.status_names or "打开,未完成").split(",") if x.strip()]
    # 必须 open+done 两批枚举（fetch_tasks("all") 是不带 isDone 过滤的单次调用，会漏 isDone=True 但状态"未完成"的单）
    tasks = fetch_tasks(s, cfg, pid, "open") + fetch_tasks(s, cfg, pid, "done")
    result = []
    for t in tasks:
        tid = t.get("_id")
        detail = fetch_task_detail(s, cfg, tid)
        st = status_name(detail)
        if status_names and st not in status_names:
            continue
        activities = fetch_activities(s, cfg, tid)
        comments = [a for a in activities if (a.get("action") or "").startswith("activity.comment")]
        files = collect_files(activities)
        result.append({
            "uniqueId": t.get("uniqueId"),
            "lib": args.lib,
            "_id": tid,
            "status": st,
            "_taskflowstatusId": detail.get("_taskflowstatusId"),
            "isDone": detail.get("isDone"),
            "updated": detail.get("updated") or t.get("updated"),
            "title": t.get("content"),
            "comments_count": len(comments),
            "comments": [{"created": c.get("created"),
                          "comment": (c.get("content") or {}).get("comment"),
                          "files": [(f.get("name") or f.get("fileName"))
                                    for f in ((c.get("content") or {}).get("files") or [])]}
                         for c in comments],
            "files": [{"name": f.get("name") or f.get("fileName"),
                       "ext": f.get("ext") or f.get("fileType"),
                       "size": f.get("size") if f.get("size") is not None else f.get("fileSize"),
                       "mimeType": f.get("mimeType") or f.get("contentType")} for f in files],
        })
    out_dir = os.path.expanduser(args.out or "~/.claude/icode_data/tb_probe")
    os.makedirs(out_dir, exist_ok=True)
    probe_path = os.path.join(out_dir, f"{pid}.json")
    with open(probe_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    lib = args.lib or pid
    print(f"=== {lib} 探测（状态名 ∈ {status_names}，{len(result)}/{len(tasks)} 条）===")
    for it in result:
        label = f"{args.lib}-{it['uniqueId']}" if args.lib and it.get("uniqueId") else str(it.get("uniqueId"))
        print(f"{label:10} [{it['status']}] 评论{it['comments_count']} 附件{len(it['files'])} "
              f"{str(it['updated'] or '')[:10]} {(it['title'] or '')[:40]}")
    print(f"[ok] probe.json -> {probe_path}（{len(result)} 条，未下载任何附件）")


def _refresh_url(s, cfg, tid, name, ext):
    try:
        for a in fetch_activities(s, cfg, tid):
            content = a.get("content") or {}
            for key in ("files", "attachments"):
                for f in content.get(key) or []:
                    fn = f.get("name") or f.get("fileName")
                    fe = f.get("ext") or f.get("fileType")
                    if fn == name and fe == ext:
                        return f.get("url") or f.get("downloadUrl")
    except Exception:
        return None
    return None


def main():
    cfg = load_config()
    libs = list(cfg.get("projects", {}).keys())
    ap = argparse.ArgumentParser(description="从 Teambition 拉取缺陷单（list / defect）")
    ap.add_argument("--lib", choices=libs, help=f"缺陷库前缀：{libs}" if libs else "缺陷库前缀（见 config.projects）")
    ap.add_argument("--pid", help="直接指定 project id（权威：覆盖 --lib，支持 URL 取来的未登记项目）")
    ap.add_argument("--domain", help="Teambition 域名（覆盖 config.domain，支持从 URL 取来、不配 config）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    pl = sub.add_parser("list", help="列缺陷")
    pl.add_argument("--status", choices=["open", "done", "all"], default="open")
    pl.add_argument("--json", action="store_true", help="输出原始 JSON")
    pl.add_argument("--with-status", action="store_true",
                    help="逐单拉详情补真实任务流状态名（list 接口不含；isDone 与状态名不同步，过滤须用它）")
    pl.set_defaults(func=cmd_list)

    pd = sub.add_parser("defect", help="拉单个缺陷：详情 + 真实评论 + 下载日志")
    pd.add_argument("id", help="缺陷标识，如 DEMO-26（或纯数字配 --lib/--pid，或 task _id）")
    pd.add_argument("--out", help="下载根目录（默认 config.log_root）")
    pd.add_argument("--meta-only", action="store_true",
                    help="只拉详情+评论写 meta.json，不下载附件（批量探测/复用预筛用）")
    pd.set_defaults(func=cmd_defect)

    pp = sub.add_parser("probe", help="批量探测：list 全量 + 每单状态名/评论/附件元数据（不下载附件），按状态名过滤写 probe.json")
    pp.add_argument("--status-names", help="过滤的状态名集合，逗号分隔（默认：打开,未完成）")
    pp.add_argument("--out", help="probe.json 输出目录（默认 ~/.claude/icode_data/tb_probe，文件名 <pid>.json）")
    pp.set_defaults(func=cmd_probe)

    args = ap.parse_args()
    if args.domain:
        args.domain = normalize_domain(args.domain)   # 统一清洗，两个子命令拿到纯域名
    args.func(args)


if __name__ == "__main__":
    main()
