# -*- coding: utf-8 -*-
"""淘宝闪购 → 模板 DG~HG 映射表(2026-10-13 修复后模板列位版)。
口径铁律:
  ① 主站本店流量 = 「全部报表 − 爆品团报表」逐店算(真机铁证: 下单 892−137=755 = 网页账号级值),
     网页「外卖」口径有值时覆盖整体三列(含跨业务去重, 略高 3~5%)。
  ② 新客/老客 = 报表逐店列(网页值仅在顾客维度自校验通过时覆盖)。
  ③ **商圈TOP(前10%均值)列**: 品牌版流量页没有商圈同行对比(2026-09-30 真机核实,
     漏斗第二列是「顾客维度=新客」, 不是基准) → 留空, 宁可不写也不写错。
  ④ 差异列 = 模板公式, 不写。

⚠️ 2026-10-13 用户换成「修复后模板」: 旧模板的「平台活动补贴」一列被删 → **从旧 DJ 往右全部左移 1 列**。
   本文件所有列字母 = 修复后模板(行 5 列头)口径; 2026-10-14 用旧版/10.13 两版模板逐列对照核实。
"""

TAOBAO_MAPPING = [
    # 淘闪总营收 ← 全部报表(新 DG/DH/DI 位置与旧版一致, 未位移)
    ("DG", "ALL:营业额", "总营业额"),
    ("DH", "ALL:收入", "总营业收入"),
    ("DI", "ALL:有效订单", "总订单量"),
    # 旧「DJ 活动总补贴」在新模板**不存在**: 新 DJ/DK/DL = 主站营业额/营业收入/订单量(=DG-ET 公式列, 禁写)
    # 爆品团营收 ← 爆品团报表(旧 EU..EY → 新 ET..EX)
    ("ET", "BOM:营业额", "爆品团营业额"),
    ("EU", "BOM:收入", "爆品团营业收入"),
    ("EV", "BOM:有效订单", "爆品团订单量"),
    ("EW", "BOM:单均实付", "爆品团单均价"),
    ("EX", "BOM:无效订单", "爆品团无效订单"),
    # 爆品团流量本店值 ← 爆品团报表(TOP列是模板公式差异列, 不写; 但EZ/FC/FI 这类对比列要写)
    ("EY", "BOM:曝光人数", "爆品团曝光"),
    ("FB", "BOM:进店人数", "爆品团进店"),
    ("FE", "BOM:进店转化率", "爆品团进店转化率"),
    ("FH", "BOM:下单人数", "爆品团下单"),
    ("FK", "BOM:下单转化率", "爆品团下单转化率"),
    # 爆品团流量 新客/老客 ← 爆品团报表(2026-09-28 补: 以前只写整体, 新客老客三列全空)
    # ⚠️ 修复后模板的新客/老客组 = [曝光人数, 进店人数, **下单转化率**](没有"进店转化率"列)
    #    → 原来取「新客/老客进店转化率」是**取错指标**(规格书 §4.7 点名: FT/FU/GC/GD 4 格)
    ("FN", "BOM:新客曝光人数", "爆品团新客曝光"),
    ("FQ", "BOM:新客进店人数", "爆品团新客进店"),
    ("FT", "BOM:新客下单转化率", "爆品团新客下单转化率"),
    ("FW", "BOM:老客曝光人数", "爆品团老客曝光"),
    ("FZ", "BOM:老客进店人数", "爆品团老客进店"),
    ("GC", "BOM:老客下单转化率", "爆品团老客下单转化率"),
    # 常规板块 ← 全部报表(旧 GM..GS → 新 GL..GR)
    ("GL", "ALL:无效订单", "无效订单"),
    ("GM", "ALL:退单费用", "退单费用"),
    ("GN", "ALL:商户原因无效订单数", "商户原因无效订单数"),
    ("GO", "ALL:商责退单率", "商责退单率"),
    ("GP", "ALL:拒单数", "拒单数"),
    ("GQ", "ALL:差评订单数", "差评订单数"),
    ("GR", "ALL:投诉订单数", "投诉订单数"),
    # 店铺评分/满意度/味道/包装 ← 淘宝报表第 99~102 列(2026-10-08 真机核实:
    #   '店铺评分'=4.5 / '满意度得分'=5.0 / '味道得分'=5.0 / '包装得分'=5.0 —— 数据本来就在报表里,
    #   只是以前没映射 → 模板一直空。旧 GT~GW → 新 GS~GV)
    ("GS", "ALL:店铺评分", "店铺评分"),
    ("GT", "ALL:满意度得分", "满意度得分"),
    ("GU", "ALL:味道得分", "味道得分"),
    ("GV", "ALL:包装得分", "包装得分"),
    ("GW", "ALL:差评订单数", "差评订单数(第二处,同源)"),
    ("GX", "ALL:近7日复购率", "近7日复购率"),
    ("GY", "ALL:近30日复购率", "近30日复购率"),
    ("GZ", "ALL:设置营业时间段", "设置营业时间段(文本)"),
    ("HA", "ALL:高峰期营业时长", "高峰期营业时长"),
    ("HB", "ALL:营业时长", "营业时长"),
    ("HD", "ALL:异常关店时长", "异常关店时长"),
    ("HE", "ALL:出餐超时订单数", "出餐超时订单数"),
    ("HF", "ALL:单均出餐时长", "单均出餐时长"),
    ("HG", "ALL:单均取餐时长", "单均取餐时长"),
]
# 不写(模板公式列, 修复后模板口径):
#   主站营收/订单 DJ/DK/DL(=DG-ET 等), 差异列 DN? 否 —— 差异列是
#   DO/DR/DU/DX/EA、ED/EG/EJ、EM/EP/ES、FA/FD/FG/FJ/FM、FP/FS/FV、FY/GB/GE,
#   推广进店率 GI(=GH/GG), 推广ROI GJ
# 网页来源(由 capture_stats 产出): DM/DP/DS/DV/DY(外卖漏斗, 账号级, 覆盖报表减法),
# EB/EE/EH(新客)、EK/EN/EQ(老客) —— 仅「顾客维度自校验通过」时覆盖。
# 商圈TOP静态列(DN/DQ/DT/DW/DZ、EC/EF/EI/EL/EO/ER、EZ/FC/FF/FI/FL、FO/FR/FU、FX/GA/GD):
#   品牌版流量页无商圈同行对比时不写(见上方口径铁律③); capture 逐店抓到前10%基准时写。
#   生产模板里若有历史错值, 见 tools/clean_taobao_top_columns.py 清理(它会先备份)。

