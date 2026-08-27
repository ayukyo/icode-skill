#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tb_watch 网页只读查看服务（局域网分享 .icode_output 分析产物）。

由 tb_watch_ctl.sh 集成管理：`start` 时一并拉起（配置 web 段启用才起），
`stop`/`stop --force` 一并停。也可单独运行：

    python3 tb_web.py --config <tb_watch.json>            # 根目录 = 配置 project_dir/.icode_output
    python3 tb_web.py --root <路径> --port 8000            # 覆盖根目录/端口

特性：
- 目录列表页（中文/美化/面包屑/大小/时间，隐藏 dotfile）
- .md 自动渲染为 HTML（markdown 库，charset=utf-8，根治浏览器按错误编码显示乱码）
- 其它文件按类型 inline/下载
- 严格只读：GET/HEAD 之外一律 403；realpath 前缀校验防目录穿越
- SMB(gvfs) 挂载慢：ThreadingHTTPServer 并发，单请求异常不崩服务

配置段（tb_watch.config 顶层 "web"）：
    {"enable": true, "host": "0.0.0.0", "port": 8000}
缺省 = 启用、0.0.0.0、8000。
"""
import argparse
import html
import json
import mimetypes
import os
import posixpath
import sys
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ---------------------------------------------------------------------------
# 页面样式（内联，浅色主题；markdown 渲染页 + 目录列表页共用）
# ---------------------------------------------------------------------------
PAGE_CSS = """
body{margin:0;background:#f5f6f8;color:#24292f;font:14px/1.6 -apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}
.wrap{max-width:980px;margin:24px auto;padding:0 16px}
header{background:#fff;border-bottom:1px solid #d0d7de;padding:14px 0;margin-bottom:20px}
header .wrap{display:flex;align-items:baseline;gap:12px;margin-top:0;margin-bottom:0}
header h1{font-size:18px;margin:0}
header .sub{color:#57606a;font-size:13px}
table{border-collapse:collapse;width:100%;background:#fff;box-shadow:0 1px 3px rgba(31,35,40,.08)}
th,td{text-align:left;padding:8px 12px;border-bottom:1px solid #d0d7de;font-size:13px}
th{background:#f6f8fa;font-weight:600}
tr:hover td{background:#f6f8fa}
a{color:#0969da;text-decoration:none}a:hover{text-decoration:underline}
.crumb{font-size:13px;color:#57606a;margin:0 0 12px}
.crumb a{color:#0969da}
.size{color:#57606a;font-variant-numeric:tabular-nums;white-space:nowrap}
.up{color:#0969da;display:inline-block;margin-bottom:8px}
article{background:#fff;padding:24px 32px;box-shadow:0 1px 3px rgba(31,35,40,.08);overflow-x:auto}
article h1{font-size:22px;border-bottom:1px solid #d0d7de;padding-bottom:.3em}
article h2{font-size:18px;border-bottom:1px solid #eaeef2;padding-bottom:.25em}
article h3{font-size:15px}
article table{border-collapse:collapse;margin:12px 0;box-shadow:none}
article th,article td{border:1px solid #d0d7de;padding:6px 10px}
article pre{background:#f6f8fa;padding:12px;border-radius:6px;overflow-x:auto}
article code{background:#f6f8fa;padding:2px 4px;border-radius:4px;font-size:13px}
article pre code{background:none;padding:0}
article blockquote{border-left:4px solid #d0d7de;color:#57606a;margin:12px 0;padding:2px 12px}
.foot{margin:24px 0 40px;color:#57606a;font-size:12px;text-align:center}
.msg{padding:40px;text-align:center;color:#57606a;background:#fff;box-shadow:0 1px 3px rgba(31,35,40,.08)}
"""


def page(title, body, extra_head=""):
    """组装完整 HTML 页（含 UTF-8 声明，根治中文乱码）。"""
    return ("<!DOCTYPE html><html lang='zh'><head><meta charset='utf-8'>"
            f"<title>{html.escape(title)}</title><style>{PAGE_CSS}</style>{extra_head}</head>"
            f"<body><header><div class='wrap'><h1>📊 tb_watch 分析产物</h1>"
            "<span class='sub'>只读查看 · md 自动渲染</span></div></header>"
            f"<div class='wrap'>{body}<div class='foot'>tb_web · 只读 · 局域网分享</div></div></body></html>")


def fmt_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def fmt_time(ts):
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


def render_md(path, root, key=""):
    """markdown 渲染（tables/fenced_code 扩展支持报告表格与代码块）。"""
    import markdown
    with open(path, encoding="utf-8", errors="replace") as f:
        text = f.read()
    body = markdown.markdown(text, extensions=["tables", "fenced_code"])
    title = next((ln.lstrip("# ").strip() for ln in text.splitlines() if ln.startswith("#")), os.path.basename(path))
    return page(title, f"<div class='crumb'>{breadcrumb_html(path, root, key)}</div><article>{body}</article>")


def _key_prefix(key):
    """URL 前缀：单根(空键)="" → '/'；多根键 "eng" → '/eng/'。"""
    return "/" + (key + "/" if key else "")


def breadcrumb_html(abs_path, root, key=""):
    """生成目录导航（根 / → ... → 当前）。多工程时首段 = 工程键。"""
    parts = []
    rel = os.path.relpath(abs_path, root)
    cur = root
    for seg in rel.split(os.sep):
        if seg in ("", "."):
            continue
        cur = os.path.join(cur, seg)
        parts.append((seg, cur))
    base = _key_prefix(key)
    crumb = [f"<a href='{base}'>{'/' if not key else html.escape(key)}</a>"]
    for name, p in parts:
        crumb.append(f"<a href='{base}{urlenc(os.path.relpath(p, root))}'>{html.escape(name)}</a>")
    return " / ".join(crumb)


def urlenc(s):
    return urllib.parse.quote(s)


def _retry_smb_path(full):
    """gvfsd-smb 负缓存兜底：stat 失败时强制 fuse 重新拉取父目录条目（open+close 临时文件触发 cache 失效）。
    gvfs 跨进程缓存已知缺陷：父目录 ls 可见但 stat 子项返 ENOENT；用创建+立即删除临时文件触发底层 SMB list 重抓。"""
    parent, base = os.path.split(full)
    if not parent or parent == full:
        return full
    # 仅对 SMB 挂载（gvfs）启用兜底，避免污染普通文件系统
    if "/gvfs/" not in parent:
        return full
    try:
        # 在父目录创建+删除一个临时文件，强制 fuse 下一次 listdir 走 SMB
        tmp = os.path.join(parent, f"._wbrefresh_{os.getpid()}")
        with open(tmp, "w") as f:
            pass
        os.unlink(tmp)
        # 重 stat
        if os.path.exists(full):
            return full
    except OSError:
        pass
    return full


class Handler(BaseHTTPRequestHandler):
    server_version = "tb_web/1.0"

    # ---- 只读强制：GET/HEAD 之外一律 403 ----
    def do_POST(self):
        self._deny()
    do_PUT = do_PATCH = do_DELETE = do_MKCOL = do_MOVE = do_POST

    def _deny(self):
        self.send_response(403)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write("只读服务，仅允许查看\n".encode("utf-8"))

    def do_HEAD(self):
        self._serve(only_header=True)

    def do_GET(self):
        self._serve()

    def _serve(self, only_header=False):
        try:
            self._handle(only_header)
        except BrokenPipeError:
            pass
        except Exception as e:
            # 单请求异常不崩服务（SMB 挂载断、文件被守护写入中等）
            try:
                self.send_response(500)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                if not only_header:
                    self.wfile.write(f"读取失败: {e}".encode("utf-8"))
            except Exception:
                pass

    def _resolve(self, roots, rel):
        """多根路由：URL 首段 = 工程键（单根时键为空，保持 /xxx 直达旧行为）。

        返回 (key, root_abs, rel)。未知工程键返回 (None, None, None)。
        单根强制返回空键：即使 load_config 给单工程生成了 basename 键，也不切分 URL、
        不加 key 前缀，完全保持旧行为（否则列表里的链接会多出 /basename 前缀而 404）。
        """
        if len(roots) == 1:
            return "", roots[0][1], rel
        seg, _, rest = rel.partition("/")
        for key, rp in roots:
            if key == seg:
                return key, rp, rest
        return None, None, None

    def _handle(self, only_header):
        roots = self.server.roots
        raw = urllib.parse.unquote(self.path.split("?", 1)[0])
        # 不能用 posixpath.normpath：以 `.` 开头的目录名（如 .debug）会被当相对路径吃掉
        rel = raw.lstrip("/")
        if not rel and len(roots) > 1:
            # 多工程根：首页显示工程列表
            self._index(roots, only_header)
            return
        key, root, rel = self._resolve(roots, rel)
        if key is None:
            self._text(404, "未知工程", only_header)
            return
        full = os.path.realpath(os.path.join(root, rel))
        # gvfsd-smb 负缓存兜底：父目录 ls 可见但 open()/stat() 报不存在（fuse dentry 陈旧）。
        # 用 cd + 重 ls 触发 fetch，避开即时失败。
        if not os.path.exists(full) and not os.path.isdir(full):
            full = _retry_smb_path(full)
        # 目录穿越防护：解析后的路径必须仍在 root 内
        if full != root and not full.startswith(root + os.sep):
            self._text(404, "路径越界", only_header)
            return
        if os.path.isdir(full):
            self._listing(key, root, full, only_header)
        elif os.path.isfile(full):
            self._file(key, root, full, only_header)
        else:
            self._text(404, "文件不存在", only_header)

    def _index(self, roots, only_header):
        """多工程首页：列出全部工程（key + 目录）。"""
        rows = []
        for key, rp in roots:
            name = key or os.path.basename(rp.rstrip(os.sep)) or "root"
            rows.append(f"<tr><td>📁 <a href='{_key_prefix(key)}'>{html.escape(name)}</a></td>"
                        f"<td>{html.escape(rp)}</td></tr>")
        body = ("<div class='crumb'>工程列表</div>"
                "<table><tr><th>工程</th><th>目录</th></tr>" + "".join(rows) + "</table>")
        self._html(200, page("tb_watch 工程列表", body), only_header)

    def _text(self, code, msg, only_header):
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        if not only_header:
            self.wfile.write(page("提示", f"<div class='msg'>{html.escape(msg)}</div>").encode("utf-8"))

    def _listing(self, key, root, full, only_header):
        entries = []
        for name in os.listdir(full):
            if name == ".ico_metadata.json":  # 隐藏元数据文件（debug 工单指纹），避免误导用户进入
                continue
            p = os.path.join(full, name)
            try:
                st = os.stat(p)
                is_dir = os.path.isdir(p)
            except OSError:
                continue
            entries.append((name, is_dir, st.st_size, st.st_mtime))
        entries.sort(key=lambda e: (not e[1], e[0].lower()))  # 目录在前，名称排序
        rows = []
        for name, is_dir, size, mtime in entries:
            # 用根相对路径（/开头），避免进子页后点 .debug/ 被当相对路径解析
            sub_rel = os.path.relpath(full, root).replace(os.sep, "/")
            href = _key_prefix(key) + posixpath.join(sub_rel, urlenc(name)) + ("/" if is_dir else "")
            icon = "📁" if is_dir else ("📄" if name.lower().endswith(".md") else "📎")
            rows.append(f"<tr><td>{icon} <a href='{href}'>{html.escape(name)}</a></td>"
                        f"<td>{'目录' if is_dir else '文件'}</td>"
                        f"<td class='size'>{'—' if is_dir else fmt_size(size)}</td>"
                        f"<td>{fmt_time(mtime)}</td></tr>")
        body = (f"<div class='crumb'>{breadcrumb_html(full, root, key)}</div>"
                f"<table><tr><th>名称</th><th>类型</th><th>大小</th><th>修改时间</th></tr>"
                f"{''.join(rows)}</table>")
        self._html(200, page(os.path.basename(full) or "/", body), only_header)

    def _html(self, code, content, only_header):
        data = content.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if not only_header:
            self.wfile.write(data)

    def _file(self, key, root, full, only_header):
        if full.lower().endswith(".md"):
            # md 渲染成 HTML（charset=utf-8，根治乱码）
            self._html(200, render_md(full, root, key), only_header)
            return
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        try:
            size = os.path.getsize(full)
        except OSError:
            self._text(404, "文件不存在", only_header)
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype + "; charset=utf-8" if ctype.startswith("text/") else ctype)
        self.send_header("Content-Length", str(size))
        self.send_header("Content-Disposition", "inline")
        self.end_headers()
        if only_header:
            return
        with open(full, "rb") as f:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except BrokenPipeError:
                    break


def load_config(path):
    """读 tb_watch 配置，返回 (roots, web_cfg)。

    多工程支持：收集配置里全部工程根（每个 project 的 project_dir，顶层 project_dir 可省作缺省；去重），
    每个工程映射一个 web 键（URL 首段），web 段缺省 = 启用 0.0.0.0:8000。
    roots = [(key, 该工程 .icode_output 绝对路径)]。
    """
    if not os.path.isfile(path):
        sys.exit(f"[tb_web] 配置不存在: {path}")
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    web = cfg.get("web") or {}
    web_cfg = {
        "enable": bool(web.get("enable", True)),
        "host": web.get("host") or "0.0.0.0",
        "port": int(web.get("port") or 8000),
    }
    # 顶层 project_dir 可省（规范写法：每个 project 自带 project_dir）；缺省才回退 cwd
    default_pd = cfg.get("project_dir")
    if not default_pd:
        for p in cfg.get("projects") or []:
            if p.get("project_dir"):
                default_pd = p["project_dir"]
                break
    default_pd = default_pd or os.getcwd()
    dirs = []
    if cfg.get("projects"):
        for p in cfg["projects"]:
            pd = p.get("project_dir") or default_pd
            if pd not in dirs:
                dirs.append(pd)
    else:
        dirs.append(default_pd)
    # 键默认 = 工程目录名；同名（不同父目录）时追加序号避免冲突
    used = {}
    roots = []
    for pd in dirs:
        base = os.path.basename(pd.rstrip(os.sep)) or "root"
        key = base
        n = 1
        while key in used:
            n += 1
            key = f"{base}-{n}"
        used[key] = pd
        roots.append((key, os.path.realpath(os.path.join(pd, ".icode_output"))))
    return roots, web_cfg


def main():
    ap = argparse.ArgumentParser(description="tb_watch 网页只读查看服务")
    ap.add_argument("--config", help="tb_watch 配置 JSON（读 project_dir + web 段）")
    ap.add_argument("--root", help="覆盖根目录（默认 配置project_dir/.icode_output）")
    ap.add_argument("--host", help="覆盖监听地址（默认 0.0.0.0）")
    ap.add_argument("--port", type=int, help="覆盖端口（默认 8000）")
    ap.add_argument("--quiet", action="store_true", help="启动成功不打印访问地址")
    args = ap.parse_args()

    if args.config:
        roots, web_cfg = load_config(args.config)
    else:
        roots, web_cfg = [("", os.path.realpath(os.getcwd()))], {"enable": True, "host": "0.0.0.0", "port": 8000}
    if not web_cfg["enable"]:
        sys.exit("[tb_web] 配置 web.enable=false，退出")
    # --root 覆盖为单根（旧行为）
    if args.root:
        roots = [("", os.path.realpath(args.root))]
    # 只保留真实存在的工程根（挂载未就绪的工程不影响其它工程展示）
    roots = [(k, rp) for k, rp in roots if os.path.isdir(rp)]
    if not roots:
        sys.exit("[tb_web] 无可用根目录（.icode_output 不存在）")
    host = args.host or web_cfg["host"]
    port = args.port or web_cfg["port"]

    srv = ThreadingHTTPServer((host, port), Handler)
    srv.roots = roots
    addrs = "、".join(f"http://{ip}:{port}/" for ip in _local_ips(host))
    roots_txt = "；".join(f"{k or '/'}->{rp}" for k, rp in roots)
    if not args.quiet:
        print(f"[tb_web] 只读查看服务已启动：{addrs}  （工程根: {roots_txt}）")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.shutdown()


def _local_ips(host):
    """返回可对外访问的监听地址列表（绑定 0.0.0.0 时枚举本机非回环 IP）。"""
    if host != "0.0.0.0":
        return [host]
    try:
        import socket
        ips = set()
        for info in socket.getaddrinfo(socket.gethostname(), None):
            ip = info[4][0]
            if not ip.startswith("127."):
                ips.add(ip)
        return sorted(ips) or ["0.0.0.0"]
    except Exception:
        return ["0.0.0.0"]


if __name__ == "__main__":
    main()
