# -*- coding: utf-8 -*-
"""京东到家报表映射: 门店报表(29列) → 模板 HI~HK 京东外卖数据区。

口径来源: 脑图 运营报表.pdf(京东区) + 2026-09-20 真实下载报表实测
(门店_20260913_20260919_ningjizzl99_*.xlsx, sheet「数据」, 155 行)。

模板京东区(脑图): HI 营业额 / HJ 营业收入 / HK 订单量。
- HI 营业额   ← 营业额
- HJ 营业收入 ← 收入
- HK 订单量   ← 有效订单
率类/时长类列(履约准时率等)模板未要求, 不映射。
"""
JD_MAPPING = [
    ("HI", "营业额", "京东营业额"),
    ("HJ", "收入", "京东营业收入"),
    ("HK", "有效订单", "京东订单量"),
]

# 日期列 / 文件名模式(下载列表文件名: 门店_<起8>_<止8>_<账号>_<时间>.xlsx)
DATE_COL = "日期"
FILE_PREFIX = "门店_"


def num(v):
    """京东报表数值: 字符串带 % 的率不转(模板未用), 千分位去逗号"""
    if v in (None, ""):
        return None
    try:
        s = str(v).replace(",", "")
        if s.endswith("%"):
            return None
        return float(s)
    except (TypeError, ValueError):
        return None


def build_write_set(row, weather=(None, None)):
    """京东报表行 → write_set(模板列→值)"""
    vals = {}
    for col, field, _name in JD_MAPPING:
        vals[col] = num((row or {}).get(field))
    if weather and weather[0] is not None:
        vals["C"] = weather[0]
    if weather and len(weather) > 1 and weather[1] is not None:
        vals["D"] = weather[1]
    return vals