# ---------- 数值合理性校验(规格书 铁律 8 / T11, 淘宝侧; 与美团 meituan_mapping 同风格) ----------
# 率值列 → 必须 ∈ [0, 100](规格书: 转化率 ∈ [0,100]%; 淘宝报表的率值既可能是小数 0.2115,
#   也可能是百分数 21.15 —— 真机两种都见过, 故上界按规格书取 100, 不按 1 收窄);
# 其余数值列(人数/订单/金额/时长/得分) → 必须 ≥ 0。超界**不写该值** + 固定文案告警(可 grep)。
# 三态校验: 全部 ≈ 新客 + 老客(人数类, ±1 容差) → 只提示, 不拦写入。
RATE_COLS = frozenset({"DS", "DY", "EH", "EQ",          # 主站 进店/下单转化率 + 新客/老客下单转化率
                       "FE", "FK", "FT", "GC",          # 爆品团 进店/下单转化率 + 新客/老客下单转化率
                       "GO",                            # 商责退单率
                       "GX", "GY"})                     # 近7日/近30日复购率
THREE_STATE = (("曝光人数", "新客曝光人数", "老客曝光人数"),
               ("进店人数", "新客进店人数", "老客进店人数"),
               ("下单人数", "新客下单人数", "老客下单人数"))
