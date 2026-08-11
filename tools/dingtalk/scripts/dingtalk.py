#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
钉钉文档/钉盘 (alidocs.dingtalk.com) 访问工具。

子命令:
  auth      解密本机 Chrome cookie，生成钉钉登录态 cookie jar
  resolve   把节点链接 /i/nodes/{token} 解析成 folder 的 dentryUuid/spaceId/corpId
  ls        列出某文件夹的子项
  download  下载某文件（真实上传的 xlsx/pdf/docx 等）

设计说明（这些是反复验证过的关键点，别改）:
  - API 基址是 /box/api/v2/... ，不是 /api/ 也不是 /i/api/（那两个 404）
  - 列表/下载接口要带请求头 X-XSRF-TOKEN: <alidocs XSRF-TOKEN cookie 的值>
    （头名大小写敏感，写成 x-csrf-token 会 403）
  - 列表是 GET，下载是 GET；POST 会 405
  - 节点页 /i/nodes/{token} 是 SPA，对文件夹而言 token 就是它的 dentryUuid；
    spaceId 从页面预加载的预览 URL (?spaceId=数字&...dentryUuid=token) 里抠
  - cookie 用 browser_cookie3 解密（Chrome v11/v2，AES-128-CBC，自动处理
    v11_empty_key 与 version>=24 的域名 SHA256 前缀）
