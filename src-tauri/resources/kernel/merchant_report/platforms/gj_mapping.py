# -*- coding: utf-8 -*-
"""美团管家 → 模板 HN~HY 映射(脑图口径: 经营分析→营业概况)。

数据源: 营业概览页 DOM 直读(主路径) 或「导出」的 xlsx(备用)。
「营业」sheet / 页面明细区的订单分类构成:
    订单分类 | 营业收入 | 营业额 | 优惠金额 | 优惠占比 | 订单量
    店内销售 / 自提销售 / 美团外卖 / 淘宝闪购 / 自营外卖 / 京东秒送(未开通渠道无该行)

模板列(**10.13 版实测**, 逐列对照见 refs 双模板表头):
    HN 营业额-店内销售    ← 店内销售.营业额
    HO 营业收入-店内销售   ← 店内销售.营业收入
    HP 订单量-店内销售    ← 店内销售.订单量
    HQ 营业额-自提销售    ← 自提销售.营业额
    HR 营业收入-自提销售   ← 自提销售.营业收入
    HS 订单量-自提销售    ← 自提销售.订单量
    HT/HU/HV 抖音外卖三项  ← 无渠道数据(抖音走第三方, 管家不汇总) → 见下「抖音外卖」说明
    HW 自营外卖营业额     ← 自营外卖.营业额
    HX 自营外卖收入       ← 自营外卖.营业收入
    HY 自营外卖单量       ← 自营外卖.订单量
    HZ 敏感操作次数 / IA 敏感操作金额 / IB 当日工时
    IC 线上单量 / ID 线下单量 / IE 单量合计
脑图另标的 HK(订单量) 出自「财务数据→账务操作统计」, 与模板美团外卖区冲突复用 → 暂不映射。

⚠️ 旧版模板的管家区整体比 10.13 **右移一格**(旧版从 HO 起, 10.13 从 HN 起)。
   本模块一律按 **10.13 列位**写死; 旧版模板由 fill 层(任务 I)的列位平移统一 +1 兜底
   → HN 落到 HO, 以此类推。**不要**在这里按模板版本分支, 更不要动 fill 层。

历史: 2026-10-15(任务 J)前本模块用的是**旧版**字母(HO 起) → 在 10.13 模板上整块右移一格,
    `HN`(店内营业额)永远空, 客户模板公式 `IH=E+DG+HH+HN` 因此少算。现整体 -1 已修正。
"""
GJ_MAPPING = [
    ("HN", "店内销售", "营业额"),
    ("HO", "店内销售", "营业收入"),
    ("HP", "店内销售", "订单量"),
    ("HQ", "自提销售", "营业额"),
    ("HR", "自提销售", "营业收入"),
    ("HS", "自提销售", "订单量"),
    # 抖音外卖: **渠道行是动态的**(未开通的店后台不渲染该行)。用户口径(2026-10-08):
    # "如果有抖音外卖数据就填入, 没有就直接填 0" —— 缺该渠道 = 该店抖音外卖确实为 0(真实值)。
    # (旧代码这三列压根没映射 → 一直空; 现在加进来, 缺渠道时由 build_write_set 写 0)
    ("HT", "抖音外卖", "营业额"),
    ("HU", "抖音外卖", "营业收入"),
    ("HV", "抖音外卖", "订单量"),
    ("HW", "自营外卖", "营业额"),
    ("HX", "自营外卖", "营业收入"),
    ("HY", "自营外卖", "订单量"),
]

import json as _json  # noqa: E402
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


# ---------- 接口直取(2026-10-11): business-summary-page 响应 → 结构化 ----------
# 真机取证(高桥店 10-10, 与 DOM 版逐项 15/15 一致, 见 tests/test_gj_api_parse.py):
#   渠道值 = data.BusinessOverviewPrintBusinessCompose.items[]
#            incomeAmt(分)/100 → 营业收入; businessAmt(分)/100 → 营业额; orderCnt → 订单量
#   总量   = compose.sum[0](营业额/订单量) + data.BusinessEfficiency.payAmt(营业收入)
#   敏感   = data.SensOperation.{dishDetail,orderDetail}[].sensitiveTimes / skuAmt|orderAmt
# 失败(结构不符/非业务响应) → (None, {"api_error":..}) → 调用方回退 DOM(宁可不写不写错)。


def _cy(v):
    """金额: 分 → 元(两位小数)。None 安全。"""
    n = num(v)
    return None if n is None else round(n / 100.0, 2)


def _loads(v):
    """JSON 双编码解包: 字符串 → dict; dict 原样; 其它 → None。"""
    if isinstance(v, dict):
        return v
    if isinstance(v, str):
        try:
            r = _json.loads(v)
            return r if isinstance(r, dict) else None
        except Exception:
            return None
    return None


def _api_block(data, name):
    """data.<name>.data 内层 JSON(结构: {code, msg, data:"<json>"}); 不合法 → None。"""
    v = data.get(name)
    if not isinstance(v, dict) or v.get("code") not in (0, "0"):
        return None
    return _loads(v.get("data"))


