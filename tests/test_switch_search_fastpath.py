# -*- coding: utf-8 -*-
"""切店「搜索编号直选」快路径 + 切店耗时打点 单测(2026-10-13)。

任务背景(用户口径): 选店弹窗里**直接搜门店ID/机构编码**快速选店, 省掉"一个个选";
让能这么做的平台都做(淘宝 shop_id / 管家 12 位机构编码; 美团走 cookie、京东不做)。

纪律(宁可不快, 不许选错店):
  · 一切新 UI 路径必须「探测 + 验证 + 回退」三件套;
  · 淘宝成功判据 = URL `/app/shop/<id>/` 必须 == 目标编号(强验证), 右上角店名尽量同时匹配;
  · 管家成功判据 = 表头**全名**(维持 2026-10-11 纪律, 不用前缀);
  · 快路径任何一步不成立 → 完整回退旧路径(行为与旧版一致)。

本测试用 fake page/locator 桩, 不连浏览器。覆盖:
  ① 淘宝: 搜索路径成功(输入→过滤→点中→URL 验证通过)
  ② 淘宝: 搜索失败回退按名(回退后行为与旧路径一致 —— 仍能按名切成功)
  ③ 验证函数: id 匹配/不匹配都不误判(URL 里是别家店的 id → 判失败)
  ④ 管家: 有编码 → 先输编码; 搜不到 → 回退按名(且验证仍用全名)
  ⑤ 管家: 拿不到编码 → 直接按店名搜索
  ⑥ 打点文案格式 + 汇总格式
  ⑦ 接线守卫(inspect.getsource): switch_shop 含搜索快路径与回退; _switch_store 含编码优先;
     orchestrator 有 _switch_id_map 接线
"""
import inspect
import pathlib
import sys
import types

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "kernel"))

from merchant_report.platforms import taobao as TB                              # noqa: E402
from merchant_report.platforms import meituan_gj as GJ                          # noqa: E402
from merchant_report.platforms.base import SWITCH_PATHS, ShopSwitchTimer        # noqa: E402

FAILS = []


