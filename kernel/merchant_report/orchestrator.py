# -*- coding: utf-8 -*-
"""任务编排器: 登录 → 按区间下载报表 → 流量/推广抓取(区间自定义模式) → 天气 → 逐日填表 → (可选)LLM解读。
全程 NDJSON 事件流(协议 v1)。区间 [date_from, date] 内每天一行数据; 填表 top 模式最新日期在第一行。"""
import os
import re
import time
from datetime import datetime, timedelta
from pathlib import Path

from .config import app_data_dir, load_config
from .diagnose import root_cause
from .fill import (SHEET, broken_report_files, cross_checks, dates_span_text, default_sheet_name,
                   downloaded_but_unusable, fill_many, main_fallback_note, resolve_main,
                   fill_template, find_report_files, load_report_rows,
                   load_report_rows_by_store, report_file_candidates, unusable_report_hint)
from . import weather as weather_mod
from .platforms.taobao import (TaobaoAdapter, browser_death_forensics, launch_browser,
                               wait_browser_ready, watch_browser)
from .platforms.meituan import MeituanAdapter
from .platforms.jd import JDAdapter
from .platforms.meituan_gj import MeituanGjAdapter

STEPS = ["login", "download", "capture", "weather", "fill", "llm"]


_PLAT_LABEL = {"taobao": "淘宝闪购", "meituan": "美团外卖", "meituan_gj": "美团管家", "jd": "京东"}


def _plat_label(platform):
    """平台内部名 → 面板展示用中文名"""
    return _PLAT_LABEL.get(platform, platform)


def _sheet_rules(cfg):
    """门店名→Sheet名 规则(可在 config 的 defaults.sheet_name_rules 覆盖, 便于后续细化)。"""
    r = ((cfg.get("defaults") or {}).get("sheet_name_rules") or {})
    return {"city_prefixes": r.get("city_prefixes"), "noise_words": r.get("noise_words"),
            "aliases": r.get("aliases") or {}}


def _store_names_from_rows(rows_by_date, dates, key="门店名称"):
    """从"按日报表行"里收集门店名。

    ⚠️ 单行文件/单店账号下 `rows_by_date[d]` 是 **dict 而不是 list** —— 直接 for 会退化成
    遍历字典的 key(门店名收集为空 → 一个门店 Sheet 都不建且不报错)。此处统一归一化。
    """
    names = set()
    for d in dates:
        rows = rows_by_date.get(d) or []
        rows = rows if isinstance(rows, list) else [rows]
        for r in rows:
            nm = str(r.get(key) or "").strip() if isinstance(r, dict) else ""
            if nm:
                names.add(nm)
    return names


def _detail_maps(store, platform):
    """门店明细表(设置上传) → (名称映射, 编号映射)。未配置/读失败 → ({}, {})。

    门店明细是用户维护的权威配置(编号优先匹配); 模板里的映射表保留兜底并存,
    同一后端名在两边都命中时以门店明细为准(更新更及时)。
    """
    p = str(store.get("store_details") or "").strip()
    if not p or not platform:
        return {}, {}
    try:
        from . import store_details
        d = (store_details.load_store_details(p) or {}).get(platform) or {}
        return (d.get("by_name") or {}), (d.get("by_id") or {})
    except Exception:
        return {}, {}


def _merge_maps(smap, extra):
    """模板映射表 ∪ 门店明细名称映射(明细优先)。"""
    return {**(smap or {}), **(extra or {})}


def _strict_maps(store, platform):
    """需求 A2 严格模式的门店明细映射 → (by_name, by_id); 不满足严格模式 → (None, None)。

    与 _detail_maps 的区别: 那个读不到就返回 {}(旧行为: 照旧按名称/关键词猜); 这个刻意用
    None 区分"没配置"和"配了但读不出" —— 各支路据此决定是否进入**门店明细严格模式**。
    严格模式条件(缺一不可): ① 配置了门店明细 ② 表能读 ③ **该平台在明细里有门店数据**。
    ③ 是关键: 真实样例表只有 淘宝/美团/管家(没有京东) → 跑京东时保持旧行为, 不因为
    "明细里没这个平台"就把京东全部门店当成"不在门店明细中"而跳过。
    """
    p = str(store.get("store_details") or "").strip()
    if not p or not platform:
        return None, None
    try:
        from . import store_details as _sd
        d = (_sd.load_store_details(p) or {}).get(platform) or {}
    except Exception:
        return None, None
    by_name, by_id = d.get("by_name") or {}, d.get("by_id") or {}
    if not by_name and not by_id:
        return None, None
    return by_name, by_id


def _strict_on(store, platform):
    """该平台本次是否走门店明细严格模式(见 _strict_maps)。"""
    return _strict_maps(store, platform)[0] is not None


def _switch_id_map(store, platform):
    """门店明细 → {规范化店名: 门店唯一编号} —— 切店「按编号搜索直选」快路径用。

    2026-10-13: 淘宝/管家切店弹层可**直接搜编号**(shop_id / 12 位机构编码)把列表过滤到
    唯一一行, 省掉"展开全部运营组织逐个找"的最大耗时点。适配器要的是「店名→编号」,
    门店明细存的是「编号→Sheet」+「名称→Sheet」两张表, 不能按 Sheet 反查(见 store_details
    里 by_name_id 的注释: 同 Sheet 多编号会张冠李戴)。故这里直接用明细的 by_name_id。
    未配置/读失败/该平台无数据 → {}(适配器回退旧"按名"路径, 行为不变)。
    """
    p = str(store.get("store_details") or "").strip()
    if not p or not platform:
        return {}
    try:
        from . import store_details as _sd
        d = (_sd.load_store_details(p) or {}).get(platform) or {}
    except Exception:
        return {}
    return {k: str(v).strip() for k, v in (d.get("by_name_id") or {}).items() if str(v).strip()}


def _store_ids_from_rows(rows_by_date, dates, name_key="门店名称", id_key="门店编号"):
    """逐店报表行 → {后台门店名: 门店编号} —— 门店明细的**编号锁定**要靠它。

    各平台列名不同(淘宝=门店编号/美团=门店id或报表 B/C 列), 由调用方传键名; 取不到编号的
    平台(京东)就不传 → 名称匹配兜底(用户口径 A3)。
    """
    out = {}
    for d in dates:
        rows = rows_by_date.get(d) or []
        rows = rows if isinstance(rows, list) else [rows]
        for r in rows:
            if not isinstance(r, dict):
                continue
            nm = str(r.get(name_key) or "").strip()
            vid = str(r.get(id_key) or "").strip().split(".")[0]
            if nm and vid:
                out.setdefault(nm, vid)
    return out


# ---- 下载类报表(京东)门店行定位: 名称标准化 + 编号优先匹配(规格书 铁律 11 / §4.8) ----
# 坑: 门店名含间隔号「·」(示例·柠檬茶(长沙高桥店)), **漏掉「·」一行都匹配不到**。
# 标准化: 去空格、全角括号 （）→ 半角 ()、**保留·**(sheet_match.norm 会把 · 一起剥掉, 不能直接套)。
_JD_NAME_BRACKETS = str.maketrans({"（": "(", "）": ")", "［": "[", "］": "]", "　": ""})


def _norm_store_name(s):
    """门店名标准化(用于逐店行匹配): 去所有空白 + 全角括号→半角, **保留间隔号·**。"""
    return re.sub(r"[\s]+", "", str(s or "").translate(_JD_NAME_BRACKETS))


def _match_store_row(rows, name, store_id=None):
    """在"某天的逐店行"里定位目标门店行 → (row|None, 说明)。

    优先级(规格书 铁律 11): ① 门店id 精确 ② 名称标准化后精确 ③ 都拿不到 → (None, 原因),
    由调用方**显式告警**(不再静默 continue)。同归一化名多行 → (None, 多候选原因) 不猜。
    """
    rows = [r for r in (rows or []) if isinstance(r, dict)]
    sid = str(store_id or "").strip().split(".")[0]
    if sid:
        hit = [r for r in rows if str(r.get("门店id") or "").strip().split(".")[0] == sid]
        if len(hit) == 1:
            return hit[0], f"编号锁定(门店id={sid})"
        if len(hit) > 1:
            return None, f"门店id={sid} 命中 {len(hit)} 行(不猜)"
    key = _norm_store_name(name)
    if not key:
        return None, "门店名为空"
    hit = [r for r in rows if _norm_store_name(r.get("门店名称")) == key]
    if len(hit) == 1:
        return hit[0], "名称标准化后精确匹配"
    if len(hit) > 1:
        return None, f"名称「{name}」归一化后命中 {len(hit)} 行(不猜)"
    return None, ("报表里没有这个门店行(门店名/编号都对不上; 注意门店名含间隔号「·」且括号为半角)"
                  if not sid else "报表里没有这个门店行(门店id 与名称都对不上)")


def _details_scope(sink, store, platform, names, ids_by_name=None, label="门店"):
    """需求 A2 严格模式: 把「后台门店名集合」按《门店明细》裁剪。

    返回 dict(strict, names, by_name, by_id, skipped):
      · strict=False(未配置明细/读不出/该平台明细里没数据) → names 原样, 各支路行为与旧版一致;
      · strict=True → 只保留能经明细映射到的后台门店(编号优先、名称兜底, 与 plan_matches 同纪律),
        其余**不写入也不报错**, 逐条日志"不在门店明细中 → 跳过"。
    单独成函数是为了让 A2 的三条规则(范围/不建表/管家过滤)共用同一套判定, 也好单测。
    """
    from . import sheet_match
    by_name, by_id = _strict_maps(store, platform)
    if by_name is None:
        return {"strict": False, "names": sorted(names), "by_name": {}, "by_id": {}, "skipped": []}
    keep, skipped = [], []
    for nm in sorted(names):
        sid = str((ids_by_name or {}).get(nm) or "").strip().split(".")[0]
        if by_id and sid and sid in by_id:
            keep.append(nm)
            continue
        if sheet_match.lookup_store_map(by_name, nm):      # 双向包含, 与映射表同口径
            keep.append(nm)
            continue
        skipped.append(nm)
    if skipped:
        for nm in skipped:
            sink.log(f"  ⏭ {label}「{nm}」不在门店明细中 → 跳过（不写入也不报错）", level="warn")
        sink.log(f"🧾 门店明细严格模式: 保留 {len(keep)} 家, 跳过不在明细中的 {len(skipped)} 家", level="warn")
    return {"strict": True, "names": keep, "by_name": by_name, "by_id": by_id, "skipped": skipped}


def _create_missing_for(store, platform):
    """严格模式下**不自动新建 Sheet**(门店明细是权威: 明细里的店模板里没有对应 Sheet →
    跳过 + 日志提示补模板/改明细; 而不是复刻样板表建一张新 Sheet)。"""
    return not _strict_on(store, platform)


def _apply_strict_plan(sink, plan, sc, label="门店"):
    """严格模式: 把 plan 里"不在门店明细中"的后台门店从 matched/unmatched 移出。

    为什么还要一层: 各支路的门店名单来源不同(管家=适配器已按明细过滤; 淘宝/京东=报表里的
    后台门店名), 且 plan_matches 还会用**模板里的门店名映射表**兜底 —— 那张表可能混入门店
    明细之外的后台名。这里统一兜底, 保证"只写门店明细里的店"(用户口径 A2-1)。
    """
    if not sc or not sc.get("strict") or not plan:
        return []
    allowed = set(sc.get("names") or [])
    dropped = []
    for key in ("matched", "unmatched"):
        kept = []
        for tup in plan.get(key) or []:
            if tup and tup[0] in allowed:
                kept.append(tup)
            else:
                dropped.append(tup[0] if tup else "")
        plan[key] = kept
    for nm in dropped:
        sink.log(f"  ⏭ {label}「{nm}」不在门店明细中 → 跳过（不写入也不报错）", level="warn")
    if dropped:
        sink.log(f"🧾 门店明细严格模式: 从写入计划中再剔除 {len(dropped)} 家不在明细中的门店", level="warn")
    plan.setdefault("strict_skipped", [])
    plan["strict_skipped"].extend(dropped)
    plan["strict"] = True
    return dropped


def _plan_scope(plan):
    """从 plan 还原一个最小 sc(给 summary 用): 各支路在计划阶段已把跳过项记进 strict_skipped。"""
    return {"strict": bool((plan or {}).get("strict")), "skipped": []}


def _strict_summary(store_plan, sc, extra_names=()):
    """严格模式跳过的门店 → summary 文案(用户口径 A2-1: "summary 体现数量")。无跳过 → None。"""
    if not sc or not sc.get("strict"):
        return None
    names = list((store_plan or {}).get("strict_skipped") or [])
    names += [n for n in (sc.get("skipped") or []) if n not in names]
    names += [n for n in (extra_names or []) if n not in names]
    if not names:
        return None
    return (f"{len(names)} 家不在门店明细中(已跳过): " + "、".join(names[:6])
            + ("…" if len(names) > 6 else ""))


def _plan_store_sheets(sink, store, names, cfg, label="门店", ids_by_name=None):
    """后台门店名 → 模板 Sheet 计划(含"匹配不到就新建 Sheet")。

    一店一 Sheet 的公共实现, 供各平台支路复用(淘宝/京东...), 别再抄一遍。
    返回 plan 或 None; plan["matched"] = [(后台门店名, Sheet 名, 原因), ...]。
    未配置门店明细 → 行为与旧版完全一致; 配了(且该平台明细里有数据) → 门店明细**严格模式**:
    只保留能映射到的后台门店, 不再自动新建 Sheet(见 _details_scope / _create_missing_for)。
    """
    if not names:
        return None
    try:
        import openpyxl as _oxl

        from . import sheet_match
        _platform = store.get("platform") or ""
        _sc = _details_scope(sink, store, _platform, names, ids_by_name, label=label)
        if not _sc["names"]:
            sink.log(f"⚠️ 门店明细严格模式下没有可比对的{label}（明细里有数据的门店不在本次报表里）", level="warn")
            return None
        _wb = _oxl.load_workbook(store["template"], read_only=True)
        try:
            _sheets = list(_wb.sheetnames)
        finally:
            _wb.close()
        _kw = ([store["keyword"]] if store.get("keyword") else []) + list(store.get("keywords") or [])
        _main = (cfg.get("defaults") or {}).get("main_sheets")
        # 「门店明细」(设置上传, 编号/名称锁定) ∪ 模板里的「门店名映射表」(后端门店名逐字) → 比关键词猜可靠
        _smap = sheet_match.load_store_map(store["template"], _platform)
        _extra, _idmap = _detail_maps(store, _platform)
        if _extra or _idmap:
            sink.log(f"🧾 门店明细已加载: {len(_extra)} 条名称 / {len(_idmap)} 条编号"
                     f"（{'编号优先锁定' if _idmap else '名称锁定'}）")
        _merged = _merge_maps(_smap, _extra)
        plan = (sheet_match.plan_matches(_sheets, _sc["names"], _kw, main=_main, store_map=_merged,
                                         id_map=_idmap, store_ids=ids_by_name or {})
                if _main else sheet_match.plan_matches(_sheets, _sc["names"], _kw, store_map=_merged,
                                                       id_map=_idmap, store_ids=ids_by_name or {}))
        # 严格模式的跳过项记进 plan → summary 里能体现数量(用户口径 A2-1)
        plan["strict"] = _sc["strict"]
        plan["strict_skipped"] = list(_sc["skipped"])
        for _nm, _sn, _why in _autocreate_store_sheets(sink, store["template"], plan,
                                                       _sheet_rules(cfg),
                                                       enabled=_autocreate_enabled(cfg) and not _sc["strict"],
                                                       detail_map=_extra, strict=_sc["strict"]):
            plan["matched"].append((_nm, _sn, _why))
        sink.log(f"🗂 模板{label}工作表: {'、'.join(plan['candidates']) or '(无)'}")
        for _nm, _sn, _why in plan["matched"]:
            sink.log(f"  🔗 {label}「{_nm}」→ Sheet「{_sn}」（{_why}）")
        for _nm, _why in plan["unmatched"]:
            sink.log(f"  ⏭ {label}「{_nm}」未写入：{_why}", level="warn")
        for _sn in plan["idle"]:
            sink.log(f"  ⏭ 模板 Sheet「{_sn}」在报表里没有对应门店，保持不写入", level="warn")
        return plan
    except Exception as e:
        sink.log(f"⚠️ {label} Sheet 计划失败(仅影响一店一Sheet): {str(e)[:80]}", level="warn")
        return None


def _autocreate_enabled(cfg):
    """`defaults.auto_create_store_sheet` 关掉时, 匹配不到的门店 Sheet 不新建(回到只写已有 Sheet)。"""
    return (cfg.get("defaults") or {}).get("auto_create_store_sheet", True) is not False