def _sens_from_summary(data):
    """SensOperation 块 → {"times":.., "amt":..} 或 None(块缺失/无值)。"""
    s = _api_block(data, "SensOperation")
    if not isinstance(s, dict):
        return None
    times = amt = 0.0
    hit = False
    for key in ("dishDetail", "orderDetail"):
        for e in (s.get(key) or []):
            if not isinstance(e, dict):
                continue
            t = e.get("sensitiveTimes")
            if t is not None:
                try:
                    times += float(t)
                    hit = True
                except (TypeError, ValueError):
                    pass
            a = e.get("skuAmt")
            if a is None:
                a = e.get("orderAmt")
            if a is not None:
                try:
                    amt += float(a)
                except (TypeError, ValueError):
                    pass
    if not hit:
        return None
    return {"times": times, "amt": round(amt / 100.0, 2)}


def parse_summary_json(obj):
    """营业概览接口(business-summary-page)响应 → (parsed, meta), 与 scrape_overview_text 同构。

    obj: dict 或原始 JSON 字符串。
    失败 → (None, {"api_error": 原因}), 调用方回退 DOM; 成功时 meta 额外含:
    source="api" 与 sensitive_from_api(或 None, 供直写 HZ/IA)。
    """
    if isinstance(obj, str):
        try:
            obj = _json.loads(obj)
        except Exception:
            return None, {"api_error": "响应不是 JSON"}
    if not isinstance(obj, dict):
        return None, {"api_error": "响应不是 JSON 对象"}
    if obj.get("code") not in (0, "0"):
        return None, {"api_error": f"接口 code={obj.get('code')}"}
    data = obj.get("data")
    if not isinstance(data, dict):
        return None, {"api_error": "响应缺少 data"}
    comp = _api_block(data, "BusinessOverviewPrintBusinessCompose")
    if not isinstance(comp, dict) or not isinstance(comp.get("items"), list):
        return None, {"api_error": "缺少渠道构成块(compose)"}
    eff = _api_block(data, "BusinessEfficiency") or {}
    sums = comp.get("sum") or []
    s0 = sums[0] if sums and isinstance(sums[0], dict) else {}

    metrics = (("营业收入", "incomeAmt"), ("营业额", "businessAmt"), ("订单量", "orderCnt"))
    channels = {k: {} for k, _f in metrics}
    unknowns = {k: {} for k, _f in metrics}
    unknown_all = {}
    for it in comp["items"]:
        if not isinstance(it, dict):
            continue
        name = str(it.get("orderTypeName") or "").strip()
        if not name:
            continue
        for k, f in metrics:
            v = num(it.get(f)) if f == "orderCnt" else _cy(it.get(f))
            if v is None:
                continue
            if name in CHANNEL_SET:
                channels[k][name] = v
            else:
                unknowns[k][name] = v
                unknown_all[name] = v

    totals = {"营业收入": _cy(eff.get("payAmt")),
              "营业额": _cy(s0.get("businessAmt")),
              "订单量": num(s0.get("orderCnt"))}
    if any(totals[k] is None for k in ("营业收入", "营业额", "订单量")):
        return None, {"api_error": "总量字段缺失(payAmt/sum)"}

    out = {}
    for cat in CHANNELS:
        d = {}
        for k, _f in metrics:
            if cat in channels[k]:
                d[k] = channels[k][cat]
        if d:
            out[cat] = d

    bad, probe_c = [], {}
    for k, _f in metrics:
        ok, off = _reconcile(totals[k], channels[k], unknowns[k])
        if ok is False:
            bad.append(k)
        probe_c[k] = {"total": totals[k], "known": channels[k], "unknown": unknowns[k],
                      "consistent": ok, "offset": round(off, 4) if off is not None else None,
                      "candidates": 1}

    zero_data = (not out) and all(totals[k] == 0 for k in ("营业收入", "营业额", "订单量"))
    if zero_data:
        for _col, cat, field in GJ_MAPPING:
            out.setdefault(cat, {}).setdefault(field, 0)

    meta = {"totals": totals, "crosscheck_failed": bad, "zero_data": zero_data,
            "unknown_channels": unknown_all, "no_data_hint": False,
            "probe": {"metrics": probe_c, "channels": list(CHANNELS)},
            "source": "api", "sensitive_from_api": _sens_from_summary(data)}
    return out, meta


def readings_equal(a, ma, b, mb):
    """双读对比: 两路读数(渠道逐项值 + 总量 + zero 标志)一致? (数值容差 0.5 分)。"""
    if set(a) != set(b):
        return False
    for cat in a:
        da, db = a.get(cat) or {}, b.get(cat) or {}
        if set(da) != set(db):
            return False
        for k, v in da.items():
            w = db[k]
            if v is None or w is None:
                if v != w:
                    return False
                continue
            try:
                if abs(float(v) - float(w)) > 0.005:
                    return False
            except (TypeError, ValueError):
                return False
    for k in ("营业收入", "营业额", "订单量"):
        v, w = (ma.get("totals") or {}).get(k), (mb.get("totals") or {}).get(k)
        if v is None or w is None:
            if v != w:
                return False
            continue
        try:
            if abs(float(v) - float(w)) > 0.005:
                return False
        except (TypeError, ValueError):
            return False
    return bool(ma.get("zero_data")) == bool(mb.get("zero_data"))


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


