# -*- coding: utf-8 -*-
"""美团管家 → 模板 HO~HZ 映射(脑图口径: 经营分析→营业概况)。

数据源: 营业概览页 DOM 直读(主路径) 或「导出」的 xlsx(备用)。
「营业」sheet / 页面明细区的订单分类构成:
    订单分类 | 营业收入 | 营业额 | 优惠金额 | 优惠占比 | 订单量
    店内销售 / 自提销售 / 美团外卖 / 淘宝闪购 / 自营外卖 / 京东秒送(未开通渠道无该行)

模板列(实测 r5):
    HO 营业额-店内销售    ← 店内销售.营业额
    HP 营业收入-店内销售   ← 店内销售.营业收入
    HQ 订单量-店内销售    ← 店内销售.订单量
    HR 营业额-自提销售    ← 自提销售.营业额
    HS 营业收入-自提销售   ← 自提销售.营业收入
    HT 订单量-自提销售    ← 自提销售.订单量
    HX 自营外卖营业额     ← 自营外卖.营业额
    HY 自营外卖收入       ← 自营外卖.营业收入
    HZ 自营外卖单量       ← 自营外卖.订单量
    HU/HV/HW 抖音外卖三项  ← 无渠道数据(抖音走第三方, 管家不汇总) → 留空
脑图另标的 HK(订单量) 出自「财务数据→账务操作统计」, 与模板美团外卖区冲突复用 → 暂不映射。
"""
GJ_MAPPING = [
    ("HO", "店内销售", "营业额"),
    ("HP", "店内销售", "营业收入"),
    ("HQ", "店内销售", "订单量"),
    ("HR", "自提销售", "营业额"),
    ("HS", "自提销售", "营业收入"),
    ("HT", "自提销售", "订单量"),
    ("HX", "自营外卖", "营业额"),
    ("HY", "自营外卖", "营业收入"),
    ("HZ", "自营外卖", "订单量"),
]

import re as _re  # noqa: E402

# 最近一次 DOM 直读的解析取证(诊断包原样带走: 解析类失败不必再靠截图猜)
LAST_PROBE = {}


def num(v):
    if v in (None, ""):
        return None
    try:
        s = str(v).replace(",", "")
        if s.endswith("%"):
            return None
        return float(s)
    except (TypeError, ValueError):
        return None


# ---------- DOM 直读(主路径): 营业概览页文本 → 结构化(免导出, ~8s/店) ----------
# ⚠️ 渠道名单是**会变的**: 2026-09-30 平台新增「自营外卖」, 旧白名单没有它 →
#    渠道合计比总量少 34/2074.81 = 1.64% → 交叉校验误判"页面没刷完" → 整店数据被弃用。
#    所以解析不假设白名单: 凡「名称: 数值」对都收; 白名单内的写模板, 白名单外的作为
#    **未识别渠道**参与校验(能精确解释差额就放行并告警), 而不是静默丢掉整店数据。
CHANNELS = ("店内销售", "自提销售", "美团外卖", "淘宝闪购", "自营外卖", "京东秒送")
CHANNEL_SET = set(CHANNELS)

_METRIC_LABELS = ["营业收入(元)", "营业额(元)", "优惠金额(元)", "优惠占比", "订单量(单)"]
# 页面上的「名称: 数值」对(渠道明细就是这种形态, 如 美团外卖:1,038.12)
_PAIR_RE = _re.compile(r"([\u4e00-\u9fa5A-Za-z]{2,8})[:：]\s*([\d,]+\.?\d*)")
_BARE_NUM_RE = _re.compile(r"[\d,]+\.?\d*")
# 与渠道无关的「名称: 数值」对(页面上其他 key: value), 不能当渠道
_NOISE_NAMES = {"商户号", "门店编号", "营业日期", "查询时间范围", "品牌商", "电话", "税率",
                "营业时长", "门店"}

TOL = 0.01          # 1%: 容忍页面四舍五入/千分位误差
_SEG_MAX = 600      # 单指标块文本上限(防越界吞到别的区块)


