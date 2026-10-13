# -*- coding: utf-8 -*-
"""京东到家报表映射: 门店报表(29列) → 模板 HH~HJ 京东外卖数据区。

口径来源: 脑图 运营报表.pdf(京东区) + 2026-09-20 真实下载报表实测
(门店_20260913_20260919_demo_account_*.xlsx, sheet「数据」, 155 行)。

⚠️ 2026-10-13 用户换成「修复后模板」: 旧模板的「平台活动补贴」一列被删 → 从旧 DJ 往右全部左移 1 列,
   京东区旧 HI~HK → **新 HH~HJ**(2026-10-14 用旧版/10.13 两版模板逐列对照核实)。
   源字段不变: 营业额 / 收入 / 有效订单(下载文件里**没有**「营业收入」「订单量」这两个列名,
   所以必须硬编码映射, 禁止按报表列名模糊匹配 —— 规格书 §4.8)。

模板京东区(脑图): HH 营业额 / HI 营业收入 / HJ 订单量。
- HH 营业额   ← 营业额
- HI 营业收入 ← 收入
- HJ 订单量   ← 有效订单
率类/时长类列(履约准时率等)模板未要求, 不映射。
"""
JD_MAPPING = [
    ("HH", "营业额", "京东营业额"),
    ("HI", "收入", "京东营业收入"),
    ("HJ", "有效订单", "京东订单量"),
]

# 日期列 / 文件名模式(下载列表文件名: 门店_<起8>_<止8>_<账号>_<时间>.xlsx)
DATE_COL = "日期"
FILE_PREFIX = "门店_"

# 行内自校验(规格书 §4.8 / 铁律 7): 收入 = 营业额 − 支出(同一行, ±0.01 舍入)。
# 不成立 = 极可能取错列(如把"营业额"当"收入") → 该行不写 + 打印原始值(取错列的探针)。
IDENTITY_FIELDS = ("收入", "营业额", "支出")
IDENTITY_TOL = 0.01
# 本次运行里"行内自校验不成立"的记录(固定文案, 供编排层打印 + 复验 grep)。
IDENTITY_WARNINGS = []


def check_identity(row, tag=""):
    """校验 收入 ≈ 营业额 − 支出(±0.01)。返回 True=通过 / False=不成立 / None=字段不全无法判定。"""
    row = row or {}
    v = {f: num(row.get(f)) for f in IDENTITY_FIELDS}
    if any(v[f] is None for f in IDENTITY_FIELDS):
        return None
    if abs(v["收入"] - (v["营业额"] - v["支出"])) <= IDENTITY_TOL:
        return True
    raw = " | ".join(f"{k}={row.get(k)!r}" for k in
                     ("门店名称", "门店id", "日期", "收入", "营业额", "支出", "有效订单"))
    IDENTITY_WARNINGS.append(
        f"[京东行内校验] {tag}收入{v['收入']} ≠ 营业额{v['营业额']} − 支出{v['支出']}"
        f"(很可能取错列) → 该行不写入 ｜ 原始值: {raw}")
    return False


def identity_report():
    """行内自校验告警 → 去重后的固定文案列表(编排层打印 + 复验 grep 断言用)。"""
    seen, out = set(), []
    for w in IDENTITY_WARNINGS:
        if w not in seen:
            seen.add(w)
            out.append(w)
    return out


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
    """京东报表行 → write_set(模板列→值)

    先做行内自校验 `收入 = 营业额 − 支出`(规格书 §4.8): 不成立 → **整行不写** + 记固定告警
    (防止把"营业额当收入"这类错列写进模板)。无法判定(字段不全)时照旧写入, 不误伤。
    """
    if check_identity(row) is False:
        vals = {}                       # 三个京东列一律不写(防错列进表)
    else:
        vals = {}
        for col, field, _name in JD_MAPPING:
            vals[col] = num((row or {}).get(field))
    # 天气/温度与京东三列无关(城市级), 行内校验失败也照写, 不牵连 T01 的成果
    if weather and weather[0] is not None:
        vals["C"] = weather[0]
    if weather and len(weather) > 1 and weather[1] is not None:
        vals["D"] = weather[1]
    return vals
