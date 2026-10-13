# -*- coding: utf-8 -*-
"""天气: 天气现象=2345历史API(国内站点口径), 温度=Open-Meteo逐小时按营业时段窗口统计。
移植自已验证实现; 坐标/城市 areaId 可配。"""
import html as _html
import re
import threading
import urllib.request

WMO = {0: "晴", 1: "晴", 2: "多云", 3: "阴", 45: "雾", 48: "雾",
       51: "毛毛雨", 53: "毛毛雨", 55: "毛毛雨", 61: "小雨", 63: "中雨", 65: "大雨",
       71: "小雪", 73: "中雪", 75: "大雪", 80: "阵雨", 81: "阵雨", 82: "强阵雨",
       95: "雷阵雨"}
# 2345 城市areaId(历史页); 默认长沙, 客户配置可覆盖
# 湘潭 57773 为 2345 历史页 areaId(2026-10-14 核实)
AREA_ID = {"长沙": "59488", "湘潭": "57773"}

# 城市 → (纬度, 经度): **市级近似**, 供热图口径(同一城市的门店共用一组坐标);
# 如需精化(逐店门牌级)后续可换 geocode。湘潭用市级近似 (27.83, 112.94), 长沙沿用历史值。
CITY_COORDS = {"长沙": (28.187, 112.921), "湘潭": (27.83, 112.94)}
DEFAULT_CITY = "长沙"


def city_of_address(addr):
    """门店地址 → 城市名; 认不出 → None(调用方用默认长沙并记 warn)。

    只认明确写出的市名(「湘潭」/「长沙」), 不从区名推测 —— 猜错城市会把别城的天气写进表。
    """
    s = str(addr or "")
    if "湘潭" in s:
        return "湘潭"
    if "长沙" in s:
        return "长沙"
    return None


def coords_for(addr, default_city=DEFAULT_CITY):
    """门店地址 → (城市, 纬度, 经度)。地址缺失/认不出 → 默认城市(长沙)坐标。"""
    city = city_of_address(addr) or default_city
    lat, lon = CITY_COORDS.get(city, CITY_COORDS[DEFAULT_CITY])
    return city, lat, lon


def _fetch(url, referer=None, timeout=25):
    h = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
         "Accept-Language": "zh-CN,zh;q=0.9"}
    if referer:
        h["Referer"] = referer
    return urllib.request.build_opener(urllib.request.ProxyHandler({})) \
        .open(urllib.request.Request(url, headers=h), timeout=timeout).read().decode("utf-8", "ignore")


def _get_json(url, timeout=30):
    import json
    return json.loads(_fetch(url, timeout=timeout))


def fetch_weather_text(date, city="长沙"):
    """2345 历史API: 返回如 '阴~多云'; 失败 None"""
    area = AREA_ID.get(city)
    if not area:
        return None
    try:
        u = ("https://tianqi.2345.com/Pc/GetHistory?areaInfo%5BareaId%5D=" + area +
             "&areaInfo%5BareaType%5D=2"
             f"&date%5Byear%5D={date[:4]}&date%5Bmonth%5D={int(date[5:7])}")
        d = _fetch(u, referer="https://tianqi.2345.com/")
        i = d.find(date)
        if i <= 0:
            return None
        cells = [_html.unescape(c).strip() for c in re.findall(r">([^<>]+)<", d[i:i + 600]) if c.strip()]
        cells = [re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), c) for c in cells]
        for c in cells:
            if any(w in c for w in ("晴", "云", "阴", "雨", "雪", "雾", "雷")):
                return c
    except Exception:
        return None
    return None


def fetch_temp(date, lat, lon, biz_hours=None):
    """Open-Meteo 逐小时温度, 按 '14:15-22:50,23:02-23:22' 窗口统计 min~max; 返回 '24~31℃'"""
    def m(s):
        a, b = s.split(":")
        return int(a) * 60 + int(b)

    def window_filter(times, temps, wins):
        sel = []
        for i, ts in enumerate(times):
            hm = m(ts[-5:])
            if any(hm < e and hm + 60 > s for s, e in wins):
                if temps[i] is not None:
                    sel.append(temps[i])
        return sel

    times = temps = None
    for u in (
        f"https://archive-api.open-meteo.com/v1/archive?latitude={lat}&longitude={lon}"
        f"&start_date={date}&end_date={date}&hourly=temperature_2m&timezone=Asia%2FShanghai",
        f"https://historical-forecast-api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
        f"&start_date={date}&end_date={date}&hourly=temperature_2m&timezone=Asia%2FShanghai",
    ):
        try:
            h = _get_json(u).get("hourly") or {}
            times, temps = h.get("time") or [], h.get("temperature_2m") or []
            if temps:
                break
        except Exception:
            continue
    if not temps:
        return None
    sel = []
    if biz_hours and "-" in biz_hours:
        wins = [tuple(m(x) for x in seg.split("-")) for seg in biz_hours.split(",") if "-" in seg]
        sel = window_filter(times, temps, wins)
    if not sel:
        sel = [t for t in temps if t is not None]
    if not sel:
        return None
    return f"{round(min(sel)):g}~{round(max(sel)):g}℃"


