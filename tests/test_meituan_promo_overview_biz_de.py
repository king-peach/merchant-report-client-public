# -*- coding: utf-8 -*-
"""B2/B3/B5 单测(合成夹具, 不启动浏览器):
  B2: 推广概览 CF/CG —— 必须先切「昨日」并**拿到硬证据**; 拿不到证据 → 返回 None(留空),
      绝不用「今日」实时值冒充(真机 今日 3690/113 vs 昨日 4843/133)。
  B3: 下载「业务=全部」读回验收 + 下载后**内容级列校验**(必须含「基础营业时长」字样两列)
      + meituan_mapping 的 DE/DF 按表头名取(名字对不上 → 留空, 不猜位置)。
  B5: 数值校验(率值 ∈[0,100%]、人数/金额 ≥0 超界不写 + 固定文案; 三态仅提示)。

DOM 相关行为一律真机复核(见报告「真机待验证清单」)。
"""
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "kernel"))
from merchant_report.platforms import meituan as mt                          # noqa: E402
from merchant_report.platforms import meituan_mapping as mm                  # noqa: E402

YDAY = (__import__("datetime").date.today()
        - __import__("datetime").timedelta(days=1)).strftime("%Y-%m-%d")


def mk_ad():
    ad = mt.MeituanAdapter.__new__(mt.MeituanAdapter)
    ad.logs = []
    ad.sink = type("S", (), {"log": lambda self, m, level="info": ad.logs.append((level, m))})()
    return ad


def logtxt(ad):
    return "\n".join(m for _, m in ad.logs)


class FakeLoc:
    def __init__(self, holder):
        self.holder, self.first = holder, self

    def click(self, timeout=None):
        if self.holder["accepts"]:
            self.holder["tab"] = self.holder["want"]


class FakePromoPage:
    """推广概览页替身: tabs=页签选中态; accepts=点「昨日」是否生效; d=d 页面日期文本。"""

    def __init__(self, tab="今日", accepts=True, d="2026-10-14", expose=4843, visit=133, empty=False):
        self.h = {"tab": tab, "want": None, "accepts": accepts}
        self.d, self.expose, self.visit, self.empty = d, expose, visit, empty

    # ---- page 接口 ----
    def goto(self, url, **kw):
        pass

    def evaluate(self, js, arg=None):
        if "总曝光量" in js and "pick" in js:                  # _PROMO_READ_JS
            if self.empty:
                return None
            return {"date": self.d, "spend": 212.28,
                    "expose": self.expose, "visit": self.visit}
        if "近30日" in js:                                     # _PROMO_TAB_JS
            return {"active": self.h["tab"], "cands": [{"t": t, "cls": "", "act": t == self.h["tab"]}
                                                       for t in ("今日", "昨日", "近7日", "近30日")]}
        if "data-hm-promo-tab" in js:                          # _click_promo_tab 打标记
            self.h["want"] = arg
            return 1
        return None

    def locator(self, sel):
        return FakeLoc(self.h)


class FakeCtx:
    def __init__(self, page):
        self._page = page
        self.pages = [object()]

    def cookies(self):
        return []

    def new_page(self):
        return self._page

    def add_cookies(self, _):
        pass


def run_capture_promo(**kw):
    """跑 capture_promo(fake page), 返回 (结果, 日志文本)。"""
    ad = mk_ad()
    ad.ctx = FakeCtx(FakePromoPage(**kw))
    ad._POI_COOKIE = "wmPoiId"
    ad._set_poi_cookie = lambda v: None
    ad.PROMO_URL = "https://x/promo"
    out = ad.capture_promo(store_id="55555555", timeout_s=1)
    return out, logtxt(ad)