def _occurrences(txt, label):
    """label 的每一处出现 → 片段 [start, end): 到下一个指标标签或 +_SEG_MAX 为止。

    同一指标在页面上会**出现多次**(顶部卡片一处、明细构成区一处), 旧代码只取第一处,
    页面顺序一变就解析错块 → 必须逐处尝试、按自洽性挑选。
    """
    out = []
    i = txt.find(label)
    while i >= 0:
        start = i + len(label)
        end = start + _SEG_MAX
        for lb in _METRIC_LABELS:
            j = txt.find(lb, start)
            if j >= 0:
                end = min(end, j)
        if end > start:
            out.append((start, end))
        i = txt.find(label, i + 1)
    return out


def _parse_segment(seg):
    """片段 → (总量, 已知渠道, 未识别渠道)。

    总量 = 第一个**不属于**「名称: 数值」对的数字(渠道明细全是带名称的对)。
    """
    pairs = [(m.group(1), num(m.group(2)), m.start(), m.end()) for m in _PAIR_RE.finditer(seg)]
    masked = list(seg)
    for _n, _v, s, e in pairs:
        for k in range(s, e):
            masked[k] = " "
    m = _BARE_NUM_RE.search("".join(masked))
    total = num(m.group(0)) if m else None
    known, unknown = {}, {}
    for name, v, _s, _e in pairs:
        if v is None:
            continue
        if name in CHANNEL_SET:
            known[name] = v
        elif name not in _NOISE_NAMES:
            unknown[name] = v
    return total, known, unknown


def _reconcile(total, known, unknown):
    """渠道合计 vs 总量 → (True/False/None, 偏差率)。

    None  = 无法判定(总量缺失或一条渠道都没有) → 不校验、不误报。
    True  = 对得上 —— 含"差额被未识别渠道精确解释"的情况(渠道名变了但数据是好的)。
    False = 真对不上(页面没刷完/结构变了) → 调用方弃用该店数据以免写错。
    """
    if total in (None, 0) or not known:
        return None, None
    known_sum = sum(known.values())
    off = abs(known_sum - total) / total
    if off <= TOL:
        return True, off
    rest = total - known_sum
    unk_sum = sum(unknown.values())
    if unknown and unk_sum and abs(unk_sum - rest) / total <= TOL:
        return True, off          # 差额 = 未识别渠道之和 → 数据可信, 只是有渠道没映射
    return False, off


def _pick_candidate(txt, label):
    """同一指标的多个候选片段 → 取最可信的一个。

    优先级: 有渠道且自洽 > 有渠道且差额可解释 > 偏差最小 > 首个候选。
    返回 (total, known, unknown, ok, off, 候选数)。
    """
    cands = []
    for s, e in _occurrences(txt, label):
        total, known, unknown = _parse_segment(txt[s:e])
        ok, off = _reconcile(total, known, unknown)
        cands.append({"total": total, "known": known, "unknown": unknown, "ok": ok, "off": off})
    if not cands:
        return None, {}, {}, None, None, 0
    with_ch = [c for c in cands if c["known"] or c["unknown"]]
    ok_ch = [c for c in with_ch if c["ok"] is True]
    if ok_ch:
        pick = ok_ch[0]
    elif with_ch:
        pick = sorted(with_ch, key=lambda c: (c["off"] if c["off"] is not None else 9))[0]
    else:
        pick = cands[0]
    return (pick["total"], pick["known"], pick["unknown"], pick["ok"], pick["off"], len(cands))