# ---------- 线上/线下单量 (ID/IE/IF) ----------
# 用户口径(2026-10-08, 选项 B): 自提在线点也算线上 → 线上 = 自提销售 + 全部外卖渠道;
#   线下 = 店内销售。
# 真机对齐依据(2026-10-06 长沙绿地之窗店): 管家渠道行 ↔「订单来源」详情页**逐项相等** ——
#   店内销售18 = 扫码点餐9+收银POS9; 自提销售15 = 美团自提在线点15; 美团外卖90/淘宝闪购76/
#   京东秒送10/自营外卖1 全部 1:1; 两边合计都 = 210。→ 无需进「订单来源」详情页(懒加载XHR),
#   直接由渠道读数求和(渠道数据本就已被交叉校验过, 稳)。
_GJ_OFFLINE_CAT = "店内销售"


def online_offline(parsed):
    """渠道读数 → (线上单量, 线下单量)。店内销售行缺失 → (None, None) 留空不写。"""
    if not isinstance(parsed, dict):
        return None, None
    offline = num((parsed.get(_GJ_OFFLINE_CAT) or {}).get("订单量"))
    if offline is None:
        return None, None
    online = 0.0
    for cat, d in parsed.items():
        if (cat == _GJ_OFFLINE_CAT or cat == "meta"
                or str(cat).startswith("_") or not isinstance(d, dict)):
            continue
        v = num(d.get("订单量"))
        if v is not None:
            online += v
    return online, offline


def build_write_set(parsed, weather=(None, None)):
    """parse_biz_xlsx / scrape_overview_text 结果 → write_set(模板列→值)

    ⚠️ 渠道行是**动态的**: 未开通某渠道时后台不渲染那一行。缺渠道 ≠ 没抓到数据 ——
    用户口径(2026-10-08): "如果有抖音外卖数据就填入, 没有就直接填 0" → 缺该渠道写 0(真实值)。
    但"该渠道存在、只是字段没解析出来" → 仍留空(那是解析问题, 不能写 0 掩盖)。
    """
    vals = {}
    _cats = set((parsed or {}).keys())
    # ⚠️ 闸门: 只有"确实解析出了渠道"时才缺啥补 0。整页一个渠道都没解析出来(页面没渲染/结构变了)
    # → 不补 0(否则会把"没抓到"写成"全 0", 这是最危险的错法) → 保持留空, 由调用方按失败处理。
    _zero_missing = bool(_cats)
    for col, cat, field in GJ_MAPPING:
        if _zero_missing and cat not in _cats:
            vals[col] = 0                    # 后台没有该渠道行 = 该渠道真实为 0
            continue
        v = (parsed.get(cat) or {}).get(field)
        vals[col] = num(v)
    # 用户口径(2026-10-08): "美团商家中抖音外卖和自营外卖的数据需要汇总到模板里的抖音外卖数据里"
    # → 模板「抖音外卖」(HT/HU/HV) = 后台[抖音外卖] + 后台[自营外卖] 之和
    #   (该账号后台没有"抖音外卖"行 —— 抖音外卖业务是由"自营外卖"渠道报出来的, 故必须相加)
    #   两个渠道都不存在 → 保持上面缺渠道规则的结果(0)。
    for _col, _fld in (("HT", "营业额"), ("HU", "营业收入"), ("HV", "订单量")):
        _sum, _hit = 0.0, False
        for _cat in ("抖音外卖", "自营外卖"):
            if _cat in _cats:
                _hit = True
                _sum += num((parsed.get(_cat) or {}).get(_fld)) or 0.0
        if _hit:
            vals[_col] = _sum
    # ── 敏感操作 (HZ/IA): 「综合统计→敏感操作」详情页懒加载 XHR 抓取 ──
    #    由 meituan_gj._capture_sensitive 写入 parsed["_sensitive"]={"times":..,"amt":..};
    #    抓不到 → 不写(留空) —— "宁可不写, 也不写错"。
    _sens = parsed.get("_sensitive") if isinstance(parsed, dict) else None
    if _zero_missing and isinstance(_sens, dict):
        _t, _a = num(_sens.get("times")), num(_sens.get("amt"))
        if _t is not None:
            vals["HZ"] = _t
        if _a is not None:
            vals["IA"] = _a
    # ── 当日工时 (IB): 用户口径(2026-10-08)「数据源未定, 先填 0」 ──
    if _zero_missing:
        vals["IB"] = 0
    # ── 线上/线下单量 (IC/ID/IE): 用户口径(2026-10-08 选项B) → 渠道读数直接求和(见 online_offline) ──
    if _zero_missing:
        _on, _off = online_offline(parsed)
        if _on is not None and _off is not None:
            vals["IC"] = _on
            vals["ID"] = _off
            vals["IE"] = _on + _off
    if weather and weather[0] is not None:
        vals["C"] = weather[0]
    if weather and len(weather) > 1 and weather[1] is not None:
        vals["D"] = weather[1]
    return vals