def check(name, cond, extra=""):
    print(("  ✅ " if cond else "  ❌ ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


def _sink():
    logs = []
    return types.SimpleNamespace(log=lambda m, level="info": logs.append((level, m))), logs


class _FakeClock:
    """替身时钟: sleep 空转 + time() 每次 +0.5s —— 让"按时间轮询等待"的验收循环瞬间跑完
    (不这样每个 case 都要真等十几秒, 63 个测试文件的整套跑法受不了)。"""

    def __init__(self):
        self.t = 10_000.0

    def time(self):
        self.t += 0.5
        return self.t

    def sleep(self, s):
        self.t += float(s or 0)


def _patch_clock(mod):
    orig = mod.time
    fake = _FakeClock()
    mod.time = fake
    return orig, fake


def _restore(mod, orig):
    mod.time = orig


# ===================== fake: 淘宝切店弹层 =====================
class FakeLocator:
    def __init__(self, page, els):
        self.page = page
        self.els = els

    def count(self):
        return len(self.els)

    def nth(self, i):
        return FakeLocator(self.page, [self.els[i]])

    @property
    def first(self):
        return self

    def is_visible(self):
        return bool(self.els)

    def click(self, timeout=None, force=False):
        self.page._on_click(self.els[0] if self.els else None)

    def fill(self, v):
        self.page.typed = []

    def type(self, t, delay=None):
        self.page.typed.append(t)
        self.page.switcher_open = True


class FakeKeyboard:
    def __init__(self):
        self.keys = []

    def press(self, k):
        self.keys.append(k)


class FakeTaobaoPage:
    """淘宝切店弹层桩。

    click_urls: 每次点击后依次把 page.url 设成的值(None=该次点击不改 URL)。
    这样能精确模拟「搜索路径点了但 URL 验收不过 → 回退按名后才变目标」。
    """

    SHOP_ID = "1111111111"

    def __init__(self, shop_name, shop_id=SHOP_ID, search_input_present=True,
                 filter_rows=None, click_urls=None, start_url=None):
        self.shop_name = shop_name
        self.shop_id = shop_id
        self.search_input_present = search_input_present
        self.filter_rows = filter_rows if filter_rows is not None else [
            {"txt": shop_name, "lv": "2", "cls": "itemShopName__x"}]
        self.click_urls = list(click_urls or [])
        self.url = start_url or "https://melody.shop.ele.me/app/shop/1/dashboard"
        self.typed = []
        self.keyboard = FakeKeyboard()
        self.switcher_open = False

    def _on_click(self, el):
        # 只有点「结果行」才推进 URL(点搜索框不算)
        if el == self.shop_name and self.click_urls:
            self.url = self.click_urls.pop(0)

    def locator(self, sel):
        if "input" in sel:
            if self.search_input_present and ("搜索" in sel or "门店" in sel
                                              or sel == "#shopSwitcher input"):
                return FakeLocator(self, ["input-search"])
            return FakeLocator(self, [])
        if ">> text=" in sel and self.switcher_open:
            return FakeLocator(self, [self.shop_name])
        if self.switcher_open:
            return FakeLocator(self, [self.shop_name])
        return FakeLocator(self, [])

    def evaluate(self, js, arg=None):
        if "itemShopName" in js:          # _SHOP_SEARCH_ROWS_JS
            return list(self.filter_rows) if self.switcher_open else []
        if "shop-name__" in js:           # _shop_name: URL 已是目标店才算
            return self.shop_name if f"/app/shop/{self.shop_id}/" in self.url else ""
        return True


def _taobao_adapter(id_by_name=None):
    ad = TB.TaobaoAdapter.__new__(TB.TaobaoAdapter)
    sink, logs = _sink()
    ad.sink = sink
    ad.id_by_name = id_by_name or {}
    ad.switch_timer = ShopSwitchTimer("淘宝")
    ad.page = None
    ad.shop_id = ""
    # 打开弹层/收起弹层与真机一致的点开动作不在此测(真机另有验证), 这里只测搜索/回退判定
    ad._open_switcher = lambda page, expand=True: setattr(page, "switcher_open", True)
    ad._close_switcher = lambda page: setattr(page, "switcher_open", False)
    return ad, logs


# ===================== ① 淘宝: 搜索路径成功 =====================
def test_taobao_search_success():
    name = "示例柠檬茶(湘潭岚霞路店)"
    page = FakeTaobaoPage(name, shop_id="4444444444",
                          click_urls=["https://melody.shop.ele.me/app/shop/4444444444/dashboard"])
    ad, logs = _taobao_adapter({name: "4444444444"})
    sid = ad.switch_shop(name, page)
    check("① 淘宝搜索ID 切店成功返回 shop_id", sid == "4444444444", sid)
    check("① 淘宝搜索ID 真的键盘输入了编号", page.typed == ["4444444444"], page.typed)
    check("① 淘宝搜索ID 打点路径=搜索ID", ad.switch_timer.path_counts.get("搜索ID") == 1,
          ad.switch_timer.path_counts)
    check("① 淘宝搜索ID 没走回退", not any("回退" in m for _, m in logs), logs)


# ===================== ② 淘宝: 搜索失败回退按名 =====================
def test_taobao_fallback_no_searchbox():
    """弹层里没有搜索框 → 快路径不成立 → 完整回退按名, 回退后仍能按名切成功。"""
    name = "示例柠檬茶(湘潭岚霞路店)"
    page = FakeTaobaoPage(name, shop_id="4444444444", search_input_present=False,
                          click_urls=["https://melody.shop.ele.me/app/shop/4444444444/dashboard"])
    ad, logs = _taobao_adapter({name: "4444444444"})
    sid = ad.switch_shop(name, page)
    check("② 无搜索框 → 回退后按名切店仍成功", sid == "4444444444", sid)
    check("② 回退路径打点=搜索ID回退", ad.switch_timer.path_counts.get("搜索ID回退") == 1,
          ad.switch_timer.path_counts)
    check("② 回退有 info 日志(不刷 warn)", any(lv == "info" and "回退" in m for lv, m in logs), logs)
    check("② 回退不误报 warn", not [m for lv, m in logs if lv == "warn"],
          [m for lv, m in logs if lv == "warn"])


def test_taobao_fallback_no_result():
    """搜索框在但输编号过滤不出任何行 → 回退按名。"""
    name = "示例柠檬茶(湘潭岚霞路店)"
    page = FakeTaobaoPage(name, shop_id="4444444444", filter_rows=[],
                          click_urls=["https://melody.shop.ele.me/app/shop/4444444444/dashboard"])
    ad, logs = _taobao_adapter({name: "4444444444"})
    sid = ad.switch_shop(name, page)
    check("②' 无过滤结果 → 回退按名成功", sid == "4444444444", sid)
    check("②' 打点=搜索ID回退", ad.switch_timer.path_counts.get("搜索ID回退") == 1,
          ad.switch_timer.path_counts)


def test_taobao_no_id_keeps_old_path():
    """没配编号 → 直接老流程(展开按名), 不输入任何东西。"""
    name = "示例柠檬茶(湘潭岚霞路店)"
    page = FakeTaobaoPage(name, shop_id="4444444444",
                          click_urls=["https://melody.shop.ele.me/app/shop/4444444444/dashboard"])
    ad, logs = _taobao_adapter({})
    sid = ad.switch_shop(name, page)
    check("②'' 无编号 → 走旧展开按名路径", ad.switch_timer.path_counts.get("展开按名") == 1,
          ad.switch_timer.path_counts)
    check("②'' 无编号仍按名切成功", sid == "4444444444", sid)
    check("②'' 无编号不输任何编号", page.typed == [], page.typed)


# ===================== ③ 验证函数: id 匹配/不匹配 =====================
def test_verify_shop_strict_id():
    name = "某店"
    ad, _ = _taobao_adapter()

    wrong = FakeTaobaoPage(name, shop_id="9999999999",
                           start_url="https://melody.shop.ele.me/app/shop/8888888888/dashboard")
    ok, sid = ad._verify_shop(wrong, name, "9999999999", timeout_s=0.01, poll=0.001)
    check("③ 验证: URL 是别家店 id → 判失败", (ok is False and sid == "8888888888"), (ok, sid))

    right = FakeTaobaoPage(name, shop_id="9999999999",
                           start_url="https://melody.shop.ele.me/app/shop/9999999999/dashboard")
    ok2, sid2 = ad._verify_shop(right, name, "9999999999", timeout_s=0.01, poll=0.001)
    check("③ 验证: URL == 目标 id → 通过", (ok2 is True and sid2 == "9999999999"), (ok2, sid2))

    # URL 还没变(无 tid 时) → 用右上角店名兜底
    late = FakeTaobaoPage(name, shop_id="9999999999",
                          start_url="https://melody.shop.ele.me/app/shop/9999999999/dashboard")
    ok3, _ = ad._verify_shop(late, name, "", timeout_s=0.01, poll=0.001)
    check("③ 验证: 无编号时按店名兜底", ok3 is True, ok3)

    none = FakeTaobaoPage(name, shop_id="9999999999",
                          start_url="https://melody.shop.ele.me/app/shop/1/dashboard")
    ok4, _ = ad._verify_shop(none, name, "9999999999", timeout_s=0.01, poll=0.001)
    check("③ 验证: URL 未变 → 判失败(不误判)", ok4 is False, ok4)


def test_taobao_search_verify_fail_falls_back():
    """搜索路径点中了但 URL 验收不过(变成别家店) → 回退按名(最终成功)。"""
    name = "示例柠檬茶(湘潭岚霞路店)"
    # 第 1 次点击(搜索行) → 别家店 id; 第 2 次点击(按名) → 目标 id
    page = FakeTaobaoPage(name, shop_id="4444444444",
                          click_urls=["https://melody.shop.ele.me/app/shop/7777777777/dashboard",
                                      "https://melody.shop.ele.me/app/shop/4444444444/dashboard"])
    ad, logs = _taobao_adapter({name: "4444444444"})
    sid = ad.switch_shop(name, page)
    check("③' 搜索验收不过 → 回退按名最终成功", sid == "4444444444", sid)
    check("③' 验收不过记入回退", ad.switch_timer.path_counts.get("搜索ID回退") == 1,
          ad.switch_timer.path_counts)


# ===================== ④ 管家: 编码优先 + 回退 =====================
class FakeGjLocator:
    def __init__(self, page):
        self.page = page

    @property
    def first(self):
        return self

    def click(self, timeout=None):
        pass

    def fill(self, v):
        self.page.typed = []
        self.page.cur_kw = ""

    def type(self, t, delay=None):
        self.page.typed.append(t)
        self.page.history.append(t)
        self.page.cur_kw = t


class FakeGjPage:
    """管家切店弹窗桩。

    rows_for: {当前搜索词: 表格里能否命中目标行} —— 编码搜不到 → False → 触发回退。
    行按「当前搜索词」决定命中(模拟真实过滤: 搜什么决定列表显示什么)。
    表头: 默认只有**真的点中了目标行**才变成目标店名(否则停在「旧店」) —— 这样"没找到行"
    不会被"迟生效"误判成成功。header_seq 给了就按它逐次弹(测前缀陷阱用)。
    """

    def __init__(self, name, rows_for=None, header_seq=None):
        self.name = name
        self.rows_for = rows_for or {}
        self.forced_header = list(header_seq) if header_seq is not None else None
        self.typed = []
        self.history = []     # 所有输入过的搜索词(跨 clear/重试, 供"先编编码后按名"断言)
        self.cur_kw = ""
        self.clicked_ok = False
        self.mouse = types.SimpleNamespace(move=lambda x, y: None,
                                           click=self._on_mouse_click)

    def _on_mouse_click(self, x, y):
        if bool(self.rows_for.get(self.cur_kw, True)):
            self.clicked_ok = True

    def evaluate(self, js, arg=None):
        if "elementFromPoint" in js and "closest" in js:      # _VERIFY_JS
            return {"ok": True}
        if "elementFromPoint" in js:                          # _ROW_HIT_JS
            ok = bool(self.rows_for.get(self.cur_kw, True))
            return ({"px": 100, "py": 200, "cx": 150, "cy": 200,
                     "txt": f"{self.name} 门店 123 456", "ok1": ok, "ok2": ok} if ok else None)
        if "innerText" in js and "perspective-switch" in js:  # _header_store
            if self.forced_header:
                return self.forced_header.pop(0)
            return self.name if self.clicked_ok else "旧店"
        if "org-switch-modal" in js:                          # MODAL_VIS_JS
            return True
        return None

    def locator(self, sel):
        return FakeGjLocator(self)


def _gj_adapter(id_by_name=None, store_ids=None):
    ad = GJ.MeituanGjAdapter.__new__(GJ.MeituanGjAdapter)
    sink, logs = _sink()
    ad.sink = sink
    ad.id_by_name = id_by_name or {}
    ad.store_ids = store_ids or {}
    ad.switch_timer = ShopSwitchTimer("管家")
    ad._open_store_switch = lambda page: True
    return ad, logs


def test_gj_code_priority_success():
    name = "湘潭岳塘岚霞路店"
    page = FakeGjPage(name)
    ad, logs = _gj_adapter({name: "202000000002"})
    ok = ad._switch_store(page, name)
    check("④ 管家编码优先: 切店成功", ok is True, ok)
    check("④ 管家先输的是 12 位编码", page.history and page.history[0] == "202000000002", page.history)
    check("④ 管家打点路径=搜索编码", ad.switch_timer.path_counts.get("搜索编码") == 1,
          ad.switch_timer.path_counts)


def test_gj_code_fail_falls_back_to_name():
    """编码搜不出目标行 → 完整回退按店名搜索(且最终成功)。"""
    name = "湘潭岳塘岚霞路店"
    page = FakeGjPage(name, rows_for={"202000000002": False})   # 编码搜不到行; 店名能搜到
    ad, logs = _gj_adapter({name: "202000000002"})
    ok = ad._switch_store(page, name)
    check("④' 编码搜不到 → 回退按名成功", ok is True, ok)
    check("④' 先试了编码(纯数字)", page.history and page.history[0] == "202000000002", page.history)
    check("④' 回退后试了店名", name in page.history, page.history)
    check("④' 打点=搜索编码回退", ad.switch_timer.path_counts.get("搜索编码回退") == 1,
          ad.switch_timer.path_counts)


def test_gj_verify_still_full_name():
    """编码路径验证仍用表头**全名**: 表头停在前缀相同的别家店 → 不许判成功。"""
    name = "长沙西服务区北区店"
    page = FakeGjPage(name, header_seq=["长沙西服务区南区店"] * 400)
    ad, _ = _gj_adapter({name: "202000000002"})
    ok = ad._switch_store(page, name)
    check("④'' 编码路径验证仍全名(前缀陷阱不误判)", ok is False, ok)


def test_gj_no_code_searches_by_name():
    name = "湘潭岳塘岚霞路店"
    page = FakeGjPage(name)
    ad, logs = _gj_adapter({})
    ok = ad._switch_store(page, name)
    check("⑤ 无编码 → 按店名搜索成功", ok is True, ok)
    check("⑤ 首输词=店名(不是数字)", page.history and page.history[0] == name, page.history)
    check("⑤ 打点=搜索按名", ad.switch_timer.path_counts.get("搜索按名") == 1,
          ad.switch_timer.path_counts)


def test_gj_sid_from_cards_fallback():
    """门店明细没有, 但门店选择页卡片读到 sid → 仍走编码快路径。"""
    name = "湘潭岳塘岚霞路店"
    page = FakeGjPage(name)
    ad, _ = _gj_adapter(store_ids={name: "202000000002"})
    ad._switch_store(page, name)
    check("⑤' 卡片 sid 兜底: 仍走编码", page.history and page.history[0] == "202000000002", page.history)


# ===================== ⑥ 打点文案格式 =====================
def test_timer_format():
    timer = ShopSwitchTimer()
    sink, logs = _sink()
    timer.log_line(sink, "岚霞路店", 3.24, "搜索ID")
    timer.log_line(sink, "绿地之窗店", 12.0, "展开按名")
    timer.summary(sink)
    check("⑥ 单条文案含 店名/秒数/路径",
          any(m == "⏱ 切店 岚霞路店 3.2s (搜索ID)" for _, m in logs), logs)
    summ = [m for _, m in logs if m.startswith("⏱ 切店统计")]
    check("⑥ 汇总一行且含 次数/平均/总计/分布", bool(summ) and all(
        k in summ[0] for k in ("2 次", "平均", "总计", "路径分布")), summ)
    n0 = len([1 for _, m in logs if m.startswith("⏱ 切店统计")])
    timer.summary(sink)
    check("⑥ 汇总幂等(不重复)", len([1 for _, m in logs if m.startswith("⏱ 切店统计")]) == n0, logs)
    check("⑥ 路径名都在白名单", set(timer.path_counts) <= set(SWITCH_PATHS), timer.path_counts)


# ===================== ⑦ 接线守卫 =====================
def test_wiring_guards():
    src = inspect.getsource(TB.TaobaoAdapter.switch_shop)
    check("⑦ 淘宝 switch_shop 含搜索快路径", "_switch_by_id" in src, src[:200])
    check("⑦ 淘宝 switch_shop 含回退按名", "_switch_by_name" in src, src[:200])
    check("⑦ 淘宝 switch_shop 有打点", "switch_timer.log_line" in src, src[:200])

    src2 = inspect.getsource(TB.TaobaoAdapter._switch_by_id)
    check("⑦ 淘宝快路径: 探测搜索框", "_switcher_search_input" in src2)
    check("⑦ 淘宝快路径: 真键盘输入", ".type(" in src2)
    check("⑦ 淘宝快路径: 严格 URL 验证", "_verify_shop" in src2)
    check("⑦ 淘宝快路径: 不展开组织节点(省耗时)", "expand=False" in src2)

    v = inspect.getsource(TB.TaobaoAdapter._verify_shop)
    check("⑦ 验证函数: URL id 必须相等", "sid == tid" in v)

    gsrc = inspect.getsource(GJ.MeituanGjAdapter._switch_store)
    check("⑦ 管家 _switch_store 含编码优先", "_id_for" in gsrc and "搜索编码" in gsrc)
    check("⑦ 管家 _switch_store 有回退", "_search_and_pick" in gsrc)

    gv = inspect.getsource(GJ.MeituanGjAdapter._search_and_pick)
    check("⑦ 管家验证仍用全名 _gj_same_store", "_gj_same_store" in gv)

    from merchant_report import orchestrator as OR
    check("⑦ orchestrator 有编号接线函数", "def _switch_id_map" in inspect.getsource(OR))
    bsrc = inspect.getsource(OR.run_task)
    check("⑦ orchestrator 把编号映射注入适配器", "id_by_name = _sid_map" in bsrc, bsrc[:200])

    from merchant_report import store_details as SD
    dsrc = inspect.getsource(SD.load_store_details)
    check("⑦ 门店明细产出 by_name_id", "by_name_id" in dsrc)


def main():
    # 替身时钟: 验收/重试里有固定等待, 真等会让本文件跑 1 分多钟(整套 63 文件受不了)。
    # 只替换这两个模块的 time, 不影响别的测试进程。
    orig_tb, _ = _patch_clock(TB)
    orig_gj, _ = _patch_clock(GJ)
    try:
        test_taobao_search_success()
        test_taobao_fallback_no_searchbox()
        test_taobao_fallback_no_result()
        test_taobao_no_id_keeps_old_path()
        test_verify_shop_strict_id()
        test_taobao_search_verify_fail_falls_back()
        test_gj_code_priority_success()
        test_gj_code_fail_falls_back_to_name()
        test_gj_verify_still_full_name()
        test_gj_no_code_searches_by_name()
        test_gj_sid_from_cards_fallback()
    finally:
        _restore(TB, orig_tb)
        _restore(GJ, orig_gj)
    test_timer_format()
    test_wiring_guards()
    print()
    if FAILS:
        print(f"❌ {len(FAILS)} 条失败: {FAILS}")
        return 1
    print("✅ 切店搜索快路径 + 打点 单测全绿")
    return 0


if __name__ == "__main__":
    sys.exit(main())
