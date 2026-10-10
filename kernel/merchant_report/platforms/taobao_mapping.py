# -*- coding: utf-8 -*-
"""淘宝闪购 → 模板 DG~HH 映射表(2026-09-15 e2e 验证版)。
口径铁律:
  ① 主站本店流量 = 「全部报表 − 爆品团报表」逐店算(真机铁证: 下单 892−137=755 = 网页账号级值),
     网页「外卖」口径有值时覆盖整体三列(含跨业务去重, 略高 3~5%)。
  ② 新客/老客 = 报表逐店列(网页值仅在顾客维度自校验通过时覆盖)。
  ③ **商圈TOP(前10%均值)列不写**: 品牌版流量页没有商圈同行对比(2026-09-30 真机核实,
     漏斗第二列是「顾客维度=新客」, 不是基准) → 留空, 宁可不写也不写错。
  ④ 差异列 = 模板公式, 不写。"""

TAOBAO_MAPPING = [
    # 淘闪总营收 ← 全部报表
    ("DG", "ALL:营业额", "总营业额"),
    ("DH", "ALL:收入", "总营业收入"),
    ("DI", "ALL:有效订单", "总订单量"),
    ("DJ", "ALL:活动总补贴", "平台活动补贴"),
    # 爆品团营收 ← 爆品团报表
    ("EU", "BOM:营业额", "爆品团营业额"),
    ("EV", "BOM:收入", "爆品团营业收入"),
    ("EW", "BOM:有效订单", "爆品团订单量"),
    ("EX", "BOM:单均实付", "爆品团单均价"),
    ("EY", "BOM:无效订单", "爆品团无效订单"),
    # 爆品团流量本店值 ← 爆品团报表(TOP列是模板公式差异列, 不写; 但EZ/FC/FI写)
    ("EZ", "BOM:曝光人数", "爆品团曝光"),
    ("FC", "BOM:进店人数", "爆品团进店"),
    ("FF", "BOM:进店转化率", "爆品团进店转化率"),
    ("FI", "BOM:下单人数", "爆品团下单"),
    ("FL", "BOM:下单转化率", "爆品团下单转化率"),
    # 爆品团流量 新客/老客 ← 爆品团报表(2026-09-28 补: 以前只写整体, 新客老客三列全空)
    ("FO", "BOM:新客曝光人数", "爆品团新客曝光"),
    ("FR", "BOM:新客进店人数", "爆品团新客进店"),
    ("FU", "BOM:新客进店转化率", "爆品团新客进店转化率"),
    ("FX", "BOM:老客曝光人数", "爆品团老客曝光"),
    ("GA", "BOM:老客进店人数", "爆品团老客进店"),
    ("GD", "BOM:老客进店转化率", "爆品团老客进店转化率"),
    # 常规板块 ← 全部报表
    ("GM", "ALL:无效订单", "无效订单"),
    ("GN", "ALL:退单费用", "退单费用"),
    ("GO", "ALL:商户原因无效订单数", "商户原因无效订单数"),
    ("GP", "ALL:商责退单率", "商责退单率"),
    ("GQ", "ALL:拒单数", "拒单数"),
    ("GR", "ALL:差评订单数", "差评订单数"),
    ("GS", "ALL:投诉订单数", "投诉订单数"),
    # 店铺评分/满意度/味道/包装 ← 淘宝报表第 98~101 列(2026-10-08 真机核实:
    #   '店铺评分'=4.5 / '满意度得分'=5.0 / '味道得分'=5.0 / '包装得分'=5.0 —— 数据本来就在报表里,
    #   只是以前没映射 → 模板 GT~GW 一直空。)
    ("GT", "ALL:店铺评分", "店铺评分"),
    ("GU", "ALL:满意度得分", "满意度得分"),
    ("GV", "ALL:味道得分", "味道得分"),
    ("GW", "ALL:包装得分", "包装得分"),
    ("GX", "ALL:差评订单数", "差评订单数(第二处,同源)"),
    ("GY", "ALL:近7日复购率", "近7日复购率"),
    ("GZ", "ALL:近30日复购率", "近30日复购率"),
    ("HA", "ALL:设置营业时间段", "设置营业时间段(文本)"),
    ("HB", "ALL:高峰期营业时长", "高峰期营业时长"),
    ("HC", "ALL:营业时长", "营业时长"),
    ("HE", "ALL:异常关店时长", "异常关店时长"),
    ("HF", "ALL:出餐超时订单数", "出餐超时订单数"),
    ("HG", "ALL:单均出餐时长", "单均出餐时长"),
    ("HH", "ALL:单均取餐时长", "单均取餐时长"),
]
# 不写(模板公式列): DK-DM(主站营收=全部-爆品团), DP/DS/DV/DY/EB/EE/EH/EK/EN/EQ/ET(差异),
# FB/FE/FH/FK/FN/FQ/FT/FW/FZ/GC/GF(TOP差), GJ(进店率IFERROR), GK(ROI)
# 网页来源(由 capture_stats 产出): DN/DQ/DT/DW/DZ(外卖漏斗, 账号级, 覆盖报表减法),
# EC/EF/EI(新客)、EL/EO/ER(老客) —— 仅「顾客维度自校验通过」时覆盖。
# 商圈TOP静态列(DO/DR/DU/DX/EA、ED/EG/EJ/EM/EP/ES、FA/FD/FG/FJ/FM/FP/FS/FV/FY/GB/GE):
#   **不写**(品牌版流量页无商圈同行对比, 见上方口径铁律③)。生产模板里若有历史错值, 见
#   tools/clean_taobao_top_columns.py 清理(它会先备份)。


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

    # ---------- 主站本店流量(逐店, 报表减法) ----------
    exp, ent, ord_ = sub("曝光人数", "曝光人数"), sub("进店人数", "进店人数"), sub("下单人数", "下单人数")
    ne, nb = sub("新客进店人数", "新客进店人数"), sub("新客下单人数", "新客下单人数")
    oe, ob = sub("老客进店人数", "老客进店人数"), sub("老客下单人数", "老客下单人数")
    ec, el = sub("新客曝光人数", "新客曝光人数"), sub("老客曝光人数", "老客曝光人数")
    vals.update({
        "DN": exp, "DQ": ent, "DT": rate(ent, exp), "DW": ord_, "DZ": rate(ord_, ent),
        "EC": ec, "EF": rate(ne, ec), "EI": rate(nb, ne),
        "EL": el, "EO": rate(oe, el), "ER": rate(ob, oe),
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
            "DN": _ws.get("exp") or vals.get("DN"), "DQ": _ws.get("ent") or vals.get("DQ"),
            "DT": _ws.get("ent_rate") or vals.get("DT"),
            "DW": _ws.get("ord") or vals.get("DW"), "DZ": _ws.get("ord_rate") or vals.get("DZ"),
        })
        # 商圈TOP(前10%均值) ← 单店流量页把基准切到「商圈同行前10%均值」后的**基准列**。
        # 2026-10-01 真机核实: 品牌版 chain 视图没有商圈对比(旧代码把「顾客=新客」那列当TOP写=错);
        # 单店视图(single/business-analysis)的基准下拉里才有 商圈同行均值 / 商圈同行前10%均值。
        # bench 没切成前10%(或没抓到) → top10 为空 → 这些列留空(宁可不写, 也不写错)。
        top = w.get("top10") or {}
        vals.update({
            "DO": top.get("exp"), "DR": top.get("ent"), "DU": top.get("ent_rate"),
            "DX": top.get("ord"), "EA": top.get("ord_rate"),
        })
        zb = (capture.get("爆品团_全部顾客") or {}).get("top10") or {}
        vals.update({
            "FA": zb.get("exp"), "FD": zb.get("ent"), "FG": zb.get("ent_rate"),
            "FJ": zb.get("ord"), "FM": zb.get("ord_rate"),
        })
        # 新客/老客的商圈TOP ← 单店页切到「新客/老客」维度后、基准仍为前10%时读到的基准列
        seg = capture.get("segments") or {}
        _w = (seg.get("外卖") or {})
        _z = (seg.get("爆品团") or {})
        _nk = ((_w.get("新客") or {}).get("top10") or {})
        _lk = ((_w.get("老客") or {}).get("top10") or {})
        vals.update({
            "ED": _nk.get("exp"), "EG": _nk.get("ent"), "EJ": _nk.get("ent_rate"),
            "EM": _lk.get("exp"), "EP": _lk.get("ent"), "ES": _lk.get("ent_rate"),
        })
        _znk = ((_z.get("新客") or {}).get("top10") or {})
        _zlk = ((_z.get("老客") or {}).get("top10") or {})
        vals.update({
            "FP": _znk.get("exp"), "FS": _znk.get("ent"), "FV": _znk.get("ent_rate"),
            "FY": _zlk.get("exp"), "GB": _zlk.get("ent"), "GE": _zlk.get("ent_rate"),
        })
        # 新客/老客: 列口径是**转化率**(不是人数); 只有顾客维度自校验通过(dims_ok)才覆盖。
        if dims_ok:
            _nk_s = (capture.get("外卖_新客") or {}).get("shop") or {}
            _o_s = (capture.get("外卖_老客") or {}).get("shop") or {}
            vals.update({
                "EC": _nk_s.get("exp") or vals.get("EC"),
                "EF": _nk_s.get("ent_rate") or vals.get("EF"),
                "EI": _nk_s.get("ord_rate") or vals.get("EI"),
                "EL": _o_s.get("exp") or vals.get("EL"),
                "EO": _o_s.get("ent_rate") or vals.get("EO"),
                "ER": _o_s.get("ord_rate") or vals.get("ER"),
            })
        p = capture.get("promo") or {}
        # 推广 4 列: GG 消耗 / GH 曝光 / GI 进店 / GL 充值
        # (2026-10-08 诊断: 以前推广只在**品牌级**抓 → 逐店 vals 里没 promo → 这 4 列永远空;
        #  现在 capture_per_store 会读每店自己的推广页并放进 cap["promo"], 这里照收)
        for k in ("GG", "GH", "GI", "GL"):
            if p.get(k) is not None:
                vals[k] = p[k]
    # 天气/温度 (C/D)
    if weather[0] is not None:
        vals["C"] = weather[0]
    if weather[1] is not None:
        vals["D"] = weather[1]
    return vals