def _split_foreign_task_pids(others, my_ppid):
    """把"其它任务进程"拆成 (foreign, siblings) —— 供双开门判定是否**真的**另一个窗口。

    2026-10-15 真机误报: "一键跑所有平台"是**串行队列**, 壳(py_runner)对每个平台直接 spawn
    一个 `python -m merchant_report.task_main` 子进程; 前端收到本平台 result 事件后约 1.2s
    就启动下一个平台, 而**前一个平台的进程还在收尾**(关浏览器等)。于是后一个任务探活时总能
    看见前一个平台的**兄弟进程**(同一个 py_runner 父进程的直系子进程) —— 旧逻辑一律当"另一个
    窗口", 每切一个平台弹一次 120s 恐吓框, 用户点「结束本次」还会打断整个一键队列。

    判据: ppid == 本进程的父进程号(os.getppid()) → 同一父壳下 spawn 的兄弟, 属正常串行衔接。
    ppid 未知(-1, 探测退化) → 归入 foreign(保守: 宁可多问一句, 不可漏报真双开)。
    探测不可用(None)/空列表 → 返回 ([], []), 调用方按"无冲突"继续。

    纯函数, 便于单测。"""
    if not others:
        return [], []
    mine = my_ppid
    foreign, siblings = [], []
    for item in others:
        try:
            pid, ppid = int(item[0]), int(item[1])
        except Exception:
            pid, ppid = item, -1        # 容忍旧格式(裸 pid) → 当未知父进程处理
        (foreign if (ppid == -1 or ppid != mine) else siblings).append((pid, ppid))
    return foreign, siblings


def _other_task_gate(sink, human):
    """双开/旧版残留防护(2026-10-12 客户机实证): 两个「商家报表助手」客户端同时跑任务时,
    **共用同一个浏览器配置目录** —— 先启动的浏览器占住目录, 后启动方的新浏览器被顶死,
    表现就是"浏览器被反复关闭、重开也没用"的死循环。与其让用户一头雾水, 不如先讲清楚。

    2026-10-15: 只在**真正另一个客户端实例**(非本窗口同一队列的兄弟进程)时才弹框 ——
    见 _split_foreign_task_pids 的误报说明。全是兄弟时按正常串行衔接处理(不弹窗/不 warn)。

    返回 True=继续 / False=干净结束(用户选了"结束本次")。
    探测不可用/无响应 → 继续(保持自动化旧行为, 只 warn)。"""
    try:
        from .platforms.taobao import _other_live_task_pids
        others = _other_live_task_pids()
    except Exception:
        others = None
    if not others:
        return True
    try:
        mine = os.getppid()
    except Exception:
        mine = -1
    foreign, siblings = _split_foreign_task_pids(others, mine)
    if siblings:
        sibs = "、".join(str(p) for p, _ in siblings[:6])
        sink.log(f"ℹ️ 检测到同一窗口内其它任务进程(进程 {sibs})——属串行队列正常衔接"
                 f"(前一个任务收尾中), 已按继续处理")
    if not foreign:
        return True          # 全是兄弟 → 不打扰用户(旧版会在这里弹 120s 恐吓框)
    pids = "、".join(str(p) for p, _ in foreign[:6])
    sink.log(f"⚠️ 检测到另一个「商家报表助手」任务在运行(进程 {pids})——两个客户端同时跑会"
             f"共用浏览器配置、互相顶死(这是浏览器被反复关闭的常见原因)", level="warn")
    msg = (f"检测到**另一个窗口正在运行任务**（进程 {pids}）。\n"
           f"两个客户端共用同一个浏览器配置目录，同时跑任务会互相顶死，"
           f"表现就是浏览器被反复关闭、重开也没用。\n"
           f"建议：结束本次，只保留一个窗口后重跑；如果那是旧版本的残留窗口，"
           f"请把它完全退出（含右下角托盘里的图标）。")
    try:
        sink.need_human("confirm", msg,
                        choices=["结束本次（只留一个窗口后重跑）", "仍然继续试试"], timeout_s=120)
        resp = human.wait({"type": "need_human", "action": "confirm"}, timeout_s=120) or {}
        choice = str(resp.get("choice") or "").strip()
    except Exception:
        choice = ""
    if not choice:
        sink.log("⚠️ 双开冲突确认未收到响应（无人值守/超时）→ 按「继续」处理", level="warn")
        return True
    if "结束" in choice:
        return False
    return True


def _template_date_gate(sink, human, template, date, cfg):
    """需求 B: 模板里一张门店表都没有 {date} → 停下问用户「继续跑流程 / 结束流程」。

    位置: run_task 早期(模板可写性预检之后、登录/浏览器/下载之前) —— 不浪费采集时间。
    返回 True=继续(含"部分存在不触发"/"无门店表跳过"/"无人值守兜底继续"), False=用户选结束。

    为什么只在**一张都没有**时才问: 部分门店表有、部分没有是正常的(平台日报次日才出,
    各店 Sheet 未必同步建行) —— 那种情况填表时 resolve_row 会自然补行, 不该打扰用户。
    用户选「继续」也不需要另写新建逻辑: 填表阶段 resolve_row 本来就会建该日期的行。
    """
    try:
        from .fill import template_date_scan
    except Exception:
        return True
    try:
        n_sheets, n_hit = template_date_scan(
            template, date, main_sheets=(cfg.get("defaults") or {}).get("main_sheets"))
    except Exception as e:
        sink.log(f"⚠️ 模板日期检查失败(跳过, 不影响流程): {str(e)[:80]}", level="warn")
        return True
    if n_sheets == 0:
        sink.log(f"📅 模板日期检查: {date} —— 模板里没有可扫描的门店 Sheet(全新模板), 跳过检查")
        return True
    if n_hit:
        sink.log(f"📅 模板日期检查: {date} 存在于 {n_hit}/{n_sheets} 张门店表")
        return True
    sink.log(f"📅 模板日期检查: {date} 不存在于任何门店表（{n_sheets} 张）→ 已请求用户确认", level="warn")
    msg = (f"模板里没有 {date}。继续跑流程 = 填表时会自动新建这个日期的行并填写；"
           f"结束流程 = 本次不打开浏览器、不下载，直接停下。")
    sink.need_human("confirm", msg, choices=["继续跑流程", "结束流程"], timeout_s=600)
    resp = human.wait({"type": "need_human", "action": "confirm"}, timeout_s=600) or {}
    choice = str(resp.get("choice") or "").strip()
    if not choice:
        # 无人值守兜底: 没有交互通道(CLI/超时) → 保持自动化旧行为(继续), 只 warn
        sink.log("⚠️ 模板日期确认未收到响应（无人值守/超时）→ 按「继续跑流程」处理", level="warn")
        return True
    if "结束" in choice:
        return False
    return True


def _addr_by_sheet(store):
    """《门店明细》→ {Sheet 名: 门店地址}(跨平台汇总; 同一 Sheet 取首个非空)。

    未配置 / 老表没有「门店地址」列 → {}(调用方沿用旧口径: 整表一组天气, 不告警)。
    """
    p = str(store.get("store_details") or "").strip()
    if not p:
        return {}
    try:
        from . import store_details as _sd
        d = _sd.load_store_details(p) or {}
    except Exception:
        return {}
    out = {}
    for k, v in d.items():
        if isinstance(v, dict):
            for sn, addr in (v.get("addr_by_sheet") or {}).items():
                if str(addr or "").strip():
                    out.setdefault(str(sn), str(addr).strip())
    return out


def _weather_targets(store, extra=()):
    """本次要**逐店**取天气的 {Sheet 名: 门店地址}。

    「门店地址」列与「运营报表模板Sheet名」在同一数据行 → 明细里的 Sheet 名就是模板 Sheet 名。
    单店模式点名要写的表用 extra 带进来(没配地址 → 回退默认长沙)。
    **没有任何地址配置 → {}(整条链回退旧口径: 整表一组天气, 且不告警)。**
    """
    addr = _addr_by_sheet(store)
    if not addr:
        return {}
    out = dict(addr)
    for sn in extra:
        if sn:
            out.setdefault(str(sn), addr.get(str(sn)))
    return out


def _biz_hours_by_sheet(store, platform, rows_by_date, dates, name_key="门店名称", pick=None):
    """逐店逐日「营业时段」→ {(Sheet 名, 日期): 'HH:MM-HH:MM'}。

    为什么要按店: 温度是**按营业时段窗口**统计的(T01), 各家店的营业时间不同 → 用首店时段
    取到的温度放到别家店的表里就是错的。报表行名 → Sheet 名用「门店明细 ∪ 模板映射表」
    (与门店计划同一套映射); 解析不出的门店 **不猜**, 该店温度回落到全天口径(旧行为)。
    """
    if not rows_by_date:
        return {}
    try:
        from . import sheet_match
        _smap = sheet_match.load_store_map(store["template"], platform)
    except Exception:
        _smap = {}
    _by_name, _ = _detail_maps(store, platform)
    merged = {**(_smap or {}), **(_by_name or {})}
    out = {}
    for d in dates:
        rows = rows_by_date.get(d) or []
        rows = rows if isinstance(rows, list) else [rows]
        for r in rows:
            if not isinstance(r, dict):
                continue
            nm = str(r.get(name_key) or r.get("B") or "").strip()
            if not nm:
                continue
            sn = merged.get(sheet_match._tidy(nm))
            if not sn:
                continue
            biz = (pick or (lambda x: x.get(name_key))) (r)
            if biz:
                out.setdefault((sn, d), biz)
    return out


def _wx_of(wx_by_sheet, wmap, sheet, date):
    """该 Sheet 自己那天的 (天气, 温度)。

    逐店表里没有这张 Sheet(没配地址/新匹配到的表) → 回退主表那组(旧口径)。
    **绝不**拿别的店的天气顶替 —— 那正是本次要修的错。
    """
    per = (wx_by_sheet or {}).get(sheet)
    if per and per.get(date):
        return per[date]
    return (wmap or {}).get(date)


def _warn_no_store_weather(sink, sheet, targets, warned):
    """写入前提示: 这张门店表没能参与逐店取数(门店明细里没配地址) → 回退了主表那组。

    每个 Sheet 只提示一次(逐日逐店会调很多次)。整份明细都没配地址时不提示 ——
    那是老配置的旧口径, 不是本次引入的问题。
    """
    if not sink or not targets or sheet in (warned or ()) or sheet in targets:
        return
    warned.add(sheet)
    sink.log(f"⚠️ 门店「{sheet}」在《门店明细》里没有「门店地址」→ 该表天气/温度按主表口径"
             f"(默认长沙)填入, C/D 列建议人工核对", level="warn")


def _start_weather_prefetch(store, dates, holder, targets=None):
    """后台线程预取天气 —— 纯 HTTP, **不碰 Playwright**, 所以能与浏览器阶段并行。

    为什么只能搬天气: Playwright 的 sync API 不能在两个线程里同时操作同一个 context,
    「一边等下载一边抓页面」做不到; 但天气/文件准备这类非浏览器工作可以叠在浏览器时间里。
    targets(需求 T01) = {Sheet 名: 门店地址} → 线程里**逐店**取(同城同日自动去重);
    主表仍按账号默认坐标取一份(旧口径不变)。
    """
    import threading

    def _run():
        try:
            if targets:
                by_sheet, warns = weather_mod.fetch_map(targets, dates, label="门店")
                holder["by_sheet"] = by_sheet
                holder["warns"] = warns
            holder["map"] = {d: weather_mod.fetch_cached(d, store.get("lat", 28.187),
                                                         store.get("lon", 112.921))
                             for d in dates}
        except Exception as e:
            holder["err"] = str(e)[:80]

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    holder["thread"] = t
    return t


def _take_weather_map(holder, dates, store, sink=None):
    """取预取结果(最多等 90s); 没有/失败就原地串行抓一次。返回 {日期: (天气, 温度)}(主表口径)。"""
    t = holder.get("thread")
    if t is not None:
        t.join(timeout=90)
        m = holder.get("map")
        if isinstance(m, dict) and m:
            if sink:
                sink.log(f"🌤 天气(与浏览器阶段并行取得): {'、'.join(sorted(m))}")
            return m
        if sink and holder.get("err"):
            sink.log(f"⚠️ 天气预取失败({holder['err']})，改为串行抓取", level="warn")
    return {d: weather_mod.fetch_cached(d, store.get("lat", 28.187), store.get("lon", 112.921))
            for d in dates}


def _log_weather_warns(sink, warns, limit=8):
    """逐店天气的告警(缺地址/认不出城市/单店失败)挂到日志 —— 不许静默。"""
    if not sink:
        return
    for w in (warns or [])[:limit]:
        sink.log("⚠️ " + w, level="warn")
    if len(warns or []) > limit:
        sink.log(f"  …另有 {len(warns) - limit} 条逐店天气告警(见上同类)", level="warn")


def _take_weather_by_sheet(holder, dates, store, sink=None, targets=None, biz_hours=None):
    """逐店天气 {Sheet 名: {日期: (天气, 温度)}} —— 预取命中直接复用, 否则原地并发取一次。

    返回 (by_sheet, warns)。用假 sink 的路径都走这里(美团外卖/淘宝的天气在报表解析后才有
    营业时段, 所以那两条支路不走预取, 直接调本函数)。
    """
    t = holder.get("thread") if holder else None
    if t is not None:
        t.join(timeout=90)
        m = holder.get("by_sheet")
        if isinstance(m, dict) and m:
            _log_weather_warns(sink, holder.get("warns"))
            return m, list(holder.get("warns") or [])
    if not targets:
        return {}, []
    by_sheet, warns = weather_mod.fetch_map(targets, dates, biz_hours, label="门店")
    _log_weather_warns(sink, warns)
    return by_sheet, warns


def _single_store_sheet(store, cfg, template):
    """单门店模式该写哪个 Sheet。

    用户口径(2026-09-28): 单门店模式**更要**找关键词对应的门店表, **绝不能写主表**(Sheet2)。
    顺序: 关键词/门店名 → match_sheet(精确/关键词包含/反向/配置关键词) → 命中就用它;
    都没命中 → 按**地名关键字**新建一个(结构由 fill_many 复刻第 3 个 sheet)。
    """
    import openpyxl
    from . import sheet_match
    rules = _sheet_rules(cfg)
    kw = str(store.get("keyword") or "").strip()
    nm = str(store.get("name") or "").strip()
    try:
        wb = openpyxl.load_workbook(template, read_only=True)
        names = list(wb.sheetnames)
        wb.close()
    except Exception:
        names = []
    for probe in (kw, nm):
        if not probe or not names:
            continue
        try:
            hit = sheet_match.match_sheet(names, probe)[0]
        except Exception:
            hit = None
        if hit:
            return hit
    try:
        want = sheet_match.extract_place(kw or nm, **rules)
    except Exception:
        want = ""
    return want or kw or nm or "门店"


def _autocreate_store_sheets(sink, template, plan, rules, base_sheet=None, limit=60,
                             dry_run=True, enabled=True, detail_map=None, strict=False):
    """匹配不到 Sheet 的门店 → 按**地名关键字**决定要建哪张 Sheet → 并入 matched。

    用户口径: 没有对应 Sheet 就建一个(复刻前面的表头), 再写数据; Sheet 名只取地名关键字
    (如「长沙万家丽广场七楼店」→「万家丽广场七楼」)。返回 [(门店, Sheet, 原因)]。

    `dry_run=True`(默认) **只决定 Sheet 名, 不在磁盘上建表** —— 真正建表交给
    `fill.fill_many(..., create_missing=True)` 在批量写入时一次做完。原因: 逐店
    ensure_sheet() 是「每店一次读+存」(京东 22 店实测白多花约 150s)。

    需求 A2: `strict=True`(门店明细严格模式) 或 enabled=False 时**一律不新建** —— 明细里的
    门店若模板里没有对应 Sheet, 保持"未写入"并给出可操作日志(补模板 Sheet 或改门店明细),
    而不是复刻样板表建新表。detail_map 只用于日志里带上明细指向的 Sheet 名, 便于用户核对。
    """
    from . import sheet_match
    made = []
    used = {sheet_match.norm(s) for s in (plan.get("candidates") or [])}
    left = []
    if not enabled or strict:   # 关掉自动建表 / 门店明细严格模式 → 匹配不到的门店保持"未写入"
        if strict:
            for nm, why in (plan.get("unmatched") or []):
                _want = (sheet_match.lookup_store_map(detail_map, nm) if detail_map else None)
                sink.log(f"  ⏭ 门店明细严格模式: 「{nm}」模板里没有对应 Sheet"
                         + (f"（门店明细指向「{_want}」）" if _want else "")
                         + f" → 跳过，不新建 Sheet（请补模板 Sheet 或修改门店明细）｜原因: {why}",
                         level="warn")
        return made
    for nm, why in list(plan.get("unmatched") or []):
        if len(made) >= limit:
            left.append((nm, why))
            continue
        want = sheet_match.extract_place(nm, **rules)
        if not want:
            left.append((nm, why))
            continue
        key = sheet_match.norm(want)
        if key in used:
            made.append((nm, want, f"已有同名 Sheet「{want}」"))
            continue
        if dry_run:
            used.add(key)
            sink.log(f"  🆕 新建门店 Sheet「{want}」（复刻模板表头）")
            made.append((nm, want, f"新建 Sheet（地名关键字「{want}」）"))
            continue
        from .fill import ensure_sheet
        if ensure_sheet(template, want, base_sheet=base_sheet):
            sink.log(f"  🆕 新建门店 Sheet「{want}」（复刻模板表头）")
            used.add(key)
            made.append((nm, want, f"新建 Sheet（地名关键字「{want}」）"))
        else:
            left.append((nm, why))
    plan["unmatched"] = left
    return made


def _browser_dead_exhausted_note(relaunched):
    """浏览器被反复关闭、自愈重开仍失败时的收尾文案(不许把真实原因盖成"平台没生成报表")。

    2026-10-12 用户真机: 自动化浏览器被连续关闭 3 次(疑似双开旧实例互杀/被手动关闭),
    自动重开 2 次仍失败 → 自愈循环耗尽。旧的收尾报 E_NO_REPORT, 把用户引去怀疑平台/日期。
    """
    times = f"（已自动重开 {relaunched} 次仍失败）" if relaunched else ""
    return (f"自动化浏览器被连续关闭{times}。常见原因与处理: "
            f"①请不要手动关闭它弹出的浏览器窗口；"
            f"②确认电脑上没有另一个本客户端在同时运行（含最小化到托盘的旧窗口，全部退出后再跑）；"
            f"③安全软件（360/电脑管家等）可能拦截自动化浏览器，放行后重试。"
            f"处理好后点「生成日报」重跑即可，登录状态不受影响。")