def scrape_overview_text(txt):
    """营业概览页 innerText → {"店内销售": {...}, ...}, 附交叉校验(渠道合计≈总量)。
    与 parse_biz_xlsx 输出同构, orchestrator 无感切换。"""
    keys = (("营业收入(元)", "营业收入"), ("营业额(元)", "营业额"), ("订单量(单)", "订单量"))
    totals, channels, unknown_all, bad, probe_c = {}, {}, {}, [], {}
    for label, key in keys:
        total, known, unknown, ok, off, n = _pick_candidate(txt, label)
        totals[key] = total
        channels[key] = known
        if unknown:
            unknown_all.update(unknown)
        if ok is False:
            bad.append(key)
        probe_c[key] = {"total": total, "known": known, "unknown": unknown,
                        "consistent": ok, "offset": round(off, 4) if off is not None else None,
                        "candidates": n}

    out = {}
    for cat in CHANNELS:
        d = {}
        for label, key in keys:
            if cat in channels[key]:
                d[key] = channels[key][cat]
        if d:
            out[cat] = d

    # 「无数据」≠「结构变了」: 门店当天没营业/未开业/数据未同步时, 平台**照样渲染出**
    # 营业收入(元)/营业额(元)/订单量(单) 这些标签, 只是数值全 0、各构成块显示「暂无数据」,
    # 渠道明细(店内销售:1,520.33 这种)一行都没有 → 渠道正则命中 0 条。
    # 判据必须要求**标签存在**: 标签都找不到 = 页面结构真变了(那是另一回事, 不能掩盖),
    # 只有"标签在 + 数值全 0"才是"该店当天没有营业数据"(2026-09-28 真机 23 店中 6 家)。
    labels_found = sum(1 for lb, _k in keys if lb in txt)
    zero_data = ((not out) and labels_found >= 2
                 and all(v in (None, 0) for v in (totals["营业收入"], totals["营业额"],
                                                  totals["订单量"])))
    if zero_data:
        # 2026-10-01 用户口径: 「能拿到数据、但数值是 0」= **真实数据**(当天确实 0 单/闭店),
        # 要**照写进模板**, 不能整店跳过 —— 跳过会让模板缺这家店, 用户还得自己猜是没数据还是没抓到。
        # 做法: 与正常返回**同构**——按 GJ_MAPPING 涉及的类别/字段全填 0(页面本来就没渲染渠道明细),
        # 下游 build_write_set 就会把这些 0 写进模板。
        # 注意区分: 标签都渲染不出来(结构真变了)时 zero_data=False, 仍走上方的报错分支, 不会写 0 冒充。
        for _col, cat, field in GJ_MAPPING:
            out.setdefault(cat, {}).setdefault(field, 0)
    meta = {"totals": totals, "crosscheck_failed": bad, "zero_data": zero_data,
            "unknown_channels": unknown_all,
            "no_data_hint": ("暂无数据" in txt) if zero_data else False,
            "probe": {"metrics": probe_c, "channels": list(CHANNELS)}}
    LAST_PROBE.clear()
    LAST_PROBE.update(meta)
    return out, meta


def parse_biz_xlsx(path):
    """营业概览导出文件 → {"店内销售": {...}, "自提销售": {...}, "meta": {...}}
    解析「营业」sheet 的 订单分类构成 段(表头行: 订单分类|营业收入|营业额|优惠金额|优惠占比|订单量)。"""
    import openpyxl
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb["营业"] if "营业" in wb.sheetnames else wb.active
    rows = list(ws.iter_rows(values_only=True))

    out = {"meta": {}}
    # 1) 顶部指标区(营业收入/营业额/订单量 总值, 备用)
    # 2) 订单分类构成表
    i = 0
    hdr_idx = None
    while i < len(rows):
        r = rows[i]
        cells = [str(c).strip() if c is not None else "" for c in r]
        if cells[:1] == ["订单分类"] and "营业额" in cells:
            hdr_idx = i
            break
        i += 1
    if hdr_idx is None:
        raise ValueError("营业概览文件中未找到「订单分类构成」表")
    hdr = [str(c).strip() if c is not None else "" for c in rows[hdr_idx]]
    _STOP = ("优惠构成", "营业收入构成", "销售品项构成", "部门营业排行", "餐段分布",
             "订单来源分布", "综合统计")
    for r in rows[hdr_idx + 1:]:
        cat = str(r[0]).strip() if r[0] is not None else ""
        if not cat:
            continue
        if cat not in CHANNEL_SET:
            # 未知渠道行: 只要这一行还有数据就照收(渠道名变了不该丢整店数据) → 由
            # scrape/校验层决定是否告警; 命中段落结束标记才停。
            if cat in _STOP:
                break
            continue
        d = {}
        for j, name in enumerate(hdr):
            if not name or j == 0:
                continue
            d[name] = r[j] if j < len(r) else None
        out[cat] = d
    return out


def build_write_set(parsed, weather=(None, None)):
    """parse_biz_xlsx / scrape_overview_text 结果 → write_set(模板列→值)"""
    vals = {}
    for col, cat, field in GJ_MAPPING:
        v = (parsed.get(cat) or {}).get(field)
        vals[col] = num(v)
    if weather and weather[0] is not None:
        vals["C"] = weather[0]
    if weather and len(weather) > 1 and weather[1] is not None:
        vals["D"] = weather[1]
    return vals
