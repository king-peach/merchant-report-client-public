# -*- coding: utf-8 -*-
"""选择器包加载器（改版对抗 L1）。

把易碎锚点(选择器/文案/列映射/文件名模式)从代码抽到 adapters/<platform>.json:
改版时改 JSON → 客户端下次任务前自动拉取最新包 → 无需发版。

- 包结构: {pack_version, min_kernel, url?, sha256?, selectors: {...}, mappings: {...}}
- 拉取: 任务前 GET pack.url(如有), ETag/版本比较, 失败静默用缓存 —— 网络问题永不阻塞任务
- 变体分支: selectors 里同键多值(list)时, 按页面指纹逐个尝试(应对灰度并存)
"""
import json
import os
import urllib.request

CACHE_DIR = os.path.join(os.path.expanduser("~"), ".hermes", "merchant-report-cache", "adapters")
LOCAL_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "adapters")


def _deep_merge(base, override):
    out = dict(base or {})
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_pack(platform, cache_dir=None):
    """读 adapters/<platform>.json; 有 url 则尝试在线更新(失败静默)。返回 dict。"""
    cache_dir = cache_dir or CACHE_DIR
    pack = None
    local_p = os.path.join(LOCAL_DIR, f"{platform}.json")
    cache_p = os.path.join(cache_dir, f"{platform}.json")
    for p in (cache_p, local_p):
        if pack is None and os.path.exists(p):
            try:
                pack = json.load(open(p, encoding="utf-8"))
            except Exception:
                pass

    if pack and pack.get("url"):
        try:
            req = urllib.request.Request(pack["url"], headers={"User-Agent": "MerchantReportClient/1.0"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                remote = json.load(resp)
            if isinstance(remote, dict) and remote.get("pack_version", 0) > pack.get("pack_version", 0):
                os.makedirs(cache_dir, exist_ok=True)
                json.dump(remote, open(cache_p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
                pack = remote
        except Exception:
            pass  # 网络失败静默, 用现有包

    if pack is None:
        pack = {"pack_version": 0, "selectors": {}, "mappings": {}}
    return pack


def get(pack, *keys, default=None):
    """按点路径取包值: get(pack, 'meituan', 'report', 'date_input')"""
    cur = pack
    for k in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k)
    return default if cur is None else cur


def variants(pack, *keys):
    """取变体列表: 标量 → [标量]; list → list。供调用方逐个尝试(灰度并存)。"""
    v = get(pack, *keys, default=None)
    if v is None:
        return []
    return v if isinstance(v, list) else [v]
