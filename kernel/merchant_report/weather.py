# -*- coding: utf-8 -*-
"""天气: 天气现象=2345历史API(国内站点口径), 温度=Open-Meteo逐小时按营业时段窗口统计。
移植自已验证实现; 坐标/城市 areaId 可配。"""
import html as _html
import re
import urllib.request

WMO = {0: "晴", 1: "晴", 2: "多云", 3: "阴", 45: "雾", 48: "雾",
       51: "毛毛雨", 53: "毛毛雨", 55: "毛毛雨", 61: "小雨", 63: "中雨", 65: "大雨",
       71: "小雪", 73: "中雪", 75: "大雪", 80: "阵雨", 81: "阵雨", 82: "强阵雨",
       95: "雷阵雨"}
# 2345 城市areaId(历史页); 默认长沙, 客户配置可覆盖
AREA_ID = {"长沙": "59488"}


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