def run_task(cfg, store, date=None, date_from=None, sink=None, human=None,
             skip_browser=False, llm_client=None, show_browser=False):
    from .events import EventSink, HumanBridge
    sink = sink or EventSink("task")
    human = human or HumanBridge()
    date = date or (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    date_from = date_from or date
    d0 = datetime.strptime(date_from, "%Y-%m-%d")
    d1 = datetime.strptime(date, "%Y-%m-%d")
    if d1 < d0:
        sink.result(False, error_code="E_BAD_RANGE", message="区间结束早于开始", retryable=False)
        return None
    dates = [(d0 + timedelta(days=i)).strftime("%Y-%m-%d") for i in range((d1 - d0).days + 1)]

    ddir = Path(cfg.get("defaults", {}).get("download_dir") or (app_data_dir() / "reports"))
    ddir.mkdir(parents=True, exist_ok=True)
    scope = store.get("scope", "all")
    sink.task_start(store["name"], f"{date_from}~{date}", [store.get("platform", "taobao")])

    # 双开/旧版残留防护(2026-10-12 客户机实证): 两个客户端同时跑会共用浏览器配置互相顶死 ——
    # 在启动浏览器之前先说清楚, 而不是让用户看到"浏览器被反复关闭"后再自己猜。
    if not _other_task_gate(sink, human):
        sink.result(False, error_code="E_OTHER_TASK_RUNNING",
                    message=("检测到另一个窗口正在运行任务，已按你的选择结束本次。"
                             "请只保留一个客户端窗口后重跑；旧版本残留窗口请连同托盘一起完全退出。"),
                    retryable=True)
        return None

    # 需求 B(2026-10-12): 模板日期预检 + 确认门 —— 必须在这里(登录/浏览器/下载之前),
    # 否则等采集跑完才发现"模板里没这个日期", 用户的时间全白花了。
    # 单店/全店、各平台一视同仁; 用区间终点 date 做检查(逐日填表最终要落的就是它)。
    if not _template_date_gate(sink, human, store.get("template"), date, cfg):
        sink.result(False, error_code="E_DATE_NOT_IN_TEMPLATE",
                    message=(f"模板里没有 {date}。已结束本次流程——你可以修复模板日期、"
                             f"或更换模板/日期后，再手动点「生成日报」重跑。"),
                    retryable=False)
        return None

    summary = {"商家": store["name"], "区间": f"{date_from}~{date}", "范围": "全部门店" if scope == "all" else f"门店#{store.get('id') or store.get('keyword')}"}
    outputs = []
    import time as _time
    t_start = _time.time()
    last_fail = {}          # sid → {error, code}: 供卡点面板给出准确原因(而非笼统"某步失败")

    def _err_code(e):
        from .diagnose import classify
        return classify(e)

    def timed(sid, name, fn):
        sink.step_start(sid, name)
        t0 = _time.time()
        try:
            r = fn()
            sink.step_ok(sid, name, elapsed_s=round(_time.time() - t0, 1))
            return r
        except Exception as e:
            code = _err_code(e)
            last_fail[sid] = {"error": str(e)[:300], "code": code,
                              "exc_type": type(e).__name__}   # 供卡点面板给出准确原因
            sink.step_fail(sid, name, str(e)[:200], code,
                           elapsed_s=round(_time.time() - t0, 1))
            if code == "E_STRUCTURAL":
                try:
                    from .diagnose import collect_diagnostic_pack
                    dp = collect_diagnostic_pack(
                        getattr(ad_holder["ad"], "page", None) if ad_holder["ad"] else None,
                        sid, str(e), str(ddir))
                    if dp:
                        sink.log(f"📦 诊断包已存: {dp} (页面结构可能已改版, 请发送该文件给支持)")
                except Exception:
                    pass
            return None

    def selfheal_retry(failed_step, error_text, scope=None):
        """失败处理: 卡点面板(分类人话+现场截图+动态选项) → LLM 白名单补丁(可选) → 按选择动作。
        返回 "retried" / "skip" / "relogin" / None(结束任务)。"""
        scope = scope or {}
        try:
            from .failure_ui import ask_failure
            from .selfheal import collect_failure_context, diagnose, apply_patch_hint
            page = ad_holder["ad"].page if ad_holder["ad"] else None
            ctx = collect_failure_context(page, error_text, failed_step,
                                          exc_type=scope.get("exc_type") or "")
            # 先确定性定位(异常类型/错误原文), 再决定是否请 LLM —— 避免"不管什么错都建议多等几秒"
            cause = scope.get("cause") or root_cause(error_text, scope.get("exc_type") or "")
            ctx["root_cause_id"] = cause.get("id", "unknown")
            diag = {}
            if cause.get("id") == "unknown" and llm_client and llm_client.configured:
                diag = diagnose(llm_client, ctx)
            elif cause.get("id") != "unknown":
                sink.log(f"🧭 已定位问题: {cause.get('title', '')}（{cause.get('id')}）")
            patch = diag.get("patch") or {}
            extra = {"root_cause": cause.get("id", "unknown")}
            if diag.get("diagnosis"):
                extra["ai_diagnosis"] = diag.get("diagnosis")
                extra["ai_suggestion"] = diag.get("patch_reason") or ""
            if diag.get("cause"):
                extra["ai_cause"] = diag.get("cause")
                extra["ai_confidence"] = diag.get("confidence", "")
            res = ask_failure(sink, human,
                              platform=scope.get("platform", ""),
                              store=scope.get("store", ""),
                              date=scope.get("date", ""),
                              step=scope.get("step_label") or failed_step,
                              error_text=error_text, page=page,
                              err_code=scope.get("err_code"),
                              exc_type=scope.get("exc_type") or "",
                              cause=cause,
                              allow_skip=bool(scope.get("allow_skip")),
                              allow_relogin=bool(scope.get("allow_relogin")),
                              extra=extra, attempt=int(scope.get("attempt", 0) or 0))
            act = res.get("action")
            if act == "retry":
                if patch:
                    apply_patch_hint(ad_holder["ad"], patch)
                    sink.log(f"🔧 已应用 AI 建议补丁: {patch}")
                return "retried"
            if act == "relogin":
                return "relogin"
            if act == "relaunch":
                return "relaunch"
            if act in ("skip", "diagnose"):
                return "skip"
            return None
        except Exception as e:
            sink.log(f"自愈流程异常(忽略): {str(e)[:100]}", level="warn")
            return "retried"

    # 1. 登录 + 下载 + 抓取(失败触发自愈弹窗, 最多2轮)
    reports = stats = None
    abort_salvage = False      # 照写模式: 「结束任务」/抓取缺失时, 已抓到的数据仍写入模板
    ad_holder = {"ad": None, "_stop": None}

    def _stop_browser():
        """浏览器阶段的释放。**必须等商圈 Top 采集之后**再调 —— 商圈采集要用活着的会话,
        否则报 `Event loop is closed! Is Playwright already stopped?`(2026-09-29 用户实测:
        TOP 列全空的真因就是这个, 不是采集逻辑本身坏)。幂等, 可重复调。"""
        fn = ad_holder.get("_stop")
        ad_holder["_stop"] = None
        if fn:
            try:
                fn()
            except Exception:
                pass
    platform = store.get("platform", "taobao")
    # 天气预取: 纯 HTTP 不碰浏览器 → 与整个浏览器阶段并行跑, 后面支路直接复用
    # (京东实测串行天气 13.3s)。淘宝/美团外卖不预取: 它们的天气要报表里的「营业时段」。
    # 需求 T01(2026-10-14): targets = 《门店明细》里配了「门店地址」的本模板门店 Sheet
    # → 线程里**逐店**取; 没配地址 → {}(旧口径: 整表一组天气, 不告警)。
    # 本次任务开始时清一次进程内天气记忆: 跨任务/跨天重跑不拿旧值(同日同城仍自动去重)。
    weather_mod.clear_cache()
    wx_targets = _weather_targets(store)
    wx_holder = {}
    if platform in ("jd", "meituan_gj"):
        _start_weather_prefetch(store, dates, wx_holder, targets=wx_targets)
        if wx_targets:
            sink.log(f"🌤 天气逐店取: {len(wx_targets)} 家门店(长沙/湘潭分别取; 地址见《门店明细》)")
    if not skip_browser:
        # 失败最多走 3 轮面板: 护栏是用户的选择(面板在重试 2 次后自动隐藏「重试」选项),
        # 不再因"未配置 LLM"只给 1 次机会——否则用户失去跳过/发诊断的机会
        max_heal = 3
        try:
            from playwright.sync_api import sync_playwright
            # 浏览器组件引导: Windows 安装包不打包 Chromium(251MB 会超 GitHub 100MB 限制),
            # 首次运行从国内镜像下载到用户数据目录; 失败不阻塞 → launch_browser 回退系统 Edge
            from .bootstrap import ensure_browser
            try:
                ok_browser, info = ensure_browser(sink)
                if not ok_browser:
                    sink.log("↩️ 本次将使用系统 Edge/Chrome 运行（功能相同）", level="warn")
            except Exception as e:
                sink.log(f"⚠️ 浏览器引导异常(忽略): {str(e)[:80]}", level="warn")
            try:
                from .browser_pref import note as _bp_note
                _n = _bp_note()
                if _n:
                    sink.log(f"🔎 浏览器选择记忆: {_n}")
            except Exception:
                pass
            pw = sync_playwright().start()
            profile_path = Path(cfg.get("browser_profile", app_data_dir() / "browser-profile"))
            # 后台模式(2026-10-13): None=按平台默认(Windows 后台) —— 由 launch_browser 解析
            _bg_pref = (cfg.get("defaults") or {}).get("browser_background")

            def build_adapter(actx):
                """建适配器并完成登录。返回 (adapter, ok)。首次启动与重开浏览器都走这里。"""
                # 切店「搜索编号直选」快路径的接线(2026-10-13): 店名→门店唯一编号, 注入适配器。
                # 拿不到就是 {} → 适配器回退旧"按名"路径(行为不变)。
                _sid_map = _switch_id_map(store, platform)
                if platform in ("meituan", "jd", "meituan_gj"):
                    extra = {}
                    if platform == "meituan_gj":
                        # 门店明细严格模式(需求 A2-3, 2026-10-12): **抓取循环只进门店明细里的店**,
                        # 其余连店都不进 —— 逐店切店+读数是管家最耗时的部分, 这是本需求省时间的关键。
                        # 过滤源 = 门店明细(名称映射的键 = 后端名; 编号映射的键 = 机构编码 12 位,
                        # 与门店选择页卡片读到的 sid 同口径) → allow_name + allow_id。
                        _by_name, _by_id = _strict_maps(store, "meituan_gj")
                        if _by_name is not None:
                            extra["allow_name"] = list(_by_name.keys())
                            extra["allow_id"] = list(_by_id.keys())
                            sink.log(f"🧾 门店明细严格模式（管家）: 只抓门店明细里的门店 → "
                                     f"{len(extra['allow_name'])} 条名称 / {len(extra['allow_id'])} 条编号")
                        # 旧口径: 关掉「自动建 Sheet」时按**模板 sheet 清单**过滤(未配置门店明细时的兜底),
                        # 但若开了自动建表 → 新店必须被抓到才能建表, 故不过滤。
                        elif (cfg.get("defaults") or {}).get("auto_create_store_sheet") is False:
                            try:
                                from . import sheet_match
                                import openpyxl as _o
                                _wb = _o.load_workbook(store.get("template"), read_only=True)
                                try:
                                    extra["want"] = sheet_match.store_sheets(list(_wb.sheetnames))
                                finally:
                                    _wb.close()
                                if extra["want"]:
                                    sink.log("🗂 模板门店工作表: " + "、".join(extra["want"]))
                            except Exception as e:
                                sink.log(f"⚠️ 读取模板门店工作表失败(将抓全部门店): {str(e)[:70]}", level="warn")
                                extra = {}
                        else:
                            sink.log("ℹ️ 已开启「匹配不到就新建 Sheet」→ 抓取全部门店(不按模板过滤)")
                    a = {"meituan": MeituanAdapter, "jd": JDAdapter,
                         "meituan_gj": MeituanGjAdapter}[platform](
                        actx, sink, human, store_id=store.get("id", ""), keyword=store.get("keyword", ""),
                        **extra)
                    if platform == "meituan_gj" and _sid_map:
                        a.id_by_name = _sid_map      # 切店按机构编码搜索直选用
                        sink.log(f"🔢 切店编号映射已接线(管家): {len(_sid_map)} 家门店可用编码搜索直选")
                    elif platform == "meituan_gj":
                        sink.log("ℹ️ 切店编号映射为空(管家): 门店明细未配置或无编号列 → "
                                 "切店走「展开+按名」", level="warn")
                    cred = store.get("credentials") or {}
                    ok = a.ensure_login(username=cred.get("username", ""),
                                        password=cred.get("password", ""))
                    return a, ok
                a = TaobaoAdapter(actx, sink, human, store_id=store.get("id", ""),
                                  keyword=store.get("keyword", ""))
                if _sid_map:
                    a.id_by_name = _sid_map          # 切店按 shop_id 搜索直选用
                    sink.log(f"🔢 切店编号映射已接线(淘宝): {len(_sid_map)} 家门店可用编号搜索直选")
                else:
                    # 2026-10-13: 为空多半=门店明细未配置/编号列缺失/店名对不上 —— 说出来,
                    # 免得用户看"切店没走搜ID"时无从下手。
                    sink.log("ℹ️ 切店编号映射为空(淘宝): 门店明细未配置或无编号列 → 切店走「展开+按名」",
                             level="warn")
                if not a._logged_in():
                    plat_cfg = (cfg.get("platforms") or {}).get("taobao") or {}
                    u = plat_cfg.get("username") or ""
                    p_ = plat_cfg.get("password") or ""
                    if not (u and p_):
                        cred = store.get("credentials") or {}
                        u, p_ = cred.get("username", ""), cred.get("password", "")
                    from .autologin import auto_login
                    _login_ok = auto_login(a, u, p_, sink, human)
                    sink.browser_hide()      # 人工登录环节结束 → 浏览器窗口收回后台
                    if not _login_ok:
                        return a, False
                a._dismiss_popups()
                a._discover_shop_id()
                return a, True

            actx = launch_browser(pw, profile_path, sink=sink, background=_bg_pref)
            try:
                _live = actx.browser
                if _live is not None:
                    watch_browser(_live, sink)   # 断开即记日志: 定位"什么时候死的"
            except Exception:
                pass
            if show_browser:
                # 「展示过程」开关开着启动(需求 F): 浏览器一就绪就滑入主屏(带滑入动画)。
                # 只动窗口坐标/状态, 与取数逻辑零接触; request() 只改标志, poll() 在主线程执行。
                try:
                    from . import browser_window as _bw
                    _bw.request(True)
                    _bw.poll(sink)
                except Exception:
                    pass
            ad, ok = build_adapter(actx)
            ad_holder["ad"] = ad
            if not ok:
                sink.result(False, error_code="E_LOGIN_TIMEOUT",
                            message=f"{_plat_label(platform)} 登录超时，请检查账号配置或手动登录", retryable=True)
                return None
            sink.log(f"✅ {_plat_label(platform)} 登录成功")

            def relaunch_browser(reason=""):
                """确定性恢复: 浏览器被关闭/配置目录被占用 → 重开 context(profile 保留登录态) + 重建适配器。
                不丢登录态, 因此不需要用户重新登录。

                2026-10-12 加固(客户机"重开连死"实证): ①关旧后先等 0.8s;
                ②重开后**验证浏览器真的活着**, 没活就关掉再试一次 —— 此前"launch 没抛异常"
                就当成功, 把死浏览器交给后续步骤, 报出一串 'Target closed' 的假象。"""
                nonlocal actx, ad
                sink.log(f"🔄 自动化浏览器不可用（{reason}），正在重开并复用登录态…")
                try:
                    actx.close()
                except Exception:
                    pass
                time.sleep(0.8)          # 让旧浏览器完全退出(Windows 收尾慢)
                launched = False
                for attempt in (1, 2):
                    try:
                        actx = launch_browser(pw, profile_path, sink=sink, background=_bg_pref)
                    except Exception as e:
                        sink.log(f"⚠️ 浏览器重开失败(第{attempt}次): {str(e)[:120]}", level="warn")
                        time.sleep(2)
                        continue
                    try:
                        _live = actx.browser
                        if _live is not None:
                            watch_browser(_live, sink)
                    except Exception:
                        pass
                    if wait_browser_ready(actx):
                        launched = True
                        break
                    sink.log(f"⚠️ 重开的浏览器没有活起来(第{attempt}次), 关闭后重试", level="warn")
                    try:
                        actx.close()
                    except Exception:
                        pass
                    time.sleep(2)
                if not launched:
                    return False
                a2, ok2 = build_adapter(actx)
                ad = a2
                ad_holder["ad"] = a2
                if ok2:
                    sink.log("✅ 浏览器已重开，登录态已复用，继续之前的步骤")
                else:
                    sink.log("⚠️ 浏览器已重开，但登录态需人工确认", level="warn")
                return ok2

            def do_download():
                return ad_holder["ad"].download_reports(str(ddir), date_from=date_from, date_to=date)

            caps = ad_holder["per_store_caps"] = {}   # 共享给外层填表段(嵌套函数作用域传不出去)

            def do_capture():
                st = ad_holder["ad"].capture_stats(date_from=date_from, date_to=date)
                # 逐店抓取(2026-10-01 用户口径: 只抓营业中的门店, 每家店单独取数)
                # ⚠️ 必须放在**抓取步骤内部** —— 放到"填表"阶段时浏览器已被关掉
                #    (真机日志: Event loop is closed! Is Playwright already stopped?)。
                try:
                    # 只有实现了 list_stores/capture_per_store 的平台才走这条路(目前=淘宝)。
                    # 美团/管家/京东各有自己的逐店机制(如美团的 wmPoiId cookie + capture_circle),
                    # 2026-10-01 真机: 不加这个判断会在美团上抛
                    #   'MeituanAdapter' object has no attribute 'list_stores'
                    _adc = ad_holder["ad"]
                    _perstore_ok = hasattr(_adc, "list_stores") and hasattr(_adc, "capture_per_store")
                    if not _perstore_ok:
                        sink.log(f"ℹ️ {_plat_label(platform)} 逐店抓取走本平台自己的机制(无 list_stores 接口), 本次跳过通用逐店")
                    elif len(dates) == 1:
                        _stores = _adc.list_stores() or []
                        _open_stores = [s for s in _stores if s.get("open")]
                        _rest = [s for s in _stores if not s.get("open")]
                        if _stores:
                            # 补门店名(2026-10-15 任务 J): 只加日志, **不改跳过行为** ——
                            # 长房东郡/兰亭这类"平台标非营业中"的店属设计跳过, 但事后对账
                            # ("这家为什么缺淘宝对比格")时, 光看个数对不出来。
                            _rest_names = "、".join(str(s.get("name") or "?") for s in _rest[:12])
                            sink.log(f"🏬 门店清单 {len(_stores)} 家: 营业中 {len(_open_stores)} 家 → 只抓这些; "
                                     f"跳过 {len(_rest)} 家(非营业中)"
                                     + (f": {_rest_names}{'…' if len(_rest) > 12 else ''}" if _rest else ""))
                        if _open_stores:
                            sink.log(f"🛰 逐店抓取开始: {len(_open_stores)} 家(外卖本店列 + 外卖/爆品团的"
                                     f"「商圈同行前10%均值」基准列, 预计每家 20~30 秒)")
                            caps.clear()
                            caps.update(ad_holder["ad"].capture_per_store(_open_stores, date) or {})
                            sink.log(f"🛰 逐店抓取完成: {len(caps)}/{len(_open_stores)} 家 "
                                     f"(商圈TOP {sum(1 for v in caps.values() if v.get('bench_ok'))} 家)")
                            # 抓完复位回账号级, 免把下次运行/下载页带进单店上下文
                            ad_holder["ad"].reset_shop_context()
                    elif len(dates) > 1:
                        sink.log("ℹ️ 区间模式: 逐店商圈TOP 本次不抓(逐店×每天成本过高) → 那几列留空; "
                                 "要 TOP 请选单日/昨日", level="warn")
                except Exception as e:
                    sink.log(f"⚠️ 逐店抓取失败: {str(e)[:90]} → 商圈TOP列留空(报表口径照写)", level="warn")
                return st

            reports = timed("download", "下载报表", do_download)
            stats = timed("capture", "网页抓取", do_capture)
            user_skipped = False
            relaunched = 0
            _diag_written = False     # 首次浏览器死亡时写一份现场取证(每任务一次)
            for heal in range(max_heal):
                failed = "download" if not reports else ("capture" if not stats else None)
                if not failed:
                    break
                step_label = "下载报表" if failed == "download" else "网页抓取"
                lf = last_fail.get(failed) or {}
                err_text = lf.get("error") or f"{step_label}没有拿到数据"
                cause = root_cause(err_text, lf.get("exc_type") or "")
                did_recover = False
                # 确定性自动恢复: 浏览器被关掉/配置目录被占用 → 直接重开并重试(登录态复用), 不打扰用户
                if cause.get("auto_recover") and relaunched < 2:
                    relaunched += 1
                    # 现场取证(每任务只写一次): 进程表+崩溃事件 → 文件; 用于区分
                    # "被外部结束"还是"自身崩溃", 客户机连续死亡排查的关键证据(2026-10-12)
                    if not _diag_written:
                        _diag_written = True
                        try:
                            _diag = browser_death_forensics(profile_path, ddir, err_text)
                            if _diag:
                                sink.log(f"🧪 浏览器死亡现场已记录: {_diag}"
                                         f"（若反复出现，请把该文件连同界面日志发给支持）")
                        except Exception:
                            pass
                    did_recover = relaunch_browser(cause.get("title", ""))
                act = "retried" if did_recover else selfheal_retry(
                    failed, err_text,
                    scope={"platform": _plat_label(platform),
                           "store": store.get("name", ""),
                           "date": (f"{date_from}~{date}" if date_from != date else date),
                           "step_label": step_label,
                           "err_code": lf.get("code"), "exc_type": lf.get("exc_type") or "",
                           "cause": cause,
                           "allow_skip": True, "allow_relogin": True,
                           "attempt": heal})
                if act is None:
                    # 2026-10-09 用户口径(照写): 「结束任务」不再丢弃已拿到的东西 ——
                    # 但只有**报表确实已下载**(reports 非空)才值得照写; 什么都没抓到就是普通结束。
                    if reports:
                        sink.log("⛔ 已按你的选择结束任务 — 此前已抓到的部分照常写入模板"
                                 "(缺失留空, 重跑覆写)", level="warn")
                        abort_salvage = True
                        break
                    sink.result(False, error_code="E_USER_ABORT", message="你选择了结束任务", retryable=True)
                    return None
                if act == "skip":
                    sink.log(f"⏭️ 已按你的选择跳过「{step_label}」")
                    user_skipped = True
                    break
                if act == "relogin":
                    sink.log("🔐 按你的要求重新登录…")
                    try:
                        cred = store.get("credentials") or {}
                        ad_holder["ad"].ensure_login(username=cred.get("username", ""),
                                                     password=cred.get("password", ""))
                    except Exception as e:
                        sink.log(f"⚠️ 重新登录异常: {str(e)[:80]}", level="warn")
                if act == "relaunch" and not did_recover:
                    did_recover = relaunch_browser("你选择了重开浏览器")
                sink.log(f"🔁 {'重开浏览器后重试' if did_recover else f'自愈重试 {heal + 1}/{max_heal}'}: {step_label}")
                if failed == "download":
                    reports = timed("download", "下载报表", do_download)
                else:
                    stats = timed("capture", "网页抓取", do_capture)
            # persistent_context 由 Playwright 管理, 可安全关闭(保留 profile 登录态)。
            # 但**先不关**: 下面的商圈 Top 采集还要用这个会话 → 登记成延迟释放。
            def _defer_stop(_actx=actx, _pw=pw):
                try:
                    _actx.close()
                except Exception:
                    pass
                _pw.stop()
            ad_holder["_stop"] = _defer_stop
            # ⚠️ 成功路径上**一处都不能提前关浏览器**(2026-10-01 真机铁证, 修了 3 处才干净):
            #    美团/拼好饭的商圈 Top 采集在后面的「填表阶段」circle 块才跑, 那时还要用这个会话。
            #    旧代码把 3 个 _stop_browser() 写在各自 if 的**前面**(本意"失败就关", 实际无条件执行)
            #    → 商圈采集 18 家门店全部 `Event loop is closed! Is Playwright already stopped?`
            #    → 美团/拼好饭两组 TOP 列全空。淘宝不受影响: 它的逐店商圈在 do_capture 内部抓完。
            #    现在: 只在**真要 return** 的分支里关; 成功路径留给 circle 块之后那一次。
            if user_skipped and not reports:
                _stop_browser()
                sink.result(False, error_code="E_USER_SKIP",
                            message="已按你的选择跳过该步骤，本次任务未产出报表。", retryable=True)
                return None
            if not reports:
                _stop_browser()
                # 2026-10-12 真机: 浏览器被连关 3 次走到这里时, 原本报笼统的 E_NO_REPORT「平台未生成」
                # → 真实原因(浏览器被关/配置目录被占)被整个盖掉, 用户被引去怀疑平台或日期。
                # 现在: 最后一轮失败若是可自动恢复类(浏览器被关/配置目录被占) → 报真实原因 + 操作指引。
                _lf = last_fail.get("download") or {}
                _lc = root_cause(_lf.get("error") or "", _lf.get("exc_type") or "")
                if _lc.get("auto_recover"):
                    sink.result(False, error_code=_lc.get("code") or "E_BROWSER_DEAD",
                                message=_browser_dead_exhausted_note(relaunched), retryable=True)
                    return None
                sink.result(False, error_code="E_NO_REPORT",
                            message=(f"平台未生成 {date_from}~{date} 的报表。可能原因: ①报表生成中(稍后重试) "
                                     f"②区间内日期过早(平台只保留近6个月) ③登录态异常。详见上方步骤错误。"),
                            retryable=True)
                return None
            if not stats:
                # 2026-10-09 用户口径(照写): 网页抓取缺失/被跳过, 但报表文件已下载 → 不再整单作废;
                # 从报表目录重新解析已下载的报表 → 先写入已有部分(流量/推广类列留空), 重跑覆写。
                _salv_stats = None
                if platform == "meituan":
                    try:
                        from .platforms import meituan_mapping as _mt_salv
                        _sa, _sb, _sf = _mt_salv.collect(str(ddir), date_from, date)
                        if _sa:
                            _salv_stats = {"all": _sa, "bao": _sb}
                    except Exception as _e:
                        sink.log(f"⚠️ 报表目录复核失败: {str(_e)[:80]}", level="warn")
                if not _salv_stats:
                    _stop_browser()
                    sink.result(False, error_code="E_CAPTURE_FAIL",
                                message="流量/推广数据抓取失败, 报表未写入。可点重试。", retryable=True)
                    return None
                sink.log("⚠️ 网页抓取缺失, 但已下载的报表还在 → 照写模式: "
                         "只写入报表已有数据(流量/推广类列留空, 重跑覆写)", level="warn")
                stats = _salv_stats
        except Exception as e:
            _stop_browser()
            sink.result(False, error_code=_err_code(e),
                        message=f"浏览器自动化失败: {str(e)[:150]}", retryable=True)
            return None
    else:
        sink.step_skip("download", "下载报表", "skip_browser 模式(使用目录已有报表)")
        sink.step_skip("capture", "网页抓取", "skip_browser 模式")

    # 1.5 美团支路: 报表口径与淘宝不同(门店报表 CSV/gb18030, 83 列), 用独立映射填 E~DF 区
    # 口径来源: 脑图 运营报表.pdf 列位置级对应表, 已与真实报表逐列核对 → platforms/meituan_mapping.py
    if platform == "meituan":
        from .platforms import meituan_mapping as mt_map
        # 诊断容器清零(本次运行从零开始): 表头名对不上的列 + 数值校验告警, 收尾统一打印进 summary
        mt_map.NAME_FALLBACKS.clear()
        mt_map.VALIDATE_WARNINGS.clear()
        all_rows = (stats or {}).get("all") or {}
        bao_rows = (stats or {}).get("bao") or {}
        _files = []
        if not all_rows:      # skip_browser / 复用已有报表: 直接从下载目录解析
            all_rows, bao_rows, _files = mt_map.collect(str(ddir), date_from, date)
        if not all_rows:
            sink.result(False, error_code="E_NO_REPORT",
                        message=f"美团 {date_from}~{date} 报表无可用数据行(下载或解析失败)", retryable=True)
            return None
        missing = [d for d in dates if d not in all_rows]
        if missing:
            # 报错要能自证: 说清"报表里到底有哪几天 + 是哪份文件", 否则用户只会看到"缺这些日期"
            # 而不知道是平台没生成、还是**下载页日期没生效**下成了别的日期。
            _have = dates_span_text(sorted(all_rows))
            _ftxt = "、".join(os.path.basename(str(p)) for p in (_files or [])[:3]) or "未找到报表文件"
            if len(missing) == len(dates):
                sink.result(False, error_code="E_NO_REPORT",
                            message=(f"美团报表内缺少这些日期: {', '.join(missing)}；"
                                     f"本次报表里实际有: {_have}（文件: {_ftxt}）。"
                                     f"平台日报通常次日才可下载；也可把区间改为已有数据的日期。"
                                     f"若实际日期与请求区间不符 → 多半是下载页日期没生效, 请重跑一次并看日志里"
                                     f"「📅 日期区间 = …」那行。"), retryable=False)
                return None
            # 2026-10-09 用户口径(照写): 只缺部分日期时不再整单作废 —— 已有日期先写入,
            # 缺失的留空, 重跑直接覆写(平台日报次日才出, "昨天的先要"是常态)。
            sink.log(f"⚠️ 美团报表缺 {', '.join(missing)}（实际有: {_have}; 文件: {_ftxt}）"
                     f" → 照写模式: 先写入已有 {len(dates) - len(missing)} 天, 缺失留空, 重跑覆写", level="warn")
            dates = [d for d in dates if d in all_rows]
        # 报表里到底有几家门店, 必须当场报出来: 平台只导出"当前门店视角"时, 一店一 Sheet
        # 就只有那 1 家有数据(2026-09-30 真机: ok=true 但表里几乎没数据, 日志里毫无线索)。
        _rows_last = all_rows.get(dates[-1]) or []
        _n_mt = len(_rows_last if isinstance(_rows_last, list) else [_rows_last])
        sink.log(f"📄 美团报表内 {_n_mt} 家门店 × {len(dates)} 天")
        if _n_mt <= 1:
            sink.log("⚠️ 美团报表里只有 1 家门店 —— 平台很可能只导出了当前门店视角, "
                     "其余门店的 Sheet 本次不会有数据(请在平台切到全部门店后重跑)", level="warn")

        def pick_pair(d):
            """单店 → 按门店编号/名称匹配行; 全店 → 可加列求和(得分/率/时长类留空, 不臆造)"""
            a = all_rows.get(d) or []
            b = bao_rows.get(d) or []
            a = a if isinstance(a, list) else [a]
            b = b if isinstance(b, list) else [b]
            if scope != "store" and len(a) > 1:
                ar = mt_map.agg_row(a)
                br = mt_map.agg_row(b) if len(b) > 1 else (b[0] if b else None)
            else:
                ar = mt_map.pick_row(a, store.get("id", ""), store.get("keyword", ""))
                br = mt_map.pick_row(b, store.get("id", ""), store.get("keyword", "")) if b else None
            return ar, br

        discovered = None
        if scope == "all":
            rl = all_rows.get(dates[-1]) or []
            rl = rl if isinstance(rl, list) else [rl]
            if len(rl) > 1:
                discovered = [{"id": str(r.get("C", "")), "name": str(r.get("B", ""))}
                              for r in rl if r.get("B")]

        # 3. 天气(逐日; 营业时段取自美团报表对应?列 → biz_hours) —— 主表口径(账号默认坐标)
        def do_weather():
            out = {}
            for d in dates:
                rr = all_rows.get(d) or []
                rr = rr if isinstance(rr, list) else [rr]
                out[d] = weather_mod.fetch_cached(d, store.get("lat", 28.187), store.get("lon", 112.921),
                                                  mt_map.biz_hours(rr[0] if rr else {}))

            return out

        # 3.5 逐店天气(T01/铁律 9): 每店按**自己的门店地址**取(长沙/湘潭分别), 温度按该店
        # **当天**的营业时段窗口统计 —— 各家店的营业时间不同, 拿首店的时段算别家的温度是错的。
        # 未配地址 → _wx_targets 为空 → 整条链保持旧口径(整表一组), 不告警。
        # 单门店模式的目标表(要在天气之前定: 逐店取数要把它算进 targets)
        _one_sheet = _single_store_sheet(store, cfg, store["template"]) if scope == "store" else None
        _wx_targets = _weather_targets(store, extra=[_one_sheet] if _one_sheet else [])
        _biz_by = _biz_hours_by_sheet(store, "meituan", all_rows, dates, pick=mt_map.biz_hours)

        def do_weather_all():
            base = do_weather()
            if not _wx_targets:
                return base, {}
            by_sheet, warns = weather_mod.fetch_map(_wx_targets, dates, _biz_by, label="美团门店")
            _log_weather_warns(sink, warns)
            return base, by_sheet

        wmap, wx_by_sheet = timed("weather", "天气获取", do_weather_all) or ({}, {})
        for d in dates:
            if wmap.get(d) and wmap[d][0]:
                sink.log(f"[天气] {d} → {wmap[d][0]} {wmap[d][1]}")
        if wx_by_sheet:
            _cities = sorted({weather_mod.city_of_address(a) or "长沙" for a in _wx_targets.values()})
            sink.log(f"🌤 天气逐店取: {len(wx_by_sheet)} 家门店 × {len(dates)} 天"
                     f"（城市: {'、'.join(_cities)}）")

        # 4. 填表(区间逐日; top 模式从最旧到最新依次插入 → 最新日期在第一行)
        mode = cfg.get("defaults", {}).get("fill_mode", "auto")
        # 一店一 Sheet(多店模式; 与美团管家同口径): 模板 Sheet 名=地名关键词, 逐店匹配后各自写入。
        # 报表 E~DF 区本就逐店一行 → 按门店名匹配 Sheet; 匹配不到就跳过(不写、不报错、不建 Sheet)。
        store_plan = None
        # 商圈/推广采集结果容器(在 try 之外声明: 单店模式走不到下面 scope!="store" 的采集分支,
        # 但填表段要用它们 → 必须存在默认值)
        circle_by_sheet = {}
        promo_by_sheet = {}      # {Sheet: {日期: {consume, recharge, balance}}} 推广流水(CE/CJ)
        promo_ov_by_sheet = {}   # {Sheet: {expose, visit}} 推广概览(CF/CG, 仅昨日口径)
        promo_pri_by_day = {}    # {日期: {consume, recharge}} 主表用量(逐店合计 = 本店或全店合计)
        promo_pri_ov = None      # 主表 CF/CG 概览(单店=本店; 全店=账号级)
        # 需求 A2: 严格模式标志在 try 之外初始化 —— 下面的建表/兜底剔除分支要用到它, 而
        # "_names 为空 / plan 异常" 等路径不会走到 try 内部赋值(否则 UnboundLocalError)。
        _sc = {"strict": False, "names": [], "by_name": {}, "by_id": {}, "skipped": []}
        _extra = {}
        if scope != "store":
            _names = set()
            for d in dates:
                for r in (all_rows.get(d) or []):
                    if isinstance(r, dict) and str(r.get("B") or "").strip():
                        _names.add(str(r["B"]).strip())
            if _names:
                try:
                    import openpyxl as _oxl
                    from . import sheet_match
                    _wb = _oxl.load_workbook(store["template"], read_only=True)
                    try:
                        _sheets = list(_wb.sheetnames)
                    finally:
                        _wb.close()
                    _kw = ([store["keyword"]] if store.get("keyword") else []) + list(store.get("keywords") or [])
                    _main = cfg.get("defaults", {}).get("main_sheets")
                    _smap = sheet_match.load_store_map(store["template"], "meituan")
                    # 门店明细(设置上传): 编号优先锁定; 报表行 B=门店名称 / C=门店id(兼容表头名取值)
                    _extra, _idmap = _detail_maps(store, "meituan")
                    _ids = {}
                    if _idmap:
                        for d in dates:
                            for r in (all_rows.get(d) or []):
                                if not isinstance(r, dict):
                                    continue
                                nm = str(r.get("门店名称") or r.get("B") or "").strip()
                                vid = str(r.get("门店id") or r.get("C") or "").strip().split(".")[0]
                                if nm and vid:
                                    _ids.setdefault(nm, vid)
                    if _extra or _idmap:
                        sink.log(f"🧾 门店明细已加载: {len(_extra)} 条名称 / {len(_idmap)} 条编号"
                                 f"（本次报表识别到 {len(_ids)} 个编号）")
                    # 门店明细严格模式(A2-1): 报表里有、但不在门店明细中的后台门店 → 不写入不报错;
                    # 明细里有、报表里也有但模板没有对应 Sheet → 由下面的"不建表"分支给明确日志。
                    _sc = _details_scope(sink, store, "meituan", _names, _ids, label="门店")
                    _merged = _merge_maps(_smap, _extra)
                    store_plan = (sheet_match.plan_matches(_sheets, _sc["names"], _kw, main=_main,
                                                          store_map=_merged, id_map=_idmap, store_ids=_ids)
                                  if _main else sheet_match.plan_matches(_sheets, _sc["names"], _kw,
                                                                        store_map=_merged, id_map=_idmap,
                                                                        store_ids=_ids))
                    sink.log(f"🗂 模板门店工作表: {'、'.join(store_plan['candidates']) or '(无)'}")
                    for _nm, _sn, _why in store_plan["matched"]:
                        sink.log(f"  🔗 门店「{_nm}」→ Sheet「{_sn}」（{_why}）")
                    for _nm, _why in store_plan["unmatched"]:
                        sink.log(f"  ⏭ 门店「{_nm}」未写入：{_why}", level="warn")
                    # 单门店模式(报表里就 1~2 家店)时绝大多数 Sheet 必然没对应门店, 这是**正常**的,
                    # 逐张刷 18 行警告会把真正的问题淹掉(2026-09-29 用户误以为"关键词没对上")。
                    _idle = list(store_plan["idle"] or [])
                    if len(_idle) > 2:
                        sink.log(f"  ℹ️ 本次报表只匹配到 {len(store_plan['matched'])} 家门店 → "
                                 f"其余 {len(_idle)} 个模板 Sheet 本就不需要写入(正常, 非错误): "
                                 f"{'、'.join(_idle[:6])}{'…' if len(_idle) > 6 else ''}")
                    else:
                        for _sn in _idle:
                            sink.log(f"  ⏭ 模板 Sheet「{_sn}」在报表里没有对应门店，保持不写入")
                except Exception as e:
                    sink.log(f"⚠️ 门店 Sheet 计划失败(仅影响一店一Sheet): {str(e)[:80]}", level="warn")
                    store_plan = None
            if store_plan and store_plan.get("unmatched"):
                for _nm, _sn, _why in _autocreate_store_sheets(
                        sink, store["template"], store_plan, _sheet_rules(cfg),
                        enabled=_autocreate_enabled(cfg) and not _sc["strict"],
                        detail_map=_extra, strict=_sc["strict"]):
                    store_plan["matched"].append((_nm, _sn, _why))
                    sink.log(f"  🔗 门店「{_nm}」→ Sheet「{_sn}」（{_why}）")
            _apply_strict_plan(sink, store_plan, _sc, label="门店")
        # ---- 推广取数(CE/CJ 流水 + CF/CG 概览, 与商圈采集共用同一次浏览器会话) ----
        # 2026-10-14 (规格书 §4.4/T04): 推广是**独立数据域**, 不再挂在「商圈Top」开关上。
        #   · CE/CJ ← 对账页「推广费流水记录」按**自定义日期=目标日**过滤: CE =「推广订单扣款」
        #     绝对值合计; CJ =「推广账户自动充值」合计(capture_promo_ledger)。
        #   · CF/CG ← 推广概览页**切「昨日」**后读「总曝光量/总进店量」(capture_promo):
        #     只有目标日==昨天才有值, 历史日期按规格书铁律 6 填 0。
        # (容器已在上面声明, 这里只填内容 —— circle_by_sheet/promo_* 都按 Sheet+日期 存)
        _yday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        _ad = ad_holder.get("ad")
        _matched = list((store_plan or {}).get("matched") or [])
        _ledger_fn = getattr(_ad, "capture_promo_ledger", None) if _ad is not None else None
        _ov_fn = getattr(_ad, "capture_promo", None) if _ad is not None else None
        # 单门店模式: 没有「门店 Sheet 计划」, 但推广列同样要按本店取(主表行 = 本店)。
        #   本店编号优先级: 配置里的门店 id → 报表行(门店名称/关键词)匹配到的门店id。
        #   两者都拿不到 → 不猜, 走「报表里没有门店编号」告警(CE/CJ/CF/CG 留空)。
        if not _matched and scope == "store":
            _sid0 = str(store.get("id") or "").strip()
            if not _sid0:
                _rows_l = all_rows.get(dates[-1]) or []
                _rows_l = _rows_l if isinstance(_rows_l, list) else [_rows_l]
                _kw0 = str(store.get("keyword") or store.get("name") or "").strip()
                _hit0 = next((r for r in _rows_l if _kw0 and _kw0 in str(r.get("B") or "")), None)
                if _hit0 is None and len(_rows_l) == 1:
                    _hit0 = _rows_l[0]
                _sid0 = str((_hit0 or {}).get("C") or "").strip()
            if _sid0:
                _matched = [(str(store.get("name") or store.get("keyword") or "本店"), None, _sid0)]
        if _ad is not None and _matched and not abort_salvage:
            _rows_last = all_rows.get(dates[-1]) or []
            _rows_last = _rows_last if isinstance(_rows_last, list) else [_rows_last]
            _id_by_name = {str(r.get("B") or "").strip(): str(r.get("C") or "").strip()
                           for r in _rows_last if isinstance(r, dict) and r.get("B")}
            if callable(_ledger_fn):
                sink.step_start("promo", "采集推广",
                                f"{len(_matched)} 店 × {len(dates)} 天 × CE/CJ 流水"
                                + ("+ CF/CG 概览(昨日)" if dates[-1] == _yday else "(CF/CG: 非昨日→0)"))
                _promo_try = 0                     # 逐店逐日尝试数(任务 J·C5: 覆盖度要可核对)
                for _nm, _sn, _why in _matched:
                    _sid = _why if _sn is None else (_id_by_name.get(_nm) or "")
                    if not _sid:
                        sink.log(f"  · {_nm}: 报表里没有门店编号, 跳过推广抓取", level="warn")
                        continue
                    for _d in dates:
                        _promo_try += 1
                        try:
                            _pl = _ledger_fn(_sid, _d)
                            if not isinstance(_pl, dict):
                                _pl = None
                        except Exception as e:
                            sink.log(f"  · {_nm}: 推广流水异常 {str(e)[:70]}", level="warn")
                            _pl = None
                        if _pl and (_pl.get("consume") is not None
                                    or _pl.get("recharge") is not None):
                            _sink_sheet = _sn if _sn else "_本店"
                            promo_by_sheet.setdefault(_sink_sheet, {})[_d] = _pl
                        else:
                            # 单条失败**必须留痕**(不静默): 真机 17/17 全失败时, 光看
                            # "流水 0 店"分不出是没跑、还是跑了但每天都失败。
                            sink.log(f"  · {_nm} {_d}: 对账页未取到流水 → 该日 CE/CJ 留空", level="warn")
                    # CF/CG: 仅「目标日==昨天」时抓概览(切昨日+硬验收在 capture_promo 内)
                    if dates[-1] == _yday and callable(_ov_fn):
                        try:
                            _pv = _ov_fn(store_id=_sid)
                        except Exception as e:
                            sink.log(f"  · {_nm}: 推广概览异常 {str(e)[:70]}", level="warn")
                            _pv = None
                        if _pv:
                            promo_ov_by_sheet[_sn if _sn else "_本店"] = _pv
                # 主表用量: 各店逐日求和(单店模式=本店; 全部门店模式=该账号下已匹配各店合计)
                for _byb in promo_by_sheet.values():
                    for _d, _v in (_byb or {}).items():
                        _acc = promo_pri_by_day.setdefault(_d, {})
                        for _k in ("consume", "recharge"):
                            if _v.get(_k) is not None:
                                _acc[_k] = round((_acc.get(_k) or 0) + _v[_k], 2)
                # 主表 CF/CG 来源: 单店模式=本店概览; 全店模式=账号级概览(capture_stats 里那份,
                # 同样过了「昨日」硬验收)
                if scope == "store":
                    promo_pri_ov = promo_ov_by_sheet.get("_本店")
                else:
                    promo_pri_ov = (stats or {}).get("promo") or None
                sink.step_ok("promo", "采集推广",
                             f"流水 {len(promo_by_sheet)} 店 / 概览 {len(promo_ov_by_sheet)} 店"
                             + (f"（逐店逐日 {_promo_try} 次尝试；失败分支已逐条告警）"
                                if _promo_try else ""))
            else:
                sink.log("ℹ️ 推广: 本平台无 capture_promo_ledger → 跳过(淘宝/京东走各自机制)")
        elif abort_salvage:
            sink.log("⏭️ 已按「结束任务」停止推广抓取 → CE~CG 留空(重跑补)", level="warn")
        elif _ad is None:
            sink.log("ℹ️ 推广: 本次无可用浏览器会话 → CE~CG 留空", level="warn")
        elif not _matched:
            sink.log(f"ℹ️ 推广: 未匹配到模板门店 Sheet(scope={scope}) → CE/CJ 留空"
                     f"(推广流水按门店写入各自 Sheet)", level="warn")
        # 商圈Top: 仅**单门店视角**可得(wmPoiId=门店编号), 但**逐店 × 逐日**都要采 ——
        # 2026-10-15 用户口径(任务 J): "以前的全部门店逻辑要去掉, 实际就是走的单店逻辑",
        # 模板要以报表结果为准 → 每家 matched 店 × 区间内每一天都采一次(原来只采 dates[-1])。
        # 抓不到就留空, 绝不臆造。
        _cap_on = (cfg.get("defaults") or {}).get("capture_circle_top") is not False
        # 门槛必须**说明白为什么没跑**: 2026-09-28 用户报「TOP 还是没取到」时, 日志里
        # 一条商圈记录都没有 → 既不知道跑没跑, 也不知道卡在哪。这里逐个条件给理由。
        if _cap_on and abort_salvage:
            sink.log("⏭️ 已按「结束任务」停止后续商圈抓取 → 商圈列留空(重跑补)", level="warn")
        elif not _cap_on:
            sink.log("ℹ️ 商圈Top: 配置已关闭(defaults.capture_circle_top=false) → 跳过", level="warn")
        elif ad_holder.get("ad") is None:
            sink.log("ℹ️ 商圈Top: 本次无可用浏览器会话(如 skip_browser 复用报表) → 跳过", level="warn")
        elif not (store_plan and store_plan.get("matched")):
            sink.log(f"ℹ️ 商圈Top: 未匹配到模板门店 Sheet(scope={scope}, "
                     f"匹配 {len((store_plan or {}).get('matched') or [])} 店) → 跳过。"
                     f"(商圈值按门店写入各自 Sheet; 单门店模式需另外写主表)", level="warn")
        if store_plan and store_plan.get("matched") and _cap_on and ad_holder.get("ad") is not None:
            _ad = ad_holder["ad"]
            _rows_last = all_rows.get(dates[-1]) or []
            _rows_last = _rows_last if isinstance(_rows_last, list) else [_rows_last]
            _id_by_name = {str(r.get("B") or "").strip(): str(r.get("C") or "").strip()
                           for r in _rows_last if isinstance(r, dict) and r.get("B")}
            sink.step_start("circle", "采集商圈Top",
                            f"单门店视角 × {len(store_plan['matched'])} 店 × {len(dates)} 天")
            _circ_ok, _circ_try = 0, 0
            for _nm, _sn, _why in store_plan["matched"]:
                _sid = _id_by_name.get(_nm) or ""
                if not _sid:
                    sink.log(f"  · {_nm}: 报表里没有门店编号, 跳过商圈采集", level="warn")
                    continue
                for _d in dates:
                    _circ_try += 1
                    _capd = None
                    _ct0 = time.time()
                    try:
                        _capd = _ad.capture_circle(_sid, date=_d)
                    except Exception as e:
                        sink.log(f"  · {_nm} {_d}: 商圈采集异常 {str(e)[:80]}", level="warn")
                    if _capd:
                        circle_by_sheet[(_sn, _d)] = _capd
                        _circ_ok += 1
                        _mm = mt_map.circle_write_set_all(_capd)
                        sink.log(f"  ✓ {_nm} {_d} → 商圈列 主站{len(_mm['main'])}/拼好饭{len(_mm['bao'])}"
                                 f"（口径 {_capd.get('bench') or '默认'} · {time.time() - _ct0:.0f}s）")
                    else:
                        sink.log(f"  · {_nm} {_d}: 商圈数据未取到(该日该店 Top 列留空)", level="warn")
            sink.step_ok("circle", "采集商圈Top",
                         f"{_circ_ok}/{_circ_try} 店×天取到商圈值"
                         + (f"（{len(store_plan['matched'])} 店 × {len(dates)} 天）"
                            if _circ_try else ""))
        # 商圈/推广采集是本次任务最后一次用浏览器 → 到这里才真正释放
        _stop_browser()
        per_store_filled = []
        # ---- 推广列(CE/CJ 流水 + CF/CG 概览) ----
        # 2026-10-14 口径重排(修复规格书 §4.4/T04 + 坑清单 7/8/9):
        #   · CE ← 对账页「推广费流水记录」当日「**推广订单扣款**」金额**绝对值合计**(逐店按日; 主表=各店合计);
        #   · CJ ← 同页当日「推广账户自动充值」合计;
        #   · CF/CG ← 推广概览页**切「昨日」并硬验收**后的「总曝光量/总进店量」。该页只有「昨日」快捷口径
        #     (无自定义日期), 所以: 目标日==昨天 → 写抓到的值; **其他日期写数值 0**(铁律 6, 不是留空)。
        #   · 一律**不再**用概览「总推广花费」/旧账户页「推广消费」当 CE(两者与真实扣款差一天, 会写错)。
        #   · 读不到 → 该列**不进 write_set**(留空) + 告警, 绝不拿旧口径或别天的值顶替。
        _yday_main = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        _promo_warned = set()

        def _promo_vals(day, sheet=None):
            """该日·该Sheet(或主表)的推广列 → {CE/CJ/CF/CG 子集}; 取不到的键**不出现**(留空)。"""
            out = {}
            if sheet is None:
                _v = (promo_pri_by_day or {}).get(day) or {}
                _ov, _tag = promo_pri_ov, "主表"
            else:
                _v = ((promo_by_sheet.get(sheet) or {}).get(day)) or {}
                _ov, _tag = promo_ov_by_sheet.get(sheet), f"[{sheet}]"
            for _k, _c in (("consume", "CE"), ("recharge", "CJ")):
                if _v.get(_k) is not None:
                    out[_c] = _v[_k]
            if not out.get("CE") and not out.get("CJ"):
                # 该日流水没取到(页面读失败 / 该日无流水记录) → 留空, 不用任何旧口径顶替
                if day not in _promo_warned:
                    _promo_warned.add(day)
                    sink.log(f"ℹ️ {_tag} 推广流水({day})未取到 → CE/CJ 留空"
                             f"(不用概览「总花费」或旧账户页「推广消费」顶替)", level="warn")
            if day == _yday_main:
                _ov = _ov or {}
                if (_ov.get("expose") is not None and _ov.get("visit") is not None
                        and str(_ov.get("date") or day) == day):
                    out["CF"], out["CG"] = _ov["expose"], _ov["visit"]
                else:
                    # 目标日=昨天但概览没通过「昨日」硬验收 → 留空(不写 0: 0 会被读成"确实没曝光")
                    if "ov" not in _promo_warned:
                        _promo_warned.add("ov")
                        sink.log(f"⚠️ {_tag} 推广概览「昨日」未取到或未通过验收 → CF/CG 留空"
                                 f"(不使用「今日」实时值冒充)", level="warn")
            else:
                # 非昨日: 概览页没有该日口径 → 铁律 6: 数值 0(不是留空)
                out["CF"] = out["CG"] = 0
            return out

        def _promo_summary():
            """收尾文案(给 UI): 直接说清 CE/CJ 来源与 CF/CG 是否为本期有效口径。"""
            _has_ce = any(("consume" in ((promo_pri_by_day or {}).get(d) or {})) for d in dates)
            _has_re = any(("recharge" in ((promo_pri_by_day or {}).get(d) or {})) for d in dates)
            _ov_ok = bool(promo_pri_ov and promo_pri_ov.get("expose") is not None)
            if dates[-1] == _yday_main:
                _cf = (f"CF/CG=概览「昨日」{promo_pri_ov.get('expose')}/{promo_pri_ov.get('visit')}"
                       if _ov_ok else "CF/CG 未取到(概览「昨日」未通过验收→留空)")
            else:
                _cf = f"CF/CG=0(目标日{dates[-1]}≠昨天{_yday_main}, 概览页无该日口径)"
            return (f"CE{'✓' if _has_ce else '✗'}/CJ{'✓' if _has_re else '✗'}"
                    f" 来自对账页「推广费流水记录」(扣款绝对值/自动充值; 按日按店); {_cf}")
        filled_rows, fill_errs = [], []
        sink.step_start("fill", "填入模板", f"区间 {dates[0]}~{dates[-1]} 共{len(dates)}天 (美团 E~DF)")
        # 先算 write_set, 最后批量写一次(逐日逐店直接写 = 每店一轮读+写+存+回读)
        # 主数据表 = 默认解析(第 3 个 sheet; 可用 config defaults.main_sheet 覆盖),
        # 且若解析结果**同时是本次要写的门店 Sheet** → 撞车保护回退 Sheet2(汇总绝不能冲门店数据)
        _main, _fell_main, _fell_why = resolve_main(
            store["template"], cfg,
            [m[1] for m in ((store_plan or {}).get("matched") or [])])
        # 单门店模式: **必须**写关键词匹配到的门店表(匹配不到就按关键词新建), 绝不写主表(用户口径)
        _one_sheet = _single_store_sheet(store, cfg, store["template"]) if scope == "store" else None
        if _fell_main and _one_sheet is None:
            # 真会写主表时才提示(单店模式压根不写主表, 不说); 信息级 —— 这是避让, 不是错误
            sink.log("  ℹ️ " + main_fallback_note(_fell_main, _fell_why), level="info")
        fill_jobs = []
        _warned = set()      # 逐店天气缺失提示去重(见 _warn_no_store_weather)
        for d in sorted(dates):
            A, B = pick_pair(d)
            if A is None:
                fill_errs.append(f"{d}: 未匹配到门店行")
                continue
            ws = mt_map.build_write_set(A, B, weather=_wx_of(wx_by_sheet, wmap, _one_sheet or _main, d))
            # 推广列: CE/CJ 流水 + CF/CG(昨日口径; 非昨日=0)。同样过一遍数值校验
            # (人数/金额须 ≥0 —— 负数不写 + 固定文案), 免得推广列绕开铁律 8。
            ws.update(mt_map.validate_write_set(_promo_vals(d)))
            blank = sorted(k for k, v in ws.items() if v is None)
            if blank:
                sink.log(f"  · {d} 无数据留空 {len(blank)} 列: {','.join(blank)}", level="warn")
            fill_jobs.append((_one_sheet or _main, d, ws))
            # 一店一 Sheet: 把该店的报表行也写进它自己的 Sheet(推广列不复制 —— 那是账号级数据)
            if store_plan and store_plan.get("matched"):
                _rows = all_rows.get(d) or []
                _rows = _rows if isinstance(_rows, list) else [_rows]
                _brows = bao_rows.get(d) or []
                _brows = _brows if isinstance(_brows, list) else [_brows]
                for _nm, _sn, _why in store_plan["matched"]:
                    _row = next((r for r in _rows if str(r.get("B") or "").strip() == _nm), None)
                    if _row is None:
                        continue
                    _brow = next((r for r in _brows if str(r.get("B") or "").strip() == _nm), None)
                    try:
                        _warn_no_store_weather(sink, _sn, _wx_targets, _warned)
                        _ws2 = mt_map.build_write_set(_row, _brow,
                                                      weather=_wx_of(wx_by_sheet, wmap, _sn, d))
                        # 商圈TOP列: 逐店**逐日**取(2026-10-15 任务 J 去掉 dates[-1] 门槛 ——
                        # 多日区间时每天的行都要有 TOP 值; 该日该店没采到 → 不写(留空),
                        # 绝不用别天的值顶替)。差异列仍不写(模板公式自己算)。
                        _c = circle_by_sheet.get((_sn, d))
                        if _c:
                            _cw = mt_map.circle_write_set_all(_c)
                            _ws2.update(_cw["main"])
                            _ws2.update(_cw["bao"])
                        # 推广列(按店按日): CE/CJ ← 对账页「推广订单扣款绝对值/自动充值」流水;
                        # CF/CG ← 概览页「昨日」硬验收值(非昨日=0)。取不到 → 该列不写(留空)。
                        _ws2.update(mt_map.validate_write_set(_promo_vals(d, _sn)))
                        fill_jobs.append((_sn, d, _ws2))
                    except Exception as e:
                        fill_errs.append(f"[{_sn}] {d}: {str(e)[:70]}")
        _main_names = {SHEET} | {str(m) for m in ((cfg.get("defaults") or {}).get("main_sheets") or [])}
        try:
            _results = fill_many(store["template"], fill_jobs, mode=mode,
                                 main_sheets=(cfg.get("defaults") or {}).get("main_sheets"),
                                 create_missing=_create_missing_for(store, "meituan"),
                                 on_retry=lambda m: sink.log(m, level="warn"),
                                 log=lambda m: sink.log(m, level="info"))
        except Exception as e:
            sink.step_fail("fill", "填入模板", str(e)[:200], "E_FILL_FAIL")
            sink.result(False, error_code="E_FILL_FAIL",
                        message=f"美团批量写入失败: {str(e)[:120]}", retryable=True)
            return None
        _done = set()
        for _sn, _d, _fr in _results:
            _done.add((_fr.get("requested") or _sn, _d))
            if _fr.get("reread_bad"):
                sink.log(f"  ⚠️ [{_sn}] {_d} 回读不一致列: {','.join(_fr['reread_bad'][:8])}", level="warn")
            if _sn in _main_names:
                filled_rows.append((_d, _fr))
                sink.log(f"  ✓ {_d} → 行{_fr['row']} ({_fr['how']}, {_fr['filled']}格)")
            else:
                per_store_filled.append((_d, _sn, _fr))
                sink.log(f"  ✓ [{_sn}] {_d} 行{_fr['row']} ({_fr['filled']}格)")
        for _sn, _d, _ws in fill_jobs:
            if (_sn or _one_sheet or _main, _d) not in _done:
                fill_errs.append(f"[{_sn or _main}] {_d}: 写入未生效")
        if fill_errs and not filled_rows:
            sink.step_fail("fill", "填入模板", "; ".join(fill_errs)[:200], "E_FILL_FAIL")
            sink.result(False, error_code="E_FILL_FAIL", message="区间内没有任何一天写入成功", retryable=True)
            return None
        if fill_errs:
            sink.log(f"⚠️ 部分日期失败: {'; '.join(fill_errs)[:150]}", level="warn")
        sink.step_ok("fill", "填入模板", f"成功 {len(filled_rows)}/{len(dates)} 天")
        summary.update({"平台": "美团外卖", "写入天数": len(filled_rows),
                        "写入格数/天": filled_rows[0][1]["filled"] if filled_rows else 0,
                        "拼好饭报表": "已取到" if bao_rows else "未取到(拼好饭列留空)",
                        "流量": "已填本店值(报表自带)",
                        "商圈TOP": (f"已填 {len(circle_by_sheet)} 店×天"
                                    f"（{len({s for s, _d in circle_by_sheet})} 店；口径 "
                                    f"{next(iter(circle_by_sheet.values())).get('bench') or '默认'}）"
                                    if circle_by_sheet else
                                    "未采到(逐店逐日单门店视角采集；单店漏斗不可用/无浏览器会话时留空)"),
                        "推广": _promo_summary()})
        # 数值合理性校验(规格书 铁律 8 / T11) + 表头名回退诊断: 收尾统一打印, 便于 grep 复验
        for _w in mt_map.validate_report():
            sink.log(_w, level="warn")
            summary.setdefault("数值校验", []).append(_w)
        if mt_map.NAME_FALLBACKS:
            _nf = sorted(set(mt_map.NAME_FALLBACKS))
            sink.log(f"⚠️ 表头名对不上的列 {len(_nf)} 个(按名取值失败 → 留空): {'、'.join(_nf[:10])}",
                     level="warn")
            summary.setdefault("待人工核对列(表头名对不上)", []).extend(_nf)
        if fill_errs:
            # B4(T10): 下载/解析/门店行未匹配等失败必须进 summary, 不能只躺在日志里
            summary["失败明细"] = "; ".join(fill_errs[:5])
        if per_store_filled:
            summary["一店一Sheet"] = (f"{len({s for _, s, _ in per_store_filled})} 个 Sheet / "
                                      f"{len(per_store_filled)} 次写入(逐日逐店)")
        if store_plan:
            if store_plan["unmatched"]:
                summary["未匹配模板(已跳过)"] = (f"{len(store_plan['unmatched'])} 家：" +
                                                "、".join(n for n, _ in store_plan["unmatched"][:6]))
            if store_plan["idle"]:
                summary["模板里未写入数据的Sheet"] = "、".join(store_plan["idle"][:8])
        _skip_txt = _strict_summary(store_plan, _plan_scope(store_plan))
        if _skip_txt:
            summary["不在门店明细中(已跳过)"] = _skip_txt
        if discovered:
            summary["discovered_stores"] = discovered
        outputs.append(store["template"])

        # 5. LLM 解读(区间末日; 只喂真实取到的列, 未抓取的流量明示留空)
        if llm_client and llm_client.configured and cfg.get("llm", {}).get("enabled", True):
            try:
                from .llm import daily_report
                last = dates[-1]
                a_last, b_last = pick_pair(last)
                wl = mt_map.build_write_set(a_last, b_last,
                                            weather=_wx_of(wx_by_sheet, wmap, _one_sheet or _main, last))
                data = {"商家": store["name"], "日期": last, "平台": "美团外卖",
                        "总营业额": wl.get("E"), "总营业收入": wl.get("F"), "总订单量": wl.get("G"),
                        "平台活动补贴": wl.get("H"),
                        "拼好饭营业额": wl.get("AS"), "拼好饭订单量": wl.get("AU"),
                        "店铺分": wl.get("CM"), "商家评分得分": wl.get("CN"),
                        "综合体验分": wl.get("CO"), "复购率指标得分": wl.get("CT"),
                        "食品安全负反馈率": wl.get("CX"), "出餐上报率": wl.get("CZ"),
                        "消息回复率": wl.get("DB"), "营业时段": wl.get("DC"),
                        "主站曝光": wl.get("L"), "主站进店": wl.get("O"),
                        "拼好饭曝光": wl.get("AX"), "拼好饭进店": wl.get("BA"),
                        "天气": (_wx_of(wx_by_sheet, wmap, _one_sheet or _main, last) or (None, None))[0],
                        "商圈TOP流量": "PC端无数据源, 列留空(勿推测)",
                        "推广数据": "未抓取(消耗/曝光暂缺, 勿推测)"}
                text = daily_report(llm_client, data, on_delta=lambda t: sink.llm_delta("daily_report", t))
                sink.llm_done("daily_report")
                summary["llm_report"] = text
            except Exception as e:
                sink.log(f"⚠️ LLM 解读不可用(不影响数据): {str(e)[:100]}", level="warn")

        sink.result(ok=True, outputs=outputs, summary=summary)
        return {"outputs": outputs, "summary": summary, "dates": dates}

    # 1.6 京东支路: 门店报表 xlsx(每店每天一行) → 聚合行 → 模板 HH~HJ(京东外卖数据)
    if platform == "jd":
        from .platforms import jd_mapping
        jd_mapping.IDENTITY_WARNINGS.clear()   # 诊断容器清零(本次运行从零开始)
        jd_rows = (stats or {}).get("rows") or {}
        _jd_src = ""
        if not jd_rows:      # skip_browser / 复用已有报表
            f = JDAdapter._find_jd_file(str(ddir), date_from, date)
            _jd_src = (f"目录已有: {os.path.basename(f)}" if f else f"目录 {ddir} 无目标区间报表")
            if f:
                jd_rows = JDAdapter._parse_jd_file(f)
        else:
            _jd_src = f"本次下载: {os.path.basename(str((stats or {}).get('file') or '')) or '会话内'}"
        # 一店一 Sheet 用的逐店行(报表本就是一店一行, 聚合版把它们加成一个数)
        jd_rows_store = (stats or {}).get("rows_by_store") or {}
        if not jd_rows_store:
            _fs = JDAdapter._find_jd_file(str(ddir), date_from, date)
            if _fs:
                jd_rows_store = JDAdapter._parse_jd_rows(_fs)
        if not jd_rows:
            # D4/T10: 解析失败(空)必须显式告警 → 带上门店/日期/原因, 并进 summary(不只躺在日志里)
            _m = (f"京东报表解析失败/无数据行[{store.get('name', '')}][{date_from}~{date}]: "
                  f"文件缺失或解析出 0 行({_jd_src})")
            sink.log("⚠️ " + _m, level="warn")
            sink.result(False, error_code="E_NO_REPORT", message=_m, retryable=True)
            return None
        missing = [d for d in dates if d not in jd_rows]
        if missing:
            if len(missing) == len(dates):
                sink.result(False, error_code="E_NO_REPORT",
                            message=f"京东报表内缺少这些日期: {', '.join(missing)}"
                                    f"(文档 {_jd_src}; 收入数据每日10点更新, 可稍后重试)",
                            retryable=True)
                return None
            # 2026-10-09 用户口径(照写): 只缺部分日期时不再整单作废 —— 已有日期先写入, 缺失留空, 重跑覆写。
            sink.log(f"⚠️ 京东报表缺 {', '.join(missing)} → 照写模式: 先写入已有 {len(dates) - len(missing)} 天, "
                     f"缺失留空(收入数据每日10点更新, 重跑补齐)", level="warn")
            dates = [d for d in dates if d in jd_rows]

        # 3. 天气(京东报表无营业时段, 传空); 逐店(T01): 每店按自己的门店地址取
        wmap = timed("weather", "天气获取",
                     lambda: _take_weather_map(wx_holder, dates, store, sink)) or {}
        wx_by_sheet, _ = _take_weather_by_sheet(wx_holder, dates, store, sink, targets=wx_targets)
        for d in dates:
            if wmap.get(d) and wmap[d][0]:
                sink.log(f"[天气] {d} → {wmap[d][0]} {wmap[d][1]}")
        if wx_by_sheet:
            sink.log(f"🌤 天气逐店取: {len(wx_by_sheet)} 家门店 × {len(dates)} 天")

        # 4. 填表(HH~HJ) + 一店一 Sheet(与淘宝/美团同口径: Sheet 名=地名关键字, 缺则新建)
        mode = cfg.get("defaults", {}).get("fill_mode", "auto")
        filled_rows, fill_errs = [], []
        per_store_filled = []
        store_plan = None
        if scope != "store":
            store_plan = _plan_store_sheets(sink, store,
                                            _store_names_from_rows(jd_rows_store, dates), cfg,
                                            ids_by_name=_store_ids_from_rows(jd_rows_store, dates,
                                                                             id_key="门店id"))
        sink.step_start("fill", "填入模板", f"区间 {dates[0]}~{dates[-1]} 共{len(dates)}天 (京东 HH~HJ)")
        # 先算 write_set, 最后批量写入一次(逐日逐店直接写 = 每店一轮读+写+存+回读)
        # 主数据表 = 默认解析(第 3 个 sheet; 可用 config defaults.main_sheet 覆盖),
        # 且若解析结果**同时是本次要写的门店 Sheet** → 撞车保护回退 Sheet2(汇总绝不能冲门店数据)
        _main, _fell_main, _fell_why = resolve_main(
            store["template"], cfg,
            [m[1] for m in ((store_plan or {}).get("matched") or [])])
        # 单门店模式: **必须**写关键词匹配到的门店表(匹配不到就按关键词新建), 绝不写主表(用户口径)
        _one_sheet = _single_store_sheet(store, cfg, store["template"]) if scope == "store" else None
        if _fell_main and _one_sheet is None:
            # 真会写主表时才提示(单店模式压根不写主表, 不说); 信息级 —— 这是避让, 不是错误
            sink.log("  ℹ️ " + main_fallback_note(_fell_main, _fell_why), level="info")
        fill_jobs = []
        _warned = set()
        _row_errs = []      # D4/T10: 逐店行取不到/多候选 → 显式告警并进 summary, 不静默
        # 京东编号来源(规格书 铁律 11): 真实《门店明细》没有京东组 → {}; 将来配了就用编号优先锁行
        # (by_name_id: 规范化店名→编号, 与报表第 2 列「门店id」同口径)。
        _jd_ids = _switch_id_map(store, "jd")
        try:
            from . import sheet_match as _sm
        except Exception:
            _sm = None
        for d in sorted(dates):
            ws = jd_mapping.build_write_set(jd_rows.get(d),
                                            weather=_wx_of(wx_by_sheet, wmap, _one_sheet or _main, d))
            blank = sorted(k for k, v in ws.items() if v is None)
            if blank:
                sink.log(f"  · {d} 无数据留空 {len(blank)} 列: {','.join(blank)}", level="warn")
            fill_jobs.append((_one_sheet or _main, d, ws))
            # 逐店行写进各自 Sheet(天气是城市级, 可以照传)
            if store_plan and store_plan.get("matched"):
                _srows = jd_rows_store.get(d) or []
                _srows = _srows if isinstance(_srows, list) else [_srows]
                for _nm, _sn, _why in store_plan["matched"]:
                    _sid = _jd_ids.get(_sm._tidy(_nm)) if (_jd_ids and _sm) else None
                    _row, _rmatch = _match_store_row(_srows, _nm, store_id=_sid)
                    if _row is None:
                        # 规格书 铁律 7/11: 取不到目标门店行 → **报错不得静默留空**
                        _msg = f"[{_sn}] {d} 取不到京东门店行「{_nm}」: {_rmatch}"
                        sink.log("⚠️ " + _msg, level="warn")
                        _row_errs.append(_msg)
                        continue
                    _warn_no_store_weather(sink, _sn, wx_targets, _warned)
                    _ws_row = jd_mapping.build_write_set(
                        _row, weather=_wx_of(wx_by_sheet, wmap, _sn, d))
                    if not any(_ws_row.get(c) is not None for c in ("HH", "HI", "HJ")):
                        # 行内自校验不成立(或收入/营业额/支出全空) → 该行不写, 由 jd_mapping 已记告警
                        _msg = f"[{_sn}] {d} 京东门店行「{_nm}」未通过行内自校验 → 该行不写入"
                        sink.log("⚠️ " + _msg + "(见 [京东行内校验] 原始值)", level="warn")
                        _row_errs.append(_msg)
                        continue
                    fill_jobs.append((_sn, d, _ws_row))
        _main_names = {SHEET} | {str(m) for m in ((cfg.get("defaults") or {}).get("main_sheets") or [])}
        try:
            _results = fill_many(store["template"], fill_jobs, mode=mode,
                                 main_sheets=(cfg.get("defaults") or {}).get("main_sheets"),
                                 create_missing=_create_missing_for(store, "jd"),
                                 on_retry=lambda m: sink.log(m, level="warn"),
                                 log=lambda m: sink.log(m, level="info"))
        except Exception as e:
            sink.result(False, error_code="E_FILL_FAIL",
                        message=f"京东批量写入失败: {str(e)[:120]}", retryable=True)
            return None
        _done = set()
        for _sn, _d, _fr in _results:
            _done.add((_fr.get("requested") or _sn, _d))
            if _fr.get("reread_bad"):
                sink.log(f"  ⚠️ [{_sn}] {_d} 回读不一致列: {','.join(_fr['reread_bad'][:8])}", level="warn")
            if _sn in _main_names:
                filled_rows.append((_d, _fr))
                sink.log(f"  ✓ {_d} → 行{_fr['row']} ({_fr['how']}, {_fr['filled']}格)")
            else:
                per_store_filled.append((_d, _sn, _fr))
                sink.log(f"  ✓ [{_sn}] {_d} → 行{_fr['row']} ({_fr['filled']}格)")
        for _sn, _d, _ws in fill_jobs:
            if (_sn or _one_sheet or _main, _d) not in _done:
                fill_errs.append(f"[{_sn or _main}] {_d}: 写入未生效")
        if fill_errs and not filled_rows:
            sink.result(False, error_code="E_FILL_FAIL",
                        message=f"京东填表失败: {fill_errs[0]}", retryable=True)
            return None
        summary = {"商家": store["name"], "区间": f"{dates[0]}~{dates[-1]}",
                   "范围": "全部门店" if scope != "store" else f"门店#{store.get('keyword', '')}",
                   "平台": "京东到家", "写入天数": len(filled_rows),
                   "写入格数/天": filled_rows[0][1]["filled"] if filled_rows else 0}
        if per_store_filled:
            summary["一店一Sheet"] = (f"{len({s for _, s, _ in per_store_filled})} 个 Sheet / "
                                      f"{len(per_store_filled)} 次写入(逐日逐店)")
        elif store_plan:
            summary["一店一Sheet"] = (f"未写入（匹配到 {len(store_plan.get('matched') or [])} 家，"
                                      f"检查模板 Sheet 名）")
        if _row_errs:
            # D4/T10: 取不到目标门店行 → 汇总计数进 summary(逐条 warn 已在上面打过)
            summary["取不到门店行(已跳过)"] = f"{len(_row_errs)} 条: " + "; ".join(_row_errs[:3])
        _skip_txt = _strict_summary(store_plan, _plan_scope(store_plan))
        if _skip_txt:
            summary["不在门店明细中(已跳过)"] = _skip_txt
        # D4/T10: 行内自校验(收入=营业额−支出)+ 取不到门店行 → **进 summary**(不只躺在日志里)
        for _w in jd_mapping.identity_report():
            sink.log("⚠️ " + _w, level="warn")
            summary.setdefault("行内自校验未过", []).append(_w)
        if _row_errs:
            summary["失败明细"] = "; ".join(_row_errs[:5])
        outputs.append(store["template"])

        # 5. LLM 解读(京东区数据少, 只喂实有字段)
        if llm_client and llm_client.configured and cfg.get("llm", {}).get("enabled", True):
            try:
                from .llm import daily_report
                last = dates[-1]
                wl = jd_mapping.build_write_set(jd_rows.get(last),
                                                weather=_wx_of(wx_by_sheet, wmap, _one_sheet or _main, last))
                data = {"商家": store["name"], "日期": last, "平台": "京东到家",
                        "营业额": wl.get("HH"), "营业收入": wl.get("HI"), "订单量": wl.get("HJ"),
                        "天气": (_wx_of(wx_by_sheet, wmap, _one_sheet or _main, last) or (None, None))[0]}
                text = daily_report(llm_client, data, on_delta=lambda t: sink.llm_delta("daily_report", t))
                sink.llm_done("daily_report")
                summary["llm_report"] = text
            except Exception as e:
                sink.log(f"⚠️ LLM 解读不可用(不影响数据): {str(e)[:100]}", level="warn")

        sink.result(ok=True, outputs=outputs, summary=summary)
        return {"outputs": outputs, "summary": summary, "dates": dates}

    # 1.7 美团管家支路: 多门店模式(一店一Sheet, Sheet名=门店名关键字; 脑图口径 HN~HY)
    # 无全店聚合视图 → scope=all = 每家门店各自导出+各自填Sheet; 单店 = 只跑 keyword 店
    if platform == "meituan_gj":
        from .platforms import gj_mapping
        stores = (stats or {}).get("stores") or {}
        if not stores:
            import glob as _glob
            hits = sorted(_glob.glob(str(ddir / "管家_*_*.xlsx")))
            for f in hits:
                import os as _os
                base = _os.path.basename(f)
                try:
                    name = base.split("_", 1)[1].rsplit("_", 2)[0]
                except Exception:
                    name = base
                try:
                    stores[name] = gj_mapping.parse_biz_xlsx(f)
                except Exception:
                    pass
        if not stores:
            sink.result(False, error_code="E_NO_REPORT",
                        message=f"美团管家 {date_from}~{date} 无导出文件(下载失败或无门店)", retryable=True)
            return None

        wmap = timed("weather", "天气获取",
                     lambda: _take_weather_map(wx_holder, dates, store, sink)) or {}
        # 逐店(T01): 每店按自己的门店地址取; 温度按**该店自己的营业时段**? 管家报表只有当天
        # 一份数据(不带营业时段) → 传空(全天口径, 与旧行为一致)
        wx_by_sheet, _ = _take_weather_by_sheet(wx_holder, dates, store, sink, targets=wx_targets)

        mode = cfg.get("defaults", {}).get("fill_mode", "auto")
        # 门店 ↔ Sheet 计划: 模板是权威(一店一 Sheet, Sheet 名=地名关键词), 按关键词匹配;
        # 匹配不到就**跳过不写入**(不报错、不自动建 Sheet)。
        from . import sheet_match
        import openpyxl as _oxl
        wb0 = _oxl.load_workbook(store["template"], read_only=True)
        try:
            all_sheets = list(wb0.sheetnames)
        finally:
            wb0.close()
        kw_cfg = ([store["keyword"]] if store.get("keyword") else []) + list(store.get("keywords") or [])
        main_sheets = cfg.get("defaults", {}).get("main_sheets")
        _smap = sheet_match.load_store_map(store["template"], "meituan_gj")
        _extra, _idmap = _detail_maps(store, "meituan_gj")
        # 编号锁定(2026-10-11 接线): 适配器从「门店选择页」卡片读到 机构编码(12位, 与
        # 《门店明细》H 列同口径) → plan_matches ⓪「编号锁定」才能真正命中。
        # 此前 id_map 加载了却没传进来, 全靠名称/关键词猜 —— 「长沙西服务区南区店/北区店」
        # 这类前缀相同的就会互相误判。
        _ids = (stats or {}).get("store_ids") or {}
        if _extra or _idmap:
            sink.log(f"🧾 门店明细已加载(管家): {len(_extra)} 条名称 / {len(_idmap)} 条编号"
                     f"（本次识别到 {len(_ids)} 个门店编号）")
        _merged = _merge_maps(_smap, _extra)
        # 门店明细严格模式(A2): stores 已被适配器按明细过滤过, 这里只决定"要不要自动建表"
        # 与兜底剔除(适配器未过滤的旧路径/映射表混入的明细外门店)。
        _sc = _details_scope(sink, store, "meituan_gj", set(stores.keys()), _ids, label="后台门店")
        plan = (sheet_match.plan_matches(all_sheets, list(stores.keys()), kw_cfg, main=main_sheets,
                                         store_map=_merged, id_map=_idmap, store_ids=_ids)
                if main_sheets else sheet_match.plan_matches(all_sheets, list(stores.keys()), kw_cfg,
                                                             store_map=_merged, id_map=_idmap,
                                                             store_ids=_ids))
        # 匹配不到的店 → 按地名关键字决定 Sheet 名(真建表交给 fill_many 一次完成, 省掉逐店读写)
        # 严格模式下不建(门店明细里没有对应模板 Sheet → 跳过并提示补模板/改明细)。
        for _nm, _sn, _why in _autocreate_store_sheets(sink, store["template"], plan,
                                                       _sheet_rules(cfg),
                                                       enabled=_autocreate_enabled(cfg) and not _sc["strict"],
                                                       detail_map=_extra, strict=_sc["strict"]):
            plan["matched"].append((_nm, _sn, _why))
        _apply_strict_plan(sink, plan, _sc, label="后台门店")
        sink.log(f"🗂 模板门店工作表: {'、'.join(plan['candidates']) or '(无)'}")
        for name, sn, why in plan["matched"]:
            sink.log(f"  🔗 门店「{name}」→ Sheet「{sn}」（{why}）")
        for name, why in plan["unmatched"]:
            sink.log(f"  ⏭ 后台门店「{name}」未写入：{why}", level="warn")
        for sn in plan["idle"]:
            sink.log(f"  ⏭ 模板 Sheet「{sn}」在后台没有对应门店，保持不写入", level="warn")
        if not plan["matched"]:
            sink.result(False, error_code="E_FILL_FAIL",
                        message=(f"没有任何后台门店能对上模板 Sheet｜后台 {len(stores)} 家: "
                                 f"{'、'.join(list(stores)[:6])}｜模板门店工作表: "
                                 f"{'、'.join(plan['candidates']) or '无'}"), retryable=True)
            return None
        filled_rows, fill_errs = [], []
        sink.step_start("fill", "填入模板",
                        f"区间 {dates[0]}~{dates[-1]} 共{len(dates)}天 × {len(plan['matched'])} 店 (美团管家 HN~HY)")
        # 先算 write_set(纯计算, 不碰文件), 最后**批量写一次** —— 与淘宝/京东/美团外卖同口径。
        # 原来每店每天各自 fill_template = 每店一轮读+写+存+回读, 31 家店白花几分钟。
        # 主数据表 = 默认解析(第 3 个 sheet; 可用 config defaults.main_sheet 覆盖),
        # 且若解析结果**同时是本次要写的门店 Sheet** → 撞车保护回退 Sheet2(汇总绝不能冲门店数据)
        _main, _fell_main, _fell_why = resolve_main(
            store["template"], cfg,
            [m[1] for m in (plan.get("matched") or [])])
        # 注: 本块数据全部写各自门店 Sheet(从不写主数据表) → 撞车避让不需要给用户任何提示
        # (统一文案见 fill.main_fallback_note; 这里 resolve 只是防御性保留)
        # 单门店模式: **必须**写关键词匹配到的门店表(匹配不到就按关键词新建), 绝不写主表(用户口径)
        _one_sheet = _single_store_sheet(store, cfg, store["template"]) if scope == "store" else None
        fill_jobs = []
        for name, sn, _why in plan["matched"]:
            try:
                ws = gj_mapping.build_write_set(stores[name],
                                               weather=_wx_of(wx_by_sheet, wmap, sn, dates[-1]))
                for d in sorted(dates):      # 升序: top 模式下最新一天落在表头下第一行(与其它支路一致)
                    fill_jobs.append((sn, d, ws))
            except Exception as e:
                fill_errs.append(f"{name}: {str(e)[:80]}")
        try:
            _results = fill_many(store["template"], fill_jobs, mode=mode,
                                 main_sheets=(cfg.get("defaults") or {}).get("main_sheets"),
                                 create_missing=_create_missing_for(store, "meituan_gj"),
                                 on_retry=lambda m: sink.log(m, level="warn"),
                                 log=lambda m: sink.log(m, level="info"))
        except Exception as e:
            sink.result(False, error_code="E_FILL_FAIL",
                        message=f"美团管家批量写入失败: {str(e)[:120]}", retryable=True)
            return None
        for _sn, _d, _fr in _results:
            filled_rows.append((_sn, _d, _fr))
            if _fr.get("reread_bad"):
                sink.log(f"  ⚠️ [{_sn}] {_d} 回读不一致列: {','.join(_fr['reread_bad'][:8])}", level="warn")
            sink.log(f"  ✓ [{_sn}] {_d} 行{_fr['row']} ({_fr['filled']}格)")
        if not filled_rows:
            sink.result(False, error_code="E_FILL_FAIL",
                        message=f"美团管家填表失败: {fill_errs[0] if fill_errs else '无数据写入'}",
                        retryable=True)
            return None
        gj_failed = (stats or {}).get("failed") or []
        if gj_failed:
            for f in gj_failed[:8]:
                sink.log(f"  ⛔ {f['name']}：{f.get('reason', '未取得数据')}", level="warn")
        summary = {"商家": store["name"], "区间": f"{dates[0]}~{dates[-1]}",
                   "平台": "美团管家", "写入店数": len({r[0] for r in filled_rows}),
                   "写入格数/店/天": filled_rows[0][2]["filled"] if filled_rows else 0}
        if plan["unmatched"]:
            summary["未匹配模板(已跳过)"] = (f"{len(plan['unmatched'])} 家：" +
                                            "、".join(n for n, _ in plan["unmatched"][:6]))
        if plan["idle"]:
            summary["模板里未写入数据的Sheet"] = ("、".join(plan["idle"][:8])
                                              + "(平台当天无数据或后台无此店, 原因见日志)")
        if gj_failed:
            summary["未取到数据的门店"] = (f"{len(gj_failed)} 家：" +
                                          "、".join(f["name"] for f in gj_failed[:8]) +
                                          ("…" if len(gj_failed) > 8 else ""))
        if fill_errs:
            summary["部分失败"] = "; ".join(fill_errs[:3])
        _skip_txt = _strict_summary(plan, _sc)
        if _skip_txt:
            summary["不在门店明细中(已跳过)"] = _skip_txt
        outputs.append(store["template"])
        sink.result(ok=True, outputs=outputs, summary=summary)
        return {"outputs": outputs, "summary": summary, "dates": dates}

    # 2. 识别报表 + 校验区间数据完整(淘宝)
    all_f, bom_f = find_report_files(str(ddir), date_from, date)
    if not (all_f and bom_f):
        missing = "、".join([n for n, f in (("全部", all_f), ("爆品团", bom_f)) if not f])
        have = "、".join([n for n, f in (("全部", all_f), ("爆品团", bom_f)) if f]) or "无"
        # 先看是不是"下载下来的文件本身是空的/坏的" —— 这跟"账号没有这类业务"是两回事,
        # 报错必须分开, 否则用户拿着"缺全部报表"完全不知道该怎么办。
        broken = broken_report_files(str(ddir), date_from, date)
        if broken:
            detail = "；".join([f"{n} {why}" for n, why in broken[:3]])
            sink.result(False, error_code="E_EMPTY_REPORT",
                        message=(f"目录 {ddir} 里下载到的报表文件是空的/坏的：{detail}。"
                                 f"这通常是平台「任务已提交但文件还没生成好」——请稍等 1~2 分钟重新运行；"
                                 f"若一直如此，把区间往前挪一天试试。"), retryable=True)
            return None
        # 不是空/坏文件, 那还有一种最常见的情况: **下到了, 但日期不是这次的**
        # (2026-09-30 真机: 请求 09-28, 平台生成 09-29 → 名字区间与内容都对不上, 报错却只写
        #  "已有: 无", 用户以为程序没找到文件)。把目录里最新下载的实况直接摆进报错。
        _inv = downloaded_but_unusable(str(ddir), date_from, date)
        _inv_txt = unusable_report_hint(_inv)
        sink.log(f"📥 目录里最新下载的报表: {_inv_txt}", level="warn")
        sink.result(False, error_code="E_NO_REPORT",
                    message=(f"目录 {ddir} 里缺「{missing}」报表（已有: {have}）。注意：「全部」/「爆品团」"
                             f"是平台报表的文件类型（两种业务口径，各一份文件），不是 sheet 名。"
                             f"可能原因: ①平台还在生成, 稍后重试 ②区间超出可下载范围(选更近的日期) "
                             f"③该账号没有这类业务(单店账号通常没有「爆品团」，此时请把区间选到有数据的日期"
                             f"或告知我们调整为单报表模式)。｜目录里最新下载的报表: {_inv_txt}"), retryable=True)
        return None
    # 一店一 Sheet 必须用**逐店行**: all_rows 是账号级聚合(同一天多行求和 → 只剩 1 行,
    # 门店名只剩文件首行那家) → 用它建门店计划, 17 家店的报表只会写进 1 家
    # (2026-09-30 真机: 淘宝全部门店跑完, 模板里只有「岚霞路」拿到数据)。
    all_rows_store = load_report_rows_by_store(all_f)
    bom_rows_store = load_report_rows_by_store(bom_f)
    all_rows = load_report_rows(all_f)
    bom_rows = load_report_rows(bom_f)
    # 报表里的门店数必须**当场报出来**: 平台只导出了"当前门店视角"时, 一店一 Sheet 只会写进
    # 1 家(2026-09-30 真机: 美团全部门店任务 ok=true, 但报表里只有 1 家店 → 只有它拿到数据,
    # 用户看到的是"日志说完成、表里没数据", 却没有任何线索)。
    _n_all = len(all_rows_store.get(dates[-1]) or [])
    _n_bom = len(bom_rows_store.get(dates[-1]) or [])
    sink.log(f"📄 全部={os.path.basename(all_f)}({_n_all} 家门店) "
             f"爆品团={os.path.basename(bom_f)}({_n_bom} 家门店)")
    if _n_all <= 1:
        sink.log(f"⚠️ 这份「全部」报表里只有 {_n_all} 家门店 —— 平台很可能只导出了当前门店视角, "
                 f"其余门店的 Sheet 本次不会有数据(请在平台把视角切到全部门店后重跑)", level="warn")
    missing = [d for d in dates if d not in all_rows or d not in bom_rows]
    if missing:
        # 报错必须能自证: 把「已选中文件里实际有哪些日期」摆出来。真坑是平台文件名末段为
        # **下载时间戳**(不是报表日期), 于是选中的是别的区间的旧文件(2026-09-24 实测),
        # 原文案「平台日报通常次日才可下载」会把人误导到错方向。
        try:
            _cands = [c for c in report_file_candidates(ddir, dates[0], dates[-1])
                      if c["size"] >= 1024]
            _detail = "；".join(f"{c['name'][:30]}… 内含 {dates_span_text(c['dates'])}"
                               for c in _cands[:4])
        except Exception:
            _detail = ""
        sink.result(False, error_code="E_NO_REPORT",
                    message=(f"报表内缺少这些日期的数据行: {', '.join(missing)}。"
                             + (f"已下载报表的实际日期: {_detail}。" if _detail else "")
                             + "若实际日期与所选区间不符, 说明这份文件不是这次要的"
                               "(平台文件名末尾那串是下载时间戳, 不是报表日期); "
                               "可稍后重试(平台有时要过一阵才生成)或把区间改到已有数据的日期。"),
                    retryable=False)
        return None

    # 行选择: 单店按门店编号/名称匹配; 全店用该日全部行的汇总(淘宝全店报表该日可能多行=每店一行, 需聚合)
    def pick_rows(d):
        av, bv = all_rows.get(d), bom_rows.get(d)
        if scope != "store":
            return av, bv
        def one(v):
            rows = v if isinstance(v, list) else [v]
            if len(rows) == 1:
                return rows[0]
            sid = str(store.get("id", ""))
            for r in rows:
                if sid and (str(r.get("门店编号", "")) == sid or sid in str(r.get("门店名称", ""))):
                    return r
            kw = store.get("keyword", "")
            for r in rows:
                if kw and kw in str(r.get("门店名称", "")):
                    return r
            return None
        return one(av), one(bv)

    # 全店任务: 提取门店列表(回传前端)
    discovered = None
    if scope == "all":
        av = all_rows.get(dates[-1]) or []
        rows = av if isinstance(av, list) else [av]
        if len(rows) > 1:
            discovered = [{"id": str(r.get("门店编号", "")), "name": str(r.get("门店名称", ""))}
                          for r in rows if r.get("门店名称")]

    # 3. 天气(区间逐日) —— 主表口径(账号默认坐标)
    def do_weather():
        out = {}
        for d in dates:
            av = all_rows.get(d)
            first = (av if isinstance(av, list) else [av])[0]
            biz = first.get("设置营业时间段")
            biz = biz if isinstance(biz, str) and "-" in biz else None
            out[d] = weather_mod.fetch_cached(d, store.get("lat", 28.187), store.get("lon", 112.921), biz)
        return out

    # 单门店模式的目标表(要在天气之前定: 逐店取数要把它算进 targets)
    _one_sheet = _single_store_sheet(store, cfg, store["template"]) if scope == "store" else None
    # 数值校验容器清零(D5/T11 诊断: 本次运行从零开始, 收尾统一打印)
    from .platforms import taobao_mapping as _tb_map
    _tb_map.VALIDATE_WARNINGS.clear()
    _tb_map.VALIDATE_NOTES.clear()
    # 3.5 逐店天气(T01/铁律 9): 每店按**自己的门店地址**取(长沙/湘潭分别取值, 不再全表共用);
    # 温度按该店**当天**的「设置营业时间段」窗口统计(淘宝报表带这一列)。
    _wx_targets = _weather_targets(store, extra=[_one_sheet] if _one_sheet else [])
    _biz_by = _biz_hours_by_sheet(
        store, "taobao", all_rows_store, dates,
        pick=lambda r: (str(r.get("设置营业时间段") or "")
                        if "-" in str(r.get("设置营业时间段") or "") else None))

    def do_weather_all():
        base = do_weather()
        if not _wx_targets:
            return base, {}
        by_sheet, warns = weather_mod.fetch_map(_wx_targets, dates, _biz_by, label="淘宝门店")
        _log_weather_warns(sink, warns)
        return base, by_sheet

    wmap, wx_by_sheet = timed("weather", "天气获取", do_weather_all) or ({}, {})
    for d in dates:
        if wmap.get(d) and wmap[d][0]:
            sink.log(f"[天气] {d} → {wmap[d][0]} {wmap[d][1]}")
    if wx_by_sheet:
        sink.log(f"🌤 天气逐店取: {len(wx_by_sheet)} 家门店 × {len(dates)} 天")

    # 注: 逐店抓取已移到**抓取步骤内部**(do_capture) —— 放这里时浏览器已被关掉。

    # 4. 填表(区间逐日; top 模式从最旧到最新依次插入 → 最新日期在第一行)
    from .platforms.taobao_mapping import build_write_set
    mode = cfg.get("defaults", {}).get("fill_mode", "auto")
    # 一店一 Sheet(与美团同口径): Sheet 名=地名关键字; 匹配不到 → 新建(复刻模板表头) 后写入。
    # 注意: 网页抓到的流量/推广是**账号级**, 只写主表, **不复制**到门店 Sheet。
    store_plan = None
    per_store_filled = []
    if scope != "store":
        store_plan = _plan_store_sheets(sink, store, _store_names_from_rows(all_rows_store, dates), cfg,
                                        ids_by_name=_store_ids_from_rows(all_rows_store, dates))
    filled_rows = []
    fill_errs = []
    sink.step_start("fill", "填入模板", f"区间 {dates[0]}~{dates[-1]} 共{len(dates)}天")
    # 先只**算**出每天/每店的 write_set(纯计算, 不碰文件), 最后一次性批量落盘。
    # 原因: 逐日逐店直接调 fill_template = 每天每店一轮「读模板+写+存+回读」(京东 22 店 1 天
    # 实测 316s); 批量化后整任务 1 次读 + 1 次写 + 1 次存 + 1 次回读(见 fill.fill_many)。
    # 主数据表 = 默认解析(第 3 个 sheet; 可用 config defaults.main_sheet 覆盖) + 撞车保护
    _main, _fell_main, _fell_why = resolve_main(store["template"], cfg,
                                                [m[1] for m in ((store_plan or {}).get("matched") or [])])
    # 单门店模式: **必须**写关键词匹配到的门店表(匹配不到就按关键词新建), 绝不写主表(用户口径)
    # (_one_sheet 在上面「逐店天气」之前就解析了 —— 取数要把它算进 targets)
    if _fell_main and _one_sheet is None:
        # 真会写主表时才提示(单店模式压根不写主表, 不说); 信息级 —— 这是避让, 不是错误
        sink.log("  ℹ️ " + main_fallback_note(_fell_main, _fell_why), level="info")
    fill_jobs = []
    _warned = set()
    for d in sorted(dates):
        A, B = pick_rows(d)
        if A is None or B is None:
            fill_errs.append(f"{d}: 未匹配到门店行")
            continue
        # 区间模式 stats={日期:各态}; 单日模式 stats 直接是各态
        stats_day = (stats or {}).get(d, stats or {}) if stats else {}
        promo_day = stats_day.get("promo") if isinstance(stats_day, dict) else None
        day_stats = {k: v for k, v in (stats_day or {}).items() if k != "promo"} if isinstance(stats_day, dict) else {}
        # 区间模式: 推广累计值只落在末日, 其他天用当天抓取的推广(逐日模式 promo 在 out["promo"] 为累计, 天级不拆)
        ws_promo = (promo_day, )
        write_set = build_write_set(A, B, day_stats if day_stats else stats,
                                    weather=_wx_of(wx_by_sheet, wmap, _one_sheet or _main, d))
        for name, ok in cross_checks(write_set):
            if ok is False:
                sink.log(f"⚠️ {d} 交叉校验未过: {name}", level="warn")
        fill_jobs.append((_one_sheet or _main, d, write_set))      # None = 主数据表(Sheet2)
        # 一店一 Sheet: 把该店自己的报表行写进它自己的 Sheet
        # (账号级的流量/推广 capture 不复制 —— 复制过去就是错的)
        if store_plan and store_plan.get("matched"):
            _rows = all_rows_store.get(d) or []
            _rows = _rows if isinstance(_rows, list) else [_rows]
            _brows = bom_rows_store.get(d) or []
            _brows = _brows if isinstance(_brows, list) else [_brows]
            for _nm, _sn, _why in store_plan["matched"]:
                _row = next((r for r in _rows if str(r.get("门店名称") or "").strip() == _nm), None)
                if _row is None:
                    continue
                _brow = next((r for r in _brows if str(r.get("门店名称") or "").strip() == _nm), None)
                try:
                    # 逐店 capture(该店自己的外卖本店列 + 商圈同行前10%基准列) → 写它自己的 Sheet
                    _warn_no_store_weather(sink, _sn, _wx_targets, _warned)
                    _cap = (ad_holder.get("per_store_caps") or {}).get(_nm) if d == dates[-1] else None
                    fill_jobs.append((_sn, d, build_write_set(
                        _row, _brow, _cap, weather=_wx_of(wx_by_sheet, wmap, _sn, d))))
                except Exception as e:
                    fill_errs.append(f"[{_sn}] {d}: {str(e)[:70]}")
    _main_names = {SHEET} | {str(m) for m in ((cfg.get("defaults") or {}).get("main_sheets") or [])}
    try:
        _results = fill_many(store["template"], fill_jobs, mode=mode,
                             main_sheets=(cfg.get("defaults") or {}).get("main_sheets"),
                             create_missing=_create_missing_for(store, platform),
                             on_retry=lambda m: sink.log(m, level="warn"),
                             log=lambda m: sink.log(m, level="info"))
    except Exception as e:
        sink.step_fail("fill", "填入模板", str(e)[:200], "E_FILL_FAIL")
        sink.result(False, error_code="E_FILL_FAIL", message=f"批量写入失败: {str(e)[:120]}", retryable=True)
        return None
    _done = set()
    for _sn, _d, _fr in _results:
        _done.add((_fr.get("requested") or _sn, _d))
        if _fr.get("reread_bad"):
            sink.log(f"  ⚠️ [{_sn}] {_d} 回读不一致列: {','.join(_fr['reread_bad'][:8])}", level="warn")
        if _sn in _main_names:
            filled_rows.append((_d, _fr))
            sink.log(f"  ✓ {_d} → 行{_fr['row']} ({_fr['how']}, {_fr['filled']}格)")
        else:
            per_store_filled.append((_d, _sn, _fr))
            sink.log(f"  ✓ [{_sn}] {_d} 行{_fr['row']} ({_fr['filled']}格)")
    for _sn, _d, _ws in fill_jobs:      # 计划了但没写成功的
        if (_sn or _one_sheet or _main, _d) not in _done:
            fill_errs.append(f"[{_sn or _main}] {_d}: 写入未生效")
    if fill_errs and not filled_rows:
        sink.step_fail("fill", "填入模板", "; ".join(fill_errs)[:200], "E_FILL_FAIL")
        sink.result(False, error_code="E_FILL_FAIL", message="区间内没有任何一天写入成功", retryable=True)
        return None
    if fill_errs:
        sink.log(f"⚠️ 部分日期失败: {'; '.join(fill_errs)[:150]}", level="warn")
    sink.step_ok("fill", "填入模板",
                 f"成功 {len(filled_rows)}/{len(dates)} 天" +
                 (f" · 第一行={filled_rows[0][1]['row']}" if filled_rows else ""))
    summary.update({"写入天数": len(filled_rows), "写入格数/天": filled_rows[0][1]["filled"] if filled_rows else 0})
    if per_store_filled:
        summary["一店一Sheet"] = (f"{len({s for _, s, _ in per_store_filled})} 个 Sheet / "
                                  f"{len(per_store_filled)} 次写入(逐日逐店)")
    if store_plan:
        if store_plan["unmatched"]:
            summary["未匹配模板(已跳过)"] = (f"{len(store_plan['unmatched'])} 家：" +
                                            "、".join(n for n, _ in store_plan["unmatched"][:6]))
        if store_plan["idle"]:
            summary["模板里未写入数据的Sheet"] = "、".join(store_plan["idle"][:8])
    _skip_txt = _strict_summary(store_plan, _plan_scope(store_plan))
    if _skip_txt:
        summary["不在门店明细中(已跳过)"] = _skip_txt
    # 数值合理性校验(规格书 铁律 8 / T11)结果收尾统一打印: 三态通过 → debug 取证;
    # 超界/三态不通过 → warn + 进 summary(超界值已由 build_write_set 拦下未写入)。
    from .platforms import taobao_mapping as _tb_map
    for _w in _tb_map.validate_notes():
        sink.log(_w, level="debug")
    for _w in _tb_map.validate_report():
        sink.log(_w, level="warn")
        summary.setdefault("数值校验", []).append(_w)
    if discovered:
        summary["discovered_stores"] = discovered
    outputs.append(store["template"])

    # 5. LLM 解读(用区间末日数据)
    if llm_client and llm_client.configured and cfg.get("llm", {}).get("enabled", True):
        try:
            from .llm import daily_report
            last = dates[-1]
            A_last = pick_rows(last)[0]
            write_last = build_write_set(A_last, pick_rows(last)[1] or {}, stats or {},
                                         weather=_wx_of(wx_by_sheet, wmap, _one_sheet or _main, last))
            data = {"商家": store["name"], "日期": last,
                    "营业额": write_last.get("DG"), "订单": write_last.get("DI"),
                    "主站曝光": write_last.get("DM"), "TOP曝光": write_last.get("DN"),
                    "主站进店": write_last.get("DP"), "TOP进店": write_last.get("DQ"),
                    "天气": (_wx_of(wx_by_sheet, wmap, _one_sheet or _main, last) or (None, None))[0]}
            text = daily_report(llm_client, data, on_delta=lambda t: sink.llm_delta("daily_report", t))
            sink.llm_done("daily_report")
            summary["llm_report"] = text
        except Exception as e:
            sink.log(f"⚠️ LLM 解读不可用(不影响数据): {str(e)[:100]}", level="warn")

    sink.result(ok=True, outputs=outputs, summary=summary)
    return {"outputs": outputs, "summary": summary, "dates": dates}