VALIDATE_WARNINGS = []
VALIDATE_NOTES = []          # 通过的三态校验(debug 级取证)


def _num(v):
    if v in (None, ""):
        return None
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def validate_value(col, v):
    """单值校验 → 通过原样返回; 超界返回 None(**不写该值**)并记固定文案。"""
    if v is None or isinstance(v, str):
        return v
    if not isinstance(v, (int, float)):
        return v
    if col in RATE_COLS:
        if not (0.0 <= float(v) <= 100.0):
            VALIDATE_WARNINGS.append(f"[数值校验] {col}={v} 超出率值范围[0,100%] → 不写入")
            return None
    elif float(v) < 0:
        VALIDATE_WARNINGS.append(f"[数值校验] {col}={v} 为负值(人数/订单/金额须≥0) → 不写入")
        return None
    return v


def validate_write_set(vals):
    """对 write_set 逐列校验(超界值置空); 返回新 dict(不改入参)。"""
    return {k: validate_value(k, v) for k, v in (vals or {}).items()}


def three_state_check(row, tag=""):
    """三态校验: 全部 ≈ 新客 + 老客(人数类, ±1 容差)。通过记 debug, 不通过记 warn(不拦写入)。"""
    row = row or {}
    for tot, a, b in THREE_STATE:
        T, A, B = _num(row.get(tot)), _num(row.get(a)), _num(row.get(b))
        if not all(isinstance(x, (int, float)) for x in (T, A, B)):
            continue
        if abs(T - (A + B)) > 1:
            VALIDATE_WARNINGS.append(f"[三态校验] {tag}{tot}={T} ≠ 新客{A}+老客{B}(容差±1) → 提示(不拦写入)")
        else:
            VALIDATE_NOTES.append(f"[三态校验] {tag}{tot}={T} ≈ 新客{A}+老客{B} ✓")


def validate_report():
    """数值校验告警 → 去重后的固定文案列表(编排层打印 + 复验 grep 断言用)。"""
    seen, out = set(), []
    for w in VALIDATE_WARNINGS:
        if w not in seen:
            seen.add(w)
            out.append(w)
    return out


def validate_notes():
    """三态校验通过项 → 去重后的 debug 级取证文案。"""
    seen, out = set(), []
    for w in VALIDATE_NOTES:
        if w not in seen:
            seen.add(w)
            out.append(w)
    return out


