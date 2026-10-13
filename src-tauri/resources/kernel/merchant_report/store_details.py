# -*- coding: utf-8 -*-
"""《门店明细.xlsx》读取器 — 平台分组表头 → 编号/名称 → 模板 Sheet 名 映射。

用户定义格式(2026-10-09 实表):
  · 首个(或名为 data 的)工作表; 第 1 行 = 平台分组名(淘宝闪购/美团/美团管家/…, 可含注释文字;
    京东组允许留空 —— 用户注明"无法用编号导航+当前数据不需要单个"); 第 2 行 = 组内列头
    「门店名称」「门店唯一编号」; 另有「运营报表模板Sheet名」列(表头在 1 或 2 行, 值在数据行)。
  · 数据行从第 3 行起。
产出: {platform: {"by_id": {编号: sheet}, "by_name": {规范化名: sheet},
                  "by_name_id": {规范化名: 编号}}}
  platform ∈ taobao / meituan / meituan_gj / jd —— 与 PLATFORM_COL_HINTS 同一套键。
  by_name_id 供「切店按编号搜索直选」快路径用(orchestrator._switch_id_map → 适配器)。
匹配纪律: 编号优先、名称兜底、对不上不猜(调用方留空+告警)。

另: `platforms_in(path)` 只回答"表里**有门店数据**的平台有哪些"(字典的键),
供壳/前端决定「一键跑所有平台」要跑哪几个(真实样例只有淘宝/美团/管家, 没有京东)。
"""
import re

from .sheet_match import _tidy

_GROUP_PLAT = (
    ("管家", "meituan_gj"),
    ("淘宝", "taobao"),
    ("闪购", "taobao"),
    ("京东", "jd"),
    ("美团", "meituan"),
)

# 前端「一键跑所有平台」的固定相对顺序 —— 必须与壳/Python 两侧一致(见 __main__ 与 App.tsx)。
PLATFORM_ORDER = ("taobao", "meituan", "meituan_gj", "jd")


def _platform_of(group):
    for kw, p in _GROUP_PLAT:
        if kw in group:
            return p
    return None


def load_store_details(path):
    """读门店明细表 → {platform: {"by_id": {...}, "by_name": {...}}}; 读不了 → {}。"""
    try:
        import openpyxl
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    except Exception:
        return {}
    try:
        sn = "data" if "data" in wb.sheetnames else wb.sheetnames[0]
        ws = wb[sn]
        rows = list(ws.iter_rows(min_row=1, max_row=min(ws.max_row, 500), values_only=True))
        if len(rows) < 2:
            return {}
        h1, h2 = rows[0], rows[1]
        ncol = max(len(h1), len(h2))
        get = lambda r, i: str(r[i]).strip() if i < len(r) and r[i] is not None else ""

        sheet_col = None
        for i in range(ncol):
            probe = get(h1, i) + get(h2, i)
            if "运营报表模板" in probe or ("Sheet" in probe and "名" in probe) or \
               re.search(r"模板.*[Ss]heet", probe):
                sheet_col = i
                break
        if sheet_col is None:
            return {}

        # 「门店地址」列(需求 T01 天气逐店化): 表头含「地址」二字即认(h1 或 h2 任一命中)。
        # 老表没有这一列 → addr_col = None, 下面的 addr_* 一律为空, 调用方回退旧行为(整表共用天气)。
        addr_col = None
        for i in range(ncol):
            if "地址" in get(h1, i) or "地址" in get(h2, i):
                addr_col = i
                break

        # 逐列: 组名(向前携带) + 列角色
        col_group, name_cols, id_cols = {}, [], []
        cur = ""
        for i in range(ncol):
            g = get(h1, i)
            if g and i != sheet_col:
                cur = g
            col_group[i] = cur
            role = get(h2, i)
            if "门店名称" in role:
                name_cols.append(i)
            elif "编号" in role or "ID" in role or "id" in role:
                id_cols.append(i)

        out = {}
        addr_by_sheet = {}
        for i in name_cols:
            p = _platform_of(col_group[i])
            if not p:
                continue
            d = out.setdefault(p, {"by_id": {}, "by_name": {}, "by_name_id": {}})
            id_col = next((j for j in id_cols if col_group.get(j) == col_group[i]), None)
            for r in rows[2:]:
                nm = get(r, i)
                sheet = get(r, sheet_col)
                if not nm or not sheet:
                    continue
                d["by_name"].setdefault(_tidy(nm), sheet)
                if addr_col is not None:
                    addr = get(r, addr_col)
                    # 读不到地址的行: 只跳过该行的**地址**(不猜), 名称/编号映射照旧
                    if addr:
                        d.setdefault("addr_by_name", {}).setdefault(_tidy(nm), addr)
                        addr_by_sheet.setdefault(sheet, addr)
                if id_col is not None:
                    vid = get(r, id_col).split(".")[0]   # 防 Excel 转 float 丢精度样式 "33333333.0"
                    if vid:
                        d["by_id"].setdefault(vid, sheet)
                        # 2026-10-13: 名称→编号(切店「搜索编号直选」快路径要用)。
                        # 与 by_name/ 同一条数据行, 只是多留一份**行内**关联 —— 不经 Sheet 中转,
                        # 避免"同 Sheet 多编号"时张冠李戴。
                        d["by_name_id"].setdefault(_tidy(nm), vid)
        # addr_by_sheet: 顶层汇总 + 各平台各留一份(与「运营报表模板Sheet名」同一数据行取地址)。
        # 顶层那份是 orchestrator 天气逐店取数的主入口(它按 Sheet 名查地址, 不关心平台分组);
        # 注意它**不是**平台键 —— platforms_in() 按 PLATFORM_ORDER 过滤, 不会被带进队列。
        for p in out:
            out[p]["addr_by_sheet"] = dict(addr_by_sheet)
        if out:
            out["addr_by_sheet"] = dict(addr_by_sheet)
        return out
    finally:
        try:
            wb.close()
        except Exception:
            pass


def platforms_in(path, order=PLATFORM_ORDER):
    """门店明细表里**有门店数据**的平台(按固定相对顺序 淘宝→美团→管家→京东 过滤)。

    为什么要单独这个函数: 前端「一键跑所有平台」需要先知道该跑哪几个平台 —— 真实样例表里
    只有 淘宝/美团/管家(没有京东), 但旧逻辑写死四个平台全要配模板才能开跑, 用户被迫为
    一个根本没数据的平台去配模板。探测必须由内核做(壳里不解析 xlsx), 故提供
    `python -m merchant_report.store_details --platforms <path>` 供壳调用。
    读不了/空表/没配置 → 返回 [] (调用方回退旧行为)。
    """
    try:
        keys = set((load_store_details(path) or {}).keys())
    except Exception:
        return []
    return [p for p in order if p in keys]


if __name__ == "__main__":
    # 壳调用口径: 输出**一行 JSON 数组**(stdout), 读失败 → [] + 退出码非零(壳据此回退旧行为)。
    import json
    import sys

    argv = sys.argv[1:]
    path = ""
    if "--platforms" in argv:
        i = argv.index("--platforms")
        path = argv[i + 1] if i + 1 < len(argv) else ""
    if not path:
        sys.stderr.write("用法: python -m merchant_report.store_details --platforms <门店明细.xlsx>\n")
        print("[]")
        sys.exit(2)
    plats = platforms_in(path)
    print(json.dumps(plats, ensure_ascii=False))
    # 只认"表里真有平台分组且读得出映射"为成功; 读失败/空表 → 非零退出(壳回退旧行为)
    sys.exit(0 if plats else 1)