"""
import argparse, json, os, re, subprocess, sys, glob

API = "https://alidocs.dingtalk.com"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36")
JAR = os.path.expanduser("~/.cache/dingtalk-cookies.txt")
CHROME_PROFILES = ["Default", "Profile 1", "Profile 2", "Guest Profile"]


# -------------------------- HTTP helpers (curl) --------------------------
def curl(url, jar=JAR, headers=None, out=None, max_time=60):
    cmd = ["curl", "-sL", "-b", jar, "-A", UA, "--max-time", str(max_time),
           "-w", "\n__HTTP=%{http_code}__"]
    for h in (headers or []):
        cmd += ["-H", h]
    cmd.append(url)
    if out:
        cmd += ["-o", out]
        r = subprocess.run(cmd, capture_output=True, text=True)
        code = (r.stdout.rsplit("__HTTP=", 1)[-1].strip()
                if "__HTTP=" in r.stdout else "?")
        return code
    r = subprocess.run(cmd, capture_output=True, text=True)
    body, _, code = r.stdout.rpartition("__HTTP=")
    return body, code.strip()


def get_xsrf():
    """从 cookie jar 读 alidocs.dingtalk.com 的 XSRF-TOKEN 值。"""
    if not os.path.exists(JAR):
        die("cookie jar 不存在，先跑: dingtalk.py auth")
    with open(JAR) as f:
        for line in f:
            parts = line.split("\t")
            if len(parts) >= 7 and "alidocs.dingtalk.com" in parts[0] and parts[5] == "XSRF-TOKEN":
                return parts[6].strip()
    die("cookie jar 里没有 alidocs XSRF-TOKEN，确认已登录钉钉文档后重跑 auth")


def api_headers():
    return [f"X-XSRF-TOKEN: {get_xsrf()}",
            "Referer: https://alidocs.dingtalk.com/",
            "Origin: https://alidocs.dingtalk.com"]


def die(msg, code=1):
    print("ERROR:", msg, file=sys.stderr)
    sys.exit(code)


# -------------------------- auth --------------------------
def cmd_auth(args):
    """解密 Chrome cookie，写 Netscape cookie jar（只含 *.dingtalk.com）。"""
    try:
        import browser_cookie3
    except ImportError:
        die("缺 browser_cookie3，装一下: pip install browser_cookie3")
    best = None  # (count, profile, cookiejar)
    for prof in CHROME_PROFILES:
        p = os.path.expanduser("~/.config/google-chrome/%s/Cookies" % prof)
        if not os.path.exists(p):
            continue
        try:
            cj = browser_cookie3.chrome(cookie_file=p, domain_name="dingtalk")
        except Exception as e:
            print("  (skip %s: %s)" % (prof, e), file=sys.stderr)
            continue
        n = sum(1 for _ in cj)
        print("  profile %-13s -> %d dingtalk cookies" % (prof, n), file=sys.stderr)
        if best is None or n > best[0]:
            best = (n, prof, cj)
    if not best or best[0] == 0:
        die("任何 Chrome profile 都没找到 dingtalk cookie。"
            "先用 Chrome 登录钉钉文档 alidocs.dingtalk.com 再试。")
    _, prof, cj = best
    os.makedirs(os.path.dirname(JAR), exist_ok=True)
    lines = ["# Netscape HTTP Cookie File (dingtalk, profile %s)" % prof]
    for c in cj:
        host = c.domain
        inc = "TRUE" if host.startswith(".") else "FALSE"
        exp = int(c.expires) if c.expires else 0
        sec = "TRUE" if c.secure else "FALSE"
        lines.append("\t".join([host, inc, c.path, sec, str(exp), c.name, c.value]))
    with open(JAR, "w") as f:
        f.write("\n".join(lines) + "\n")
    os.chmod(JAR, 0o600)
    print("OK cookie jar -> %s (%d cookies, profile %s)" % (JAR, best[0], prof))
    print("corp_id_hint=" + (_cookie_val("portal_corp_id") or "<none>"))


def _cookie_val(name):
    if not os.path.exists(JAR):
        return None
    with open(JAR) as f:
        for line in f:
            parts = line.split("\t")
            if len(parts) >= 7 and parts[5] == name:
                return parts[6].strip()
    return None


# -------------------------- resolve --------------------------
def cmd_resolve(args):
    """节点链接 -> folder 的 dentryUuid / spaceId / corpId。"""
    token = args.token.rstrip("/").split("/nodes/")[-1].split("?")[0]
    if not token:
        die("无法从 %s 解析 token" % args.token)
    html, code = curl("%s/i/nodes/%s" % (API, token))
    if "login.dingtalk.com" in html or "统一身份认证" in html:
        die("cookie 已失效/未登录，重跑: dingtalk.py auth")
    if not html:
        die("节点页空 (HTTP %s)，token 是否正确？" % code)
    # 预览 URL 形如 ?spaceId=2674888132&...&dentryUuid={token}&cloudSpaceDentryId=...
    m = re.search(r"\?spaceId=(\d+)[^\"']*dentryUuid=" + re.escape(token), html)
    space_id = m.group(1) if m else None
    if not space_id:
        # 退而求其次：任意 ?spaceId=数字
        m = re.search(r"\?spaceId=(\d+)\b", html)
        space_id = m.group(1) if m else None
    corp_id = _cookie_val("portal_corp_id") or ""
    print("dentryUuid=%s" % token)
    print("spaceId=%s" % (space_id or "<未找到，用 --space-id 指定>"))
    print("corpId=%s" % (corp_id or "<未找到，用 --corp-id 指定>"))
    print("node_url=%s/i/nodes/%s" % (API, token))


# -------------------------- ls --------------------------
def cmd_ls(args):
    """列出文件夹子项。"""
    space_id = args.space_id or _guess_space_id(args.dentry_uuid)
    corp_id = args.corp_id or _cookie_val("portal_corp_id") or ""
    if not space_id:
        die("没有 spaceId。先 resolve 节点链接，或用 --space-id 指定。")
    url = ("%s/box/api/v2/dentry/list?spaceId=%s&corpId=%s&dentryUuid=%s&limit=%d"
           % (API, space_id, corp_id, args.dentry_uuid, args.limit))
    body, code = curl(url, headers=api_headers())
    try:
        d = json.loads(body)
    except Exception:
        die("list 返回非 JSON (HTTP %s): %s" % (code, body[:200]))
    if not d.get("isSuccess"):
        die("list 失败 (HTTP %s): %s" % (code, body[:300]))
    children = d["data"].get("children", [])
    rows = []
    for c in children:
        if c.get("dentryUuid") == args.dentry_uuid:
            continue  # 跳过文件夹自身/祖先标记
        st = c.get("dentryStatistic", {}) or {}
        rows.append({
            "type": c.get("dentryType"),
            "name": c.get("name"),
            "ext": c.get("extension") or "",
            "children": st.get("childrenCount"),
            "dentryUuid": c.get("dentryUuid"),
            "dentryId": c.get("dentryId"),
        })
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        print("共 %d 项" % len(rows))
        for r in rows:
            print("  [%-7s] %-44s ext=%-6s children=%-3s uuid=%s"
                  % (r["type"], r["name"], r["ext"], r["children"], r["dentryUuid"]))


def _guess_space_id(dentry_uuid):
    """若 cache 里存过该 uuid 的 spaceId 就用，否则 None。"""
    cache = JAR + ".spaces"
    if os.path.exists(cache):
        m = {l.split("=")[0]: l.split("=")[1]
             for l in open(cache) if "=" in l}
        return m.get(dentry_uuid)
    return None


# -------------------------- download --------------------------
def cmd_download(args):
    """下载文件到本地（仅真实上传的文件；.axls/.doci 原生格式需另导出）。"""
    space_id = args.space_id or _guess_space_id(args.dentry_uuid)
    corp_id = args.corp_id or _cookie_val("portal_corp_id") or ""
    if not space_id:
        die("没有 spaceId。用 --space-id 指定，或先 resolve 父文件夹。")
    url = ("%s/box/api/v2/file/download?dentryUuid=%s&spaceId=%s&corpId=%s"
           % (API, args.dentry_uuid, space_id, corp_id))
    body, code = curl(url, headers=api_headers())
    try:
        d = json.loads(body)
        oss = d["data"]["ossUrlPreSignatureInfo"]["preSignUrls"][0]
    except Exception:
        die("download 接口异常 (HTTP %s): %s" % (code, body[:300]))
    out = args.output or oss.split("?")[0].rsplit("/", 1)[-1] or "dingtalk_file.bin"
    code = curl(oss, out=out, max_time=180)
    print("OK -> %s (HTTP %s, %s bytes)" % (out, code, os.path.getsize(out) if os.path.exists(out) else "?"))


# -------------------------- main --------------------------
def main():
    ap = argparse.ArgumentParser(description="钉钉文档/钉盘访问工具")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("auth", help="解密 Chrome cookie 生成登录态 jar")

    r = sub.add_parser("resolve", help="节点链接 -> dentryUuid/spaceId/corpId")
    r.add_argument("token", help="节点链接或 token (https://alidocs.dingtalk.com/i/nodes/{token})")

    l = sub.add_parser("ls", help="列出文件夹子项")
    l.add_argument("dentry_uuid", help="文件夹 dentryUuid")
    l.add_argument("--space-id")
    l.add_argument("--corp-id")
    l.add_argument("--limit", type=int, default=200)
    l.add_argument("--json", action="store_true")

    d = sub.add_parser("download", help="下载文件")
    d.add_argument("dentry_uuid", help="文件 dentryUuid")
    d.add_argument("-o", "--output")
    d.add_argument("--space-id")
    d.add_argument("--corp-id")

    args = ap.parse_args()
    {"auth": cmd_auth, "resolve": cmd_resolve, "ls": cmd_ls, "download": cmd_download}[args.cmd](args)


if __name__ == "__main__":
    main()