def build_write_set(all_row, bom_row, capture, weather=(None, None)):
    """报表行 + 流量抓取 + 天气 → write_set

    主站本店流量口径(2026-09-30 真机实测): **全部报表 = 主站 + 爆品团** ——
        下单人数 892(全部) − 137(爆品团) = 755 = 流量页「外卖」口径的账号级值, 完全吻合;
        曝光 74,383−26,302=48,081 vs 网页 50,660、进店 3,045−475=2,570 vs 2,654(差 3~5%,
        是平台跨业务去重所致)。
    所以主站那 11 列**先用报表减法逐店算**, 网页抓取(账号级)只在有值时覆盖整体三列 ——
    以前这些列只靠网页, 而网页是**连锁(账号累计)视图** → 门店 Sheet 全空(用户 09-30 报的问题)。
    """
    from ..fill import evaluate_mapping
    all_row = all_row or {}
    bom_row = bom_row or {}     # 有的店只出现在「全部」报表里(无爆品团业务) → 当成 0 处理, 别崩
    vals, _ = evaluate_mapping(TAOBAO_MAPPING, all_row, bom_row)

    def n(v):
        try:
            return None if v in (None, "") else float(str(v).replace(",", ""))
        except (TypeError, ValueError):
            return None

    def sub(fa, fb):
        """全部报表 − 爆品团报表。

        爆品团侧缺失 = 该店没有爆品团业务(如「兰亭湾畔店」只出现在全部报表里) → 按 0 算,
        不能因此整店不写(2026-09-30 真机: 17 店 vs 16 店, 兰亭缺爆品团行)。
        """
        a = n(all_row.get(fa))
        if a is None:
            return None                      # 全部报表里就没有这个字段 → 宁可不写
        b = n(bom_row.get(fb)) if bom_row else None
        return round(a - (0.0 if b is None else b), 4)

    def rate(a, b):
        """分子/分母; 分母为 0/缺失 → 不写(0/0 无意义), 分子为 0 但分母有 → 写 0。

        报表里 0 值字段常是空串(如兰亭当日下单=0), 空串被当成"没有"会漏掉一个真实的 0 转化率。"""
        if b in (None, 0):
            return None
        return round((a or 0.0) / b, 6)

    # 爆品团新客/老客「下单转化率」兜底: 报表缺该率值列(真机见过少一列的报表形态)时按
    # 下单人数/进店人数 自算; 两者都没有 → 留空(宁缺勿错, 不拿进店转化率顶替)。
    for _c, _o, _e in (("FT", "新客下单人数", "新客进店人数"),
                       ("GC", "老客下单人数", "老客进店人数")):
        if vals.get(_c) is None and bom_row:
            vals[_c] = rate(n(bom_row.get(_o)), n(bom_row.get(_e)))

    # ---------- 主站本店流量(逐店, 报表减法) ----------
    # 修复后模板: 曝光 DM / 进店 DP / 进店转化率 DS / 下单 DV / 下单转化率 DY
    #             新客 EB(曝光) EE(进店**人数**) EH(下单转化率)
    #             老客 EK(曝光) EN(进店**人数**) EQ(下单转化率)
    exp, ent, ord_ = sub("曝光人数", "曝光人数"), sub("进店人数", "进店人数"), sub("下单人数", "下单人数")
    ne, nb = sub("新客进店人数", "新客进店人数"), sub("新客下单人数", "新客下单人数")
    oe, ob = sub("老客进店人数", "老客进店人数"), sub("老客下单人数", "老客下单人数")
    ec, el = sub("新客曝光人数", "新客曝光人数"), sub("老客曝光人数", "老客曝光人数")
    vals.update({
        "DM": exp, "DP": ent, "DS": rate(ent, exp), "DV": ord_, "DY": rate(ord_, ent),
        "EB": ec, "EE": ne, "EH": rate(nb, ne),
        "EK": el, "EN": oe, "EQ": rate(ob, oe),
    })
    dims_ok = bool(capture.get("customer_dims_ok")) if isinstance(capture, dict) else False

    if capture:
        w = capture.get("外卖_全部顾客") or {}
        # 整体三列: 单店流量页的「本店」列(含跨业务去重)覆盖报表减法
        # 2026-10-09: 五里牌这类店 capture 可能半缺(抓取失败时 shop 置 {}) —— 之前直接
        # 下标 KeyError('exp') 会把整店整天的写入废掉(日志: "部分日期失败: [五里牌]: 'exp'")
        # → 一律安全取值; 缺失即回落报表减法值(不编造、不影响其它列)
        _ws = w.get("shop") or {}
        vals.update({
            "DM": _ws.get("exp") or vals.get("DM"), "DP": _ws.get("ent") or vals.get("DP"),
            "DS": _ws.get("ent_rate") or vals.get("DS"),
            "DV": _ws.get("ord") or vals.get("DV"), "DY": _ws.get("ord_rate") or vals.get("DY"),
        })
        # 商圈TOP(前10%均值) ← 单店流量页把基准切到「商圈同行前10%均值」后的**基准列**。
        # 2026-10-01 真机核实: 品牌版 chain 视图没有商圈对比(旧代码把「顾客=新客」那列当TOP写=错);
        # 单店视图(single/business-analysis)的基准下拉里才有 商圈同行均值 / 商圈同行前10%均值。
        # bench 没切成前10%(或没抓到) → top10 为空 → 这些列留空(宁可不写, 也不写错)。
        top = w.get("top10") or {}
        vals.update({
            "DN": top.get("exp"), "DQ": top.get("ent"), "DT": top.get("ent_rate"),
            "DW": top.get("ord"), "DZ": top.get("ord_rate"),
        })
        zb = (capture.get("爆品团_全部顾客") or {}).get("top10") or {}
        vals.update({
            "EZ": zb.get("exp"), "FC": zb.get("ent"), "FF": zb.get("ent_rate"),
            "FI": zb.get("ord"), "FL": zb.get("ord_rate"),
        })
        # 新客/老客的商圈TOP ← 单店页切到「新客/老客」维度后、基准仍为前10%时读到的基准列
        seg = capture.get("segments") or {}
        _w = (seg.get("外卖") or {})
        _z = (seg.get("爆品团") or {})
        _nk = ((_w.get("新客") or {}).get("top10") or {})
        _lk = ((_w.get("老客") or {}).get("top10") or {})
        vals.update({
            "EC": _nk.get("exp"), "EF": _nk.get("ent"), "EI": _nk.get("ord_rate"),
            "EL": _lk.get("exp"), "EO": _lk.get("ent"), "ER": _lk.get("ord_rate"),
        })
        _znk = ((_z.get("新客") or {}).get("top10") or {})
        _zlk = ((_z.get("老客") or {}).get("top10") or {})
        vals.update({
            "FO": _znk.get("exp"), "FR": _znk.get("ent"), "FU": _znk.get("ord_rate"),
            "FX": _zlk.get("exp"), "GA": _zlk.get("ent"), "GD": _zlk.get("ord_rate"),
        })
        # 新客/老客: 修复后模板该组 = [曝光人数, 进店人数, 下单转化率];
        # 只有顾客维度自校验通过(dims_ok)才覆盖(否则网页值可能就是整体值)。
        if dims_ok:
            _nk_s = (capture.get("外卖_新客") or {}).get("shop") or {}
            _o_s = (capture.get("外卖_老客") or {}).get("shop") or {}
            vals.update({
                "EB": _nk_s.get("exp") or vals.get("EB"),
                "EE": _nk_s.get("ent") or vals.get("EE"),
                "EH": _nk_s.get("ord_rate") or vals.get("EH"),
                "EK": _o_s.get("exp") or vals.get("EK"),
                "EN": _o_s.get("ent") or vals.get("EN"),
                "EQ": _o_s.get("ord_rate") or vals.get("EQ"),
            })
        p = capture.get("promo") or {}
        # 推广 4 列(修复后模板): GF 消耗 / GG 曝光量 / GH 进店量 / GK 充值
        # (2026-10-08 诊断: 以前推广只在**品牌级**抓 → 逐店 vals 里没 promo → 这 4 列永远空;
        #  现在 capture_per_store 会读每店自己的推广页并放进 cap["promo"], 这里照收)
        for k in ("GF", "GG", "GH", "GK"):
            if p.get(k) is not None:
                vals[k] = p[k]
    # 天气/温度 (C/D)
    if weather[0] is not None:
        vals["C"] = weather[0]
    if weather[1] is not None:
        vals["D"] = weather[1]
    # 数值合理性校验(规格书 铁律 8 / T11, 与美团同风格): 率值 ∈ [0,100%]、人数/金额 ≥ 0 —— 超界值
    # 不写; 三态(全部≈新客+老客, ±1)通过记 debug、不通过记 warn(不拦)。结果在 VALIDATE_* 里,
    # 由编排层收尾统一打印(见 orchestrator 淘宝支路)。
    three_state_check(all_row, tag="主站/全部报表 ")
    if bom_row is not None:
        three_state_check(bom_row, tag="爆品团报表 ")
    return validate_write_set(vals)
