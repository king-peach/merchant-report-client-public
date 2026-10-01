# -*- coding: utf-8 -*-
"""从日常 Chrome 的 Cookie 库提取指定域的 cookie(解密 v10/v11), 注入客户端 profile。
macOS: Chrome Safe Storage 在 Keychain(security 命令查询会弹授权框, 用户点允许)。
只提取白名单域, 不读其他任何站点的 cookie。"""
import os
import sqlite3
import subprocess
import sys
import shutil
import tempfile

DOMAINS = ["melody.shop.ele.me", ".ele.me", ".taobao.com", ".tmall.com", ".alibaba.com"]
SRC = os.path.expanduser("~/Library/Application Support/Google/Chrome/Default/Cookies")


def get_key():
    """Keychain 取 Chrome Safe Storage(会弹授权框)"""
    r = subprocess.run(["security", "find-generic-password", "-w", "-s", "Chrome Safe Storage"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"Keychain 授权失败或被拒: {r.stderr[:100]}")
    return r.stdout.strip().encode()


def decrypt_v10(encrypted, key):
    """Chrome macOS cookie: v10 = AES-CBC(PBKDF2(key), iv=' '*16), 去前 3 字节版本头"""
    from Crypto.Cipher import AES  # pycryptodome
    from hashlib import pbkdf2_hmac
    if encrypted[:3] not in (b"v10", b"v11"):
        return encrypted.decode("utf-8", "ignore")  # 未加密
    k = pbkdf2_hmac("sha1", key, b"saltysalt", 1003, dklen=16)
    iv = b" " * 16
    dec = AES.new(k, AES.MODE_CBC, iv).decrypt(encrypted[3:])
    # 去 PKCS7 padding
    return dec[:-dec[-1]].decode("utf-8", "ignore")


def main():
    out_json = sys.argv[1] if len(sys.argv) > 1 else "/tmp/merchant_cookies.json"
    key = get_key()
    # Chrome 运行中会锁 Cookies 库 → 拷贝副本读
    tmp = tempfile.mktemp(suffix=".db")
    shutil.copy(SRC, tmp)
    conn = sqlite3.connect(tmp)
    rows = conn.execute(
        "SELECT host_key, name, encrypted_value, path, is_secure, is_httponly, expires_utc "
        "FROM cookies WHERE " + " OR ".join([f"host_key LIKE '%{d}'" for d in DOMAINS])
    ).fetchall()
    conn.close()
    os.remove(tmp)
    cookies = []
    fails = 0
    for host, name, enc, path, secure, httponly, exp in rows:
        try:
            value = decrypt_v10(enc, key)
        except Exception:
            fails += 1
            continue
        if not value:
            fails += 1
            continue
        cookies.append({"domain": host, "name": name, "value": value, "path": path or "/",
                        "secure": bool(secure), "httpOnly": bool(httponly)})
    import json
    with open(out_json, "w") as f:
        json.dump(cookies, f, ensure_ascii=False)
    print(f"提取 {len(cookies)} 条 (失败 {fails}) → {out_json}")
    domains_seen = sorted({c['domain'] for c in cookies})
    print("域:", domains_seen)
    if len(cookies) < 5:
        print("⚠️ cookie 过少, 检查日常 Chrome 是否登录了商家后台")


if __name__ == "__main__":
    main()