def fetch(date, lat, lon, biz_hours=None, city="长沙"):
    """返回 (天气文本, 温度文本) 任一可失败为 None"""
    return fetch_weather_text(date, city), fetch_temp(date, lat, lon, biz_hours)


# 进程内记忆: 同城同日的多店共用一次请求(长沙 15 家店 × 同一天 → 1 次抓取, 不是 15 次)。
# 键带 city/坐标/biz_hours: 湘潭与长沙、不同营业时段不会互相串。
_CACHE = {}
_CACHE_LOCK = threading.Lock()


def fetch_cached(date, lat, lon, biz_hours=None, city=DEFAULT_CITY):
    """fetch 的记忆版(线程安全)。失败结果也缓存 —— 同一城市同一日不重复打接口。"""
    key = (str(date), round(float(lat), 4), round(float(lon), 4), biz_hours or "", city or "")
    with _CACHE_LOCK:
        if key in _CACHE:
            return _CACHE[key]
    val = fetch(date, lat, lon, biz_hours, city)
    with _CACHE_LOCK:
        _CACHE[key] = val
    return val


def clear_cache():
    """清记忆(测试用; 也在每次任务开始时调, 避免跨任务拿到旧值)。"""
    with _CACHE_LOCK:
        _CACHE.clear()


def fetch_map(sheet_addr, dates, biz_hours=None, max_workers=4, label="", warn_missing=True):
    """**逐店 × 逐日**取天气/温度 → ({Sheet 名: {日期: (天气, 温度)}}, [warn 文本])。

    sheet_addr: {Sheet 名: 门店地址 或 None}; biz_hours: 统一 str, 或 {Sheet 名: str},
    或 {(Sheet 名, 日期): str}(美团/淘宝的营业时段是逐店逐日取自报表的)。
    地址缺失/认不出城市 → 回退默认长沙坐标 + 一条 warn(**写明是哪家店**), 不猜城市;
    warn_missing=False 用于"整张表都没有地址列"的老配置(那不是错, 只是旧口径)。
    并发 4: 20+ 店 × 多日若串行会拖成几分钟; 同城同日靠 fetch_cached 去重, 实际请求数是
    「城市数 × 日数」。单店异常只记 warn, 不影响其它店(也不影响主流程)。
    """
    from concurrent.futures import ThreadPoolExecutor

    warns = []
    hours_of = biz_hours if isinstance(biz_hours, dict) else {}

    def _one(item):
        sheet, addr = item
        if warn_missing and not str(addr or "").strip():
            warns.append(f"{label}「{sheet}」缺门店地址 → 天气按默认长沙取(请在《门店明细》补「门店地址」)")
        elif str(addr or "").strip() and city_of_address(addr) is None:
            warns.append(f"{label}「{sheet}」地址「{str(addr)[:24]}」认不出城市 → 天气按默认长沙取")
        city, lat, lon = coords_for(addr)
        per_day = {}
        for d in dates:
            biz = hours_of.get((sheet, d)) or hours_of.get(sheet) \
                or (biz_hours if isinstance(biz_hours, str) else None)
            try:
                per_day[d] = fetch_cached(d, lat, lon, biz, city)
            except Exception as e:      # 单店单日失败: 留空 + warn, 不影响别人
                per_day[d] = (None, None)
                warns.append(f"{label}「{sheet}」{d} 天气取数失败: {str(e)[:60]}")
        return sheet, per_day

    if not sheet_addr:
        return {}, warns
    out = {}
    try:
        with ThreadPoolExecutor(max_workers=max(1, int(max_workers))) as ex:
            for sheet, per_day in ex.map(_one, sorted(sheet_addr.items())):
                out[sheet] = per_day
    except Exception as e:
        warns.append(f"天气并发取数异常({str(e)[:60]}) → 改串行重试")
        out = {s: _one((s, a))[1] for s, a in sheet_addr.items()}
    return out, warns
