#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Windows 版：从本机 Chrome 解密 Teambition cookie -> .tb_cookie（供 tb_pull.py 使用）。

与 Linux 版 tb_cookie.py 不同：Windows Chrome 的 cookie 用 DPAPI 加密（Local State 的
encrypted_key + AES-128-CBC），不依赖 secretstorage/keyring。本脚本仅用标准库
（ctypes 调 crypt32 / sqlite3）。

**最小可见性**：只处理 --domain 指定的站点（默认 tb.orbbec.com），绝不读取/解密其他站点。

**前置**：Chrome 必须已关闭——运行中的 Chrome 会对 cookie DB 加独占锁，外部进程无法读取
（实测 Copy-Item / sqlite 只读 / CreateFile 全共享均被拒）。

**依赖**：Python + cryptography（与 Linux 版 tb_cookie.py 相同）。

用法：
  python tb_cookie_win.py                       # 默认域名 tb.orbbec.com -> 脚本同目录 .tb_cookie
  python tb_cookie_win.py --domain tb.orbbec.com --out <path>
"""
import argparse, base64, ctypes, ctypes.wintypes, hashlib, json, os, shutil, sqlite3, sys, tempfile

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", ctypes.wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def dpapi_unprotect(blob):
    """调用 CryptUnprotectData（当前用户 DPAPI，无需口令）。"""
    inb = DATA_BLOB(len(blob), ctypes.cast(ctypes.create_string_buffer(blob), ctypes.POINTER(ctypes.c_char)))
    outb = DATA_BLOB()
    ok = ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(inb), None, None, None, None, 0, ctypes.byref(outb))
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(outb.pbData, outb.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(outb.pbData)


def load_aes_key(local_state_path):
    """从 Chrome Local State 解出 cookie 的 AES key（DPAPI）。"""
    with open(local_state_path, encoding="utf-8") as f:
        ls = json.load(f)
    ek = base64.b64decode(ls["os_crypt"]["encrypted_key"])
    if not ek.startswith(b"DPAPI"):
        raise RuntimeError(f"未知的 encrypted_key 前缀：{ek[:5]!r}（可能为 app-bound v20，需用户手动粘贴）")
    key = dpapi_unprotect(ek[5:])
    if len(key) not in (16, 32):
        raise RuntimeError(f"AES key 长度异常：{len(key)}")
    return key


def decrypt_cookie(ev, key, host):
    """解密 Chrome cookie（Windows 现代格式，实测确认）：

    encrypted_value = "v10" + nonce(12) + AES-256-GCM(ciphertext + tag16)
    明文前 32 字节 = sha256(host_key)（domain 哈希），去掉后即 cookie 值。
    返回明文或 None。
    """
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    if ev[:3] != b"v10":
        return None
    if len(ev) < 3 + 12 + 16 + 1:
        return None
    nonce = ev[3:15]
    try:
        pt = AESGCM(key).decrypt(nonce, ev[15:], None)
    except Exception:
        return None
    body = pt
    h = hashlib.sha256(host.encode()).digest()
    if len(pt) > 32 and pt[:32] == h:
        body = pt[32:]
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        return None


def read_cookies(db_path, domain):
    """读取指定域名相关 cookie，返回 [(host_key, name, encrypted_value), ...]。

    同时覆盖当前域与父域（例如 tb.orbbec.com + .orbbec.com）——Teambition 的
    teambition_private_sid / TB_GTA 等关键字段挂在父域 .orbbec.com 上。
    """
    parts = domain.split(".", 1)
    parent = parts[1] if len(parts) == 2 else domain
    patterns = (domain, parent, "." + parent)
    tmp = tempfile.mktemp(suffix=".db")
    try:
        shutil.copy2(db_path, tmp)
    except OSError as e:
        raise RuntimeError(f"复制 cookie DB 失败（Chrome 是否在运行？）：{e}") from e
    try:
        con = sqlite3.connect(f"file:{tmp}?immutable=1", uri=True)
        try:
            rows = con.execute(
                "select host_key, name, encrypted_value from cookies "
                "where host_key in (?, ?, ?) order by host_key, name",
                patterns).fetchall()
        finally:
            con.close()
        return rows
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def main():
    ap = argparse.ArgumentParser(description="Windows 版：解密 Chrome Teambition cookie -> .tb_cookie")
    ap.add_argument("--domain", default="tb.orbbec.com", help="TB 域名（默认 tb.orbbec.com）")
    ap.add_argument("--out", default=os.path.join(SCRIPT_DIR, ".tb_cookie"), help="输出文件（默认脚本同目录 .tb_cookie）")
    args = ap.parse_args()

    local = os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\User Data")
    ls_path = os.path.join(local, "Local State")
    db_path = os.path.join(local, "Default", "Network", "Cookies")
    if not os.path.exists(ls_path) or not os.path.exists(db_path):
        print(f"[error] 未找到 Chrome cookie DB：\n  {db_path}\n  请确认用 Chrome 登录过 {args.domain}（Default profile）。", file=sys.stderr)
        sys.exit(1)

    key = load_aes_key(ls_path)
    rows = read_cookies(db_path, args.domain)
    if not rows:
        print(f"[error] 未找到 {args.domain} 相关 cookie（请先在 Chrome 登录 {args.domain}）。", file=sys.stderr)
        sys.exit(1)

    WANT = ["TB_ACCESS_TOKEN", "sl-session", "teambition_private_sid",
            "teambition_private_sid.sig", "teambition_lang", "TB_TENANT_TYPE", "TB_GTA"]
    got = {}
    skipped = 0
    for host, name, ev in rows:
        val = decrypt_cookie(ev, key, host)
        if val is None:
            skipped += 1
            continue
        if name not in got:
            got[name] = val
    if not got:
        print(f"[error] 解密失败 {len(rows)} 条（skipped={skipped}）。可能是 Chrome app-bound(v20) 加密或 key 不匹配。"
              f"\n  请改用手动方式：Chrome DevTools -> Network -> 复制 tb.orbbec.com 请求的 Cookie 头 -> 粘贴到 {args.out}", file=sys.stderr)
        sys.exit(1)

    pairs, seen = [], set()
    for n in WANT + [x for x in got if x not in WANT]:
        if n in got and n not in seen and got[n].strip():
            seen.add(n)
            pairs.append(f"{n}={got[n]}")
    header = "; ".join(pairs)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(header)
    try:
        os.chmod(args.out, 0o600)
    except OSError:
        pass

    ok = all(k in got for k in ("TB_ACCESS_TOKEN", "sl-session", "teambition_private_sid"))
    print(f"[ok] 解出 {len(got)} 条 / 解密跳过 {skipped} 条 -> {args.out}（{len(pairs)} 字段 / {len(header)} 字节）")
    print(f"[ok] 关键字段齐全：{ok}")
    if not ok:
        print("[warn] 关键鉴权字段缺失，cookie 可能未登录或已过期——请在 Chrome 重新登录后重跑。", file=sys.stderr)


if __name__ == "__main__":
    main()