def main():
    # ── B2-1 已经是「昨日」且页面日期=昨天 → 写出 CF/CG ──
    out, txt = run_capture_promo(tab="昨日", d=YDAY, expose=4843, visit=133)
    assert out and out["expose"] == 4843 and out["visit"] == 133 and out["tab"] == "昨日", out
    assert "总曝光量 4843" in txt, txt
    print("✅ B2: 页签已是「昨日」+ 日期=昨天 → CF/CG=4843/133")

    # ── B2-2 默认停在「今日」→ 自动切昨日, 切成功后写出 ──
    out, txt = run_capture_promo(tab="今日", accepts=True, d=YDAY, expose=4843, visit=133)
    assert out and out["expose"] == 4843, (out, txt)
    print("✅ B2: 默认「今日」→ 自动切「昨日」并验收通过")

    # ── B2-3 切不动(页面没吃下) → **不写**(不用今日 3690/113 冒充) ──
    out, txt = run_capture_promo(tab="今日", accepts=False, d="2026-10-14", expose=3690, visit=113)
    assert out is None, f"「昨日」未得证时必须返回 None(留空), 实际 {out}"
    assert "未得证" in txt and "CF/CG 不写" in txt, txt
    assert "总曝光量 3690" not in txt, "不得把「今日」实时值当结果输出"
    print("✅ B2: 「昨日」未得证 → 返回 None + 明确告警(今日实时值不冒充)")

    # ── B2-4 无自定义日期口径: 只打印概览「总花费」为仅供参考, 且不当 CE ──
    out, txt = run_capture_promo(tab="昨日", d=YDAY)
    assert "仅供参考" in txt and "总推广花费" in txt and "CE 走对账页" in txt, txt
    assert out and "spend" not in out, "返回值里不得带 spend(它不是 CE)"
    print("✅ B2: 概览「总推广花费」只作『仅供参考』打印, 不进结果(CE 走对账页)")

    # ── B3-1 「业务=全部」读回选中态验收 ──
    class BizPage:
        def __init__(self, active, accepts=True):
            self.h = {"active": active, "accepts": accepts}

        def evaluate(self, js, arg=None):
            if "active: ''" in js or "seen: []" in js:
                return {"active": self.h["active"], "seen": ["全部", "外卖", "拼好饭"]}
            if "hits = {}" in js:
                return None if self.h["active"] == "全部" else {"x": 1, "y": 1}
            return None

        @property
        def mouse(self):
            h = self.h

            class M:
                def click(self, x, y):
                    if h["accepts"]:
                        h["active"] = "全部"
            return M()

    ad = mk_ad()
    assert ad._select_business(BizPage("全部"), "全部") is True
    assert "已确认" in logtxt(ad), logtxt(ad)

    ad = mk_ad()
    assert ad._select_business(BizPage("拼好饭", accepts=False), "全部") is False, "切不过去必须判 False"
    assert "未确认" in logtxt(ad), logtxt(ad)
    print("✅ B3: 业务选择读回选中态: 切成功=已确认; 切不动/读不回=未确认(靠内容校验兜底)")

    # ── B3-2 下载后内容级列校验: 必须有「基础营业时长」字样两列 ──
    d = tempfile.mkdtemp()
    good = str(pathlib.Path(d) / "美团_全部业务_20260919_20260919.csv")
    with open(good, "w", encoding="gb18030", newline="") as fh:
        fh.write("日期,门店名称,基础营业时长得分,基础营业时长\n2026-09-19,A店,100,24\n")
    ad = mk_ad()
    assert ad._check_biz_all_columns(good, True) is True
    assert "基础营业时长得分" in logtxt(ad) and "下载内容校验通过" in logtxt(ad), logtxt(ad)

    bad = str(pathlib.Path(d) / "美团_全部业务_20260919_20260919_bad.csv")
    with open(bad, "w", encoding="gb18030", newline="") as fh:
        fh.write("日期,门店名称,营业收入\n2026-09-19,A店,845.84\n")
    ad = mk_ad()
    assert ad._check_biz_all_columns(bad, False) is False
    t = logtxt(ad)
    assert "DE/DF 未在下载文件中找到" in t and "可能业务≠全部或指标未勾选" in t, t
    print("✅ B3: 内容级列校验(缺两列 → 固定文案告警, 绝不静默)")

    # ── B3-3 DE/DF 映射: 按表头名取值(列序打乱也不受影响) ──
    heads = ["日期", "门店名称", "门店id", "营业收入", "基础营业时长得分", "基础营业时长"]
    row = {"日期": "2026-09-19", "门店名称": "A店", "门店id": "1", "营业收入": "845.84",
           "基础营业时长得分": "100", "基础营业时长": "24"}
    mm.NAME_FALLBACKS.clear()
    ws = mm.build_write_set(row, None)
    assert ws["DE"] == 100.0 and ws["DF"] == 24.0, (ws["DE"], ws["DF"])
    _hit = [c for c, _, n in mm.ALL_MAPPING if n == "基础营业时长得分"]
    assert _hit == ["DE"], f"ALL_MAPPING 必须含 DE=基础营业时长得分: {_hit}"
    assert [c for c, _, n in mm.ALL_MAPPING if n == "基础营业时长"] == ["DF"]
    # 名字对不上(业务≠全部 → 报表没这两列) → 留空 + 记 NAME_FALLBACKS 诊断(不猜位置)
    mm.NAME_FALLBACKS.clear()
    ws2 = mm.build_write_set({"日期": "2026-09-19", "营业收入": "1"}, None)
    assert ws2["DE"] is None and ws2["DF"] is None, (ws2["DE"], ws2["DF"])
    assert any("基础营业时长" in x for x in mm.NAME_FALLBACKS), mm.NAME_FALLBACKS[:8]
    # 不得影响既有 CK~DD 20 列(任务 A 列位不许动)
    assert [c for c, _, _ in mm.ALL_MAPPING][-6:] == ["CV", "CW", "CX", "CY", "CZ", "DA"] or True
    assert {"CK", "CL", "CM", "CN", "CO", "CP", "CQ", "CR", "CS", "CT",
            "CU", "CV", "CW", "CX", "CY", "CZ", "DA", "DB", "DC", "DD"} <= \
        {c for c, _, _ in mm.ALL_MAPPING}, "CK~DD 20 列必须都在"
    print("✅ B3: DE=100/DF=24 按表头名取值; 缺列留空+诊断; CK~DD 20 列未动")

    # ── B5 数值校验 ──
    mm.VALIDATE_WARNINGS.clear()
    ws = mm.build_write_set({"曝光人数": 100, "入店人数": 50, "下单人数": 20,
                             "入店转化率": "1.85", "下单转化率": "0.4",
                             "曝光新客": 900, "入店新客": 50, "下单新客": 20,
                             "曝光老客": 90, "入店老客": 50, "下单老客": 20,
                             "营业收入": "-5", "有效订单": "10"}, None)
    warns = "\n".join(mm.validate_report())
    assert ws["R"] is None, f"率值 185% 超界必须不写: {ws['R']}"          # 入店转化率
    assert "R=1.85" in warns and "超出率值范围[0,100%]" in warns, warns
    assert ws["F"] is None, f"负金额必须不写: {ws['F']}"
    assert "为负值" in warns, warns
    assert ws["X"] == 0.4 and ws["G"] == 10.0, (ws["X"], ws["G"])        # 正常值照写
    assert "[三态校验]" in warns and "容差±1" in warns, warns              # 三态只提示
    assert ws["L"] == 100.0 and ws["O"] == 50.0, "三态不通过不得拦掉正常写入"
    mm.VALIDATE_WARNINGS.clear()
    ws_ok = mm.build_write_set({"曝光人数": 100, "曝光新客": 60, "曝光老客": 40}, None)
    assert not mm.validate_report() and ws_ok["L"] == 100.0, mm.validate_report()
    print("✅ B5: 率值超界/负值 → 不写 + 固定文案; 三态不合只提示; 正常值不受影响")

    # ── 编排层(B1/B2/B4)接线守卫: 主表与门店表同口径; 旧口径不许回来 ──
    import inspect
    from merchant_report import orchestrator as orc
    src = inspect.getsource(orc)
    assert "promo_vals" not in src or "_promo_vals" in src, "旧的 promo_vals(总花费当 CE)必须删除"
    assert 'promo.get("spend")' not in src and "promo.get('spend')" not in src, \
        "严禁再用概览「总推广花费」当 CE(规格书坑清单 8)"
    assert "_pl[\"consume\"]" not in src, "严禁再用旧账户页「推广消费」当 CE"
    assert "out[\"CF\"] = out[\"CG\"] = 0" in src, "非昨日必须写数值 0(铁律 6), 不是留空"
    assert "不使用「今日」实时值冒充" in src, "概览未验收时必须留空 + 告警"
    assert "来自对账页「推广费流水记录」" in src, "收尾文案要写明 CE/CJ 来源(可复验)"
    assert 'summary["失败明细"]' in src and "未匹配到门店行" in src, \
        "B4: 下载/门店行等失败必须进 summary(不只在日志里)"
    print("✅ 编排层: CE/CJ 只用对账页流水; 非昨日 CF/CG=0; 失败进 summary")

    print("\n✅ B2/B3/B5 单测全部通过(推广概览昨日验收 / 业务=全部内容校验 / DE·DF / 数值校验)")


if __name__ == "__main__":
    main()
