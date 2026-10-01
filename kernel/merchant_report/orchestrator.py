# -*- coding: utf-8 -*-
"""任务编排器: 登录 → 按区间下载报表 → 流量/推广抓取(区间自定义模式) → 天气 → 逐日填表 → (可选)LLM解读。
全程 NDJSON 事件流(协议 v1)。区间 [date_from, date] 内每天一行数据; 填表 top 模式最新日期在第一行。"""
import os
from datetime import datetime, timedelta
from pathlib import Path

from .config import app_data_dir, load_config
from .diagnose import root_cause
from .fill import (SHEET, broken_report_files, cross_checks, dates_span_text, default_sheet_name,
                   downloaded_but_unusable, fill_many, resolve_main,
                   fill_template, find_report_files, load_report_rows,
                   load_report_rows_by_store, report_file_candidates, unusable_report_hint)
from . import weather as weather_mod
from .platforms.taobao import TaobaoAdapter, launch_browser
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


def _plan_store_sheets(sink, store, names, cfg, label="门店"):
    """后台门店名 → 模板 Sheet 计划(含"匹配不到就新建 Sheet")。

    一店一 Sheet 的公共实现, 供各平台支路复用(淘宝/京东...), 别再抄一遍。
    返回 plan 或 None; plan["matched"] = [(后台门店名, Sheet 名, 原因), ...]。
    """
    if not names:
        return None
    try:
        import openpyxl as _oxl

        from . import sheet_match
        _wb = _oxl.load_workbook(store["template"], read_only=True)
        try:
            _sheets = list(_wb.sheetnames)
        finally:
            _wb.close()
        _kw = ([store["keyword"]] if store.get("keyword") else []) + list(store.get("keywords") or [])
        _main = (cfg.get("defaults") or {}).get("main_sheets")
        # 模板里的「门店名映射表」优先(后端门店名逐字在里面, 比关键词猜可靠)
        _smap = sheet_match.load_store_map(store["template"], store.get("platform") or "")
        plan = (sheet_match.plan_matches(_sheets, sorted(names), _kw, main=_main, store_map=_smap)
                if _main else sheet_match.plan_matches(_sheets, sorted(names), _kw, store_map=_smap))
        for _nm, _sn, _why in _autocreate_store_sheets(sink, store["template"], plan,
                                                       _sheet_rules(cfg),
                                                       enabled=_autocreate_enabled(cfg)):
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


def _start_weather_prefetch(store, dates, holder):
    """后台线程预取天气 —— 纯 HTTP, **不碰 Playwright**, 所以能与浏览器阶段并行。

    为什么只能搬天气: Playwright 的 sync API 不能在两个线程里同时操作同一个 context,
    「一边等下载一边抓页面」做不到; 但天气/文件准备这类非浏览器工作可以叠在浏览器时间里。
    """
    import threading

    def _run():
        try:
            holder["map"] = {d: weather_mod.fetch(d, store.get("lat", 28.187),
                                                  store.get("lon", 112.921)) for d in dates}
        except Exception as e:
            holder["err"] = str(e)[:80]

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    holder["thread"] = t
    return t


def _take_weather_map(holder, dates, store, sink=None):
    """取预取结果(最多等 90s); 没有/失败就原地串行抓一次。返回 {日期: (天气, 温度)}。"""
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
    return {d: weather_mod.fetch(d, store.get("lat", 28.187), store.get("lon", 112.921))
            for d in dates}


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
                             dry_run=True, enabled=True):
    """匹配不到 Sheet 的门店 → 按**地名关键字**决定要建哪张 Sheet → 并入 matched。

    用户口径: 没有对应 Sheet 就建一个(复刻前面的表头), 再写数据; Sheet 名只取地名关键字
    (如「长沙万家丽广场七楼店」→「万家丽广场七楼」)。返回 [(门店, Sheet, 原因)]。

    `dry_run=True`(默认) **只决定 Sheet 名, 不在磁盘上建表** —— 真正建表交给
    `fill.fill_many(..., create_missing=True)` 在批量写入时一次做完。原因: 逐店
    ensure_sheet() 是「每店一次读+存」(京东 22 店实测白多花约 150s)。
    """
    from . import sheet_match
    made = []
    used = {sheet_match.norm(s) for s in (plan.get("candidates") or [])}
    left = []
    if not enabled:          # 关掉自动建表 → 匹配不到的门店保持"未写入"
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


def run_task(cfg, store, date=None, date_from=None, sink=None, human=None,
             skip_browser=False, llm_client=None):
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
    # (京东实测串行天气 13.3s)。淘宝不预取: 它的天气要报表里的「营业时段」。
    wx_holder = {}
    if platform in ("jd", "meituan_gj"):
        _start_weather_prefetch(store, dates, wx_holder)
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

            def build_adapter(actx):
                """建适配器并完成登录。返回 (adapter, ok)。首次启动与重开浏览器都走这里。"""
                if platform in ("meituan", "jd", "meituan_gj"):
                    extra = {}
                    if platform == "meituan_gj":
                        # 多门店: 只抓模板里有对应 Sheet 的门店(模板是权威, 按关键词匹配)。
                        # 但若开了「自动建 Sheet」(默认开) → 新店必须被抓到才能建表, 故不过滤。
                        if (cfg.get("defaults") or {}).get("auto_create_store_sheet") is False:
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
                    cred = store.get("credentials") or {}
                    ok = a.ensure_login(username=cred.get("username", ""),
                                        password=cred.get("password", ""))
                    return a, ok
                a = TaobaoAdapter(actx, sink, human, store_id=store.get("id", ""),
                                  keyword=store.get("keyword", ""))
                if not a._logged_in():
                    plat_cfg = (cfg.get("platforms") or {}).get("taobao") or {}
                    u = plat_cfg.get("username") or ""
                    p_ = plat_cfg.get("password") or ""
                    if not (u and p_):
                        cred = store.get("credentials") or {}
                        u, p_ = cred.get("username", ""), cred.get("password", "")
                    from .autologin import auto_login
                    if not auto_login(a, u, p_, sink, human):
                        return a, False
                a._dismiss_popups()
                a._discover_shop_id()
                return a, True

            actx = launch_browser(pw, profile_path)
            ad, ok = build_adapter(actx)
            ad_holder["ad"] = ad
            if not ok:
                sink.result(False, error_code="E_LOGIN_TIMEOUT",
                            message=f"{_plat_label(platform)} 登录超时，请检查账号配置或手动登录", retryable=True)
                return None
            sink.log(f"✅ {_plat_label(platform)} 登录成功")

            def relaunch_browser(reason=""):
                """确定性恢复: 浏览器被关闭/配置目录被占用 → 重开 context(profile 保留登录态) + 重建适配器。
                不丢登录态, 因此不需要用户重新登录。"""
                nonlocal actx, ad
                sink.log(f"🔄 自动化浏览器不可用（{reason}），正在重开并复用登录态…")
                try:
                    actx.close()
                except Exception:
                    pass
                try:
                    actx = launch_browser(pw, profile_path)
                except Exception as e:
                    sink.log(f"⚠️ 浏览器重开失败: {str(e)[:100]}", level="warn")
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
                            sink.log(f"🏬 门店清单 {len(_stores)} 家: 营业中 {len(_open_stores)} 家 → 只抓这些; "
                                     f"跳过 {len(_rest)} 家(非营业中)")
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
            if user_skipped and not (reports and stats):
                _stop_browser()
                sink.result(False, error_code="E_USER_SKIP",
                            message="已按你的选择跳过该步骤，本次任务未产出报表。", retryable=True)
                return None
            if not reports:
                _stop_browser()
                sink.result(False, error_code="E_NO_REPORT",
                            message=(f"平台未生成 {date_from}~{date} 的报表。可能原因: ①报表生成中(稍后重试) "
                                     f"②区间内日期过早(平台只保留近6个月) ③登录态异常。详见上方步骤错误。"),
                            retryable=True)
                return None
            if not stats:
                _stop_browser()
                sink.result(False, error_code="E_CAPTURE_FAIL",
                            message="流量/推广数据抓取失败, 报表未写入。可点重试。", retryable=True)
                return None
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
            sink.result(False, error_code="E_NO_REPORT",
                        message=(f"美团报表内缺少这些日期: {', '.join(missing)}；"
                                 f"本次报表里实际有: {_have}（文件: {_ftxt}）。"
                                 f"平台日报通常次日才可下载；也可把区间改为已有数据的日期。"
                                 f"若实际日期与请求区间不符 → 多半是下载页日期没生效, 请重跑一次并看日志里"
                                 f"「📅 日期区间 = …」那行。"), retryable=False)
            return None
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

        # 3. 天气(逐日; 营业时段取自美团报表对应?列 → biz_hours)
        def do_weather():
            out = {}
            for d in dates:
                rr = all_rows.get(d) or []
                rr = rr if isinstance(rr, list) else [rr]
                out[d] = weather_mod.fetch(d, store.get("lat", 28.187), store.get("lon", 112.921),
                                           mt_map.biz_hours(rr[0] if rr else {}))
            return out
        wmap = timed("weather", "天气获取", do_weather) or {}
        for d in dates:
            if wmap.get(d) and wmap[d][0]:
                sink.log(f"[天气] {d} → {wmap[d][0]} {wmap[d][1]}")

        # 4. 填表(区间逐日; top 模式从最旧到最新依次插入 → 最新日期在第一行)
        mode = cfg.get("defaults", {}).get("fill_mode", "auto")
        # 一店一 Sheet(多店模式; 与美团管家同口径): 模板 Sheet 名=地名关键词, 逐店匹配后各自写入。
        # 报表 E~DF 区本就逐店一行 → 按门店名匹配 Sheet; 匹配不到就跳过(不写、不报错、不建 Sheet)。
        store_plan = None
        circle_by_sheet = {}
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
                    store_plan = (sheet_match.plan_matches(_sheets, sorted(_names), _kw, main=_main,
                                                          store_map=_smap)
                                  if _main else sheet_match.plan_matches(_sheets, sorted(_names), _kw,
                                                                        store_map=_smap))
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
                for _nm, _sn, _why in _autocreate_store_sheets(sink, store["template"], store_plan,
                                                               _sheet_rules(cfg)):
                    store_plan["matched"].append((_nm, _sn, _why))
                    sink.log(f"  🔗 门店「{_nm}」→ Sheet「{_sn}」（{_why}）")
            # 商圈Top: 仅**单门店视角**可得(wmPoiId=门店编号)。用户口径: 做单门店数据时才采集,
            # 全部门店模式不采集(Top 列留空)。抓不到就留空, 绝不臆造。
            circle_by_sheet = {}
            _cap_on = (cfg.get("defaults") or {}).get("capture_circle_top") is not False
            # 门槛必须**说明白为什么没跑**: 2026-09-28 用户报「TOP 还是没取到」时, 日志里
            # 一条商圈记录都没有 → 既不知道跑没跑, 也不知道卡在哪。这里逐个条件给理由。
            if not _cap_on:
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
                                f"单门店视角 × {len(store_plan['matched'])} 店 × {dates[-1]}")
                for _nm, _sn, _why in store_plan["matched"]:
                    _sid = _id_by_name.get(_nm) or ""
                    if not _sid:
                        sink.log(f"  · {_nm}: 报表里没有门店编号, 跳过商圈采集", level="warn")
                        continue
                    _capd = None
                    try:
                        _capd = _ad.capture_circle(_sid, date=dates[-1])
                    except Exception as e:
                        sink.log(f"  · {_nm}: 商圈采集异常 {str(e)[:80]}", level="warn")
                    if _capd:
                        circle_by_sheet[_sn] = _capd
                        _mm = mt_map.circle_write_set_all(_capd)
                        sink.log(f"  ✓ {_nm} → 商圈列 主站{len(_mm['main'])}/拼好饭{len(_mm['bao'])}"
                                 f"（口径 {_capd.get('bench') or '默认'}）")
                    else:
                        sink.log(f"  · {_nm}: 商圈数据未取到(该店 Top 列留空)", level="warn")
                sink.step_ok("circle", "采集商圈Top",
                             f"{len(circle_by_sheet)}/{len(store_plan['matched'])} 店取到商圈值")
            # 商圈采集是本任务最后一次用浏览器 → 到这里才真正释放
            _stop_browser()
        per_store_filled = []
        # 推广数据: 推广页仅支持「昨日」快捷口径 → 只填区间末日(dates[-1] 恒为最新一天,
        # top 模式填在第一行); 其余天推广列留空。页面日期与末日不一致时同样留空, 不错位填。
        promo = (stats or {}).get("promo") or {}
        promo_vals = {}
        if promo.get("spend") is not None and promo.get("date") == dates[-1]:
            promo_vals = {"CE": promo.get("spend"), "CF": promo.get("expose"),
                          "CG": promo.get("visit")}
        elif promo:
            sink.log(f"⚠️ 推广数据日期({promo.get('date')})与区间末日({dates[-1]})不一致, 推广列留空",
                     level="warn")
        filled_rows, fill_errs = [], []
        sink.step_start("fill", "填入模板", f"区间 {dates[0]}~{dates[-1]} 共{len(dates)}天 (美团 E~DF)")
        # 先算 write_set, 最后批量写一次(逐日逐店直接写 = 每店一轮读+写+存+回读)
        # 主数据表 = 默认解析(第 3 个 sheet; 可用 config defaults.main_sheet 覆盖),
        # 且若解析结果**同时是本次要写的门店 Sheet** → 撞车保护回退 Sheet2(汇总绝不能冲门店数据)
        _main, _fell_main = resolve_main(
            store["template"], cfg,
            [m[1] for m in ((store_plan or {}).get("matched") or [])])
        if _fell_main:
            sink.log(f"  ⚠️ 主数据表原解析为「{_fell_main}」但它是本次要写的门店表、或模板里的门店名映射表 → 汇总改写 "
                     f"{SHEET}(避免冲掉门店当天数据); 想固定某张表请设 defaults.main_sheet", level="warn")
        # 单门店模式: **必须**写关键词匹配到的门店表(匹配不到就按关键词新建), 绝不写主表(用户口径)
        _one_sheet = _single_store_sheet(store, cfg, store["template"]) if scope == "store" else None
        fill_jobs = []
        for d in sorted(dates):
            A, B = pick_pair(d)
            if A is None:
                fill_errs.append(f"{d}: 未匹配到门店行")
                continue
            ws = mt_map.build_write_set(A, B, weather=wmap.get(d))
            if d == dates[-1] and promo_vals:
                ws.update(promo_vals)
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
                        _ws2 = mt_map.build_write_set(_row, _brow, weather=wmap.get(d))
                        _c = circle_by_sheet.get(_sn)
                        if _c and d == dates[-1]:
                            _cw = mt_map.circle_write_set_all(_c)
                            _ws2.update(_cw["main"])
                            _ws2.update(_cw["bao"])
                        fill_jobs.append((_sn, d, _ws2))
                    except Exception as e:
                        fill_errs.append(f"[{_sn}] {d}: {str(e)[:70]}")
        _main_names = {SHEET} | {str(m) for m in ((cfg.get("defaults") or {}).get("main_sheets") or [])}
        try:
            _results = fill_many(store["template"], fill_jobs, mode=mode,
                                 main_sheets=(cfg.get("defaults") or {}).get("main_sheets"))
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
                        "商圈TOP": (f"已填 {len(circle_by_sheet)} 店（口径 "
                                    f"{next(iter(circle_by_sheet.values())).get('bench') or '默认'}）"
                                    if circle_by_sheet else
                                    "未采集(仅单门店视角可得：全部门店模式或单店漏斗不可用时留空)"),
                        "推广": (f"已填昨日值(费用{promo.get('spend')}/曝光{promo.get('expose')}/进店{promo.get('visit')})"
                                 if promo_vals else "未取到(CE~CG 留空; 推广页仅昨日口径)")})
        if per_store_filled:
            summary["一店一Sheet"] = (f"{len({s for _, s, _ in per_store_filled})} 个 Sheet / "
                                      f"{len(per_store_filled)} 次写入(逐日逐店)")
        if store_plan:
            if store_plan["unmatched"]:
                summary["未匹配模板(已跳过)"] = (f"{len(store_plan['unmatched'])} 家：" +
                                                "、".join(n for n, _ in store_plan["unmatched"][:6]))
            if store_plan["idle"]:
                summary["模板里未写入数据的Sheet"] = "、".join(store_plan["idle"][:8])
        if discovered:
            summary["discovered_stores"] = discovered
        outputs.append(store["template"])

        # 5. LLM 解读(区间末日; 只喂真实取到的列, 未抓取的流量明示留空)
        if llm_client and llm_client.configured and cfg.get("llm", {}).get("enabled", True):
            try:
                from .llm import daily_report
                last = dates[-1]
                a_last, b_last = pick_pair(last)
                wl = mt_map.build_write_set(a_last, b_last, weather=wmap.get(last))
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
                        "天气": (wmap.get(last) or (None, None))[0],
                        "商圈TOP流量": "PC端无数据源, 列留空(勿推测)",
                        "推广数据": "未抓取(消耗/曝光暂缺, 勿推测)"}
                text = daily_report(llm_client, data, on_delta=lambda t: sink.llm_delta("daily_report", t))
                sink.llm_done("daily_report")
                summary["llm_report"] = text
            except Exception as e:
                sink.log(f"⚠️ LLM 解读不可用(不影响数据): {str(e)[:100]}", level="warn")

        sink.result(ok=True, outputs=outputs, summary=summary)
        return {"outputs": outputs, "summary": summary, "dates": dates}

    # 1.6 京东支路: 门店报表 xlsx(每店每天一行) → 聚合行 → 模板 HI~HK(京东外卖数据)
    if platform == "jd":
        from .platforms import jd_mapping
        jd_rows = (stats or {}).get("rows") or {}
        if not jd_rows:      # skip_browser / 复用已有报表
            f = JDAdapter._find_jd_file(str(ddir), date_from, date)
            if f:
                jd_rows = JDAdapter._parse_jd_file(f)
        # 一店一 Sheet 用的逐店行(报表本就是一店一行, 聚合版把它们加成一个数)
        jd_rows_store = (stats or {}).get("rows_by_store") or {}
        if not jd_rows_store:
            _fs = JDAdapter._find_jd_file(str(ddir), date_from, date)
            if _fs:
                jd_rows_store = JDAdapter._parse_jd_rows(_fs)
        if not jd_rows:
            sink.result(False, error_code="E_NO_REPORT",
                        message=f"京东 {date_from}~{date} 报表无可用数据行(下载或解析失败)", retryable=True)
            return None
        missing = [d for d in dates if d not in jd_rows]
        if missing:
            sink.result(False, error_code="E_NO_REPORT",
                        message=f"京东报表内缺少这些日期: {', '.join(missing)}(收入数据每日10点更新, 可稍后重试)",
                        retryable=True)
            return None

        # 3. 天气(京东报表无营业时段, 传空)
        wmap = timed("weather", "天气获取",
                     lambda: _take_weather_map(wx_holder, dates, store, sink)) or {}
        for d in dates:
            if wmap.get(d) and wmap[d][0]:
                sink.log(f"[天气] {d} → {wmap[d][0]} {wmap[d][1]}")

        # 4. 填表(HI~HK) + 一店一 Sheet(与淘宝/美团同口径: Sheet 名=地名关键字, 缺则新建)
        mode = cfg.get("defaults", {}).get("fill_mode", "auto")
        filled_rows, fill_errs = [], []
        per_store_filled = []
        store_plan = None
        if scope != "store":
            store_plan = _plan_store_sheets(sink, store,
                                            _store_names_from_rows(jd_rows_store, dates), cfg)
        sink.step_start("fill", "填入模板", f"区间 {dates[0]}~{dates[-1]} 共{len(dates)}天 (京东 HI~HK)")
        # 先算 write_set, 最后批量写入一次(逐日逐店直接写 = 每店一轮读+写+存+回读)
        # 主数据表 = 默认解析(第 3 个 sheet; 可用 config defaults.main_sheet 覆盖),
        # 且若解析结果**同时是本次要写的门店 Sheet** → 撞车保护回退 Sheet2(汇总绝不能冲门店数据)
        _main, _fell_main = resolve_main(
            store["template"], cfg,
            [m[1] for m in ((store_plan or {}).get("matched") or [])])
        if _fell_main:
            sink.log(f"  ⚠️ 主数据表原解析为「{_fell_main}」但它是本次要写的门店表、或模板里的门店名映射表 → 汇总改写 "
                     f"{SHEET}(避免冲掉门店当天数据); 想固定某张表请设 defaults.main_sheet", level="warn")
        # 单门店模式: **必须**写关键词匹配到的门店表(匹配不到就按关键词新建), 绝不写主表(用户口径)
        _one_sheet = _single_store_sheet(store, cfg, store["template"]) if scope == "store" else None
        fill_jobs = []
        for d in sorted(dates):
            ws = jd_mapping.build_write_set(jd_rows.get(d), weather=wmap.get(d))
            blank = sorted(k for k, v in ws.items() if v is None)
            if blank:
                sink.log(f"  · {d} 无数据留空 {len(blank)} 列: {','.join(blank)}", level="warn")
            fill_jobs.append((_one_sheet or _main, d, ws))
            # 逐店行写进各自 Sheet(天气是城市级, 可以照传)
            if store_plan and store_plan.get("matched"):
                _srows = jd_rows_store.get(d) or []
                _srows = _srows if isinstance(_srows, list) else [_srows]
                for _nm, _sn, _why in store_plan["matched"]:
                    _row = next((r for r in _srows
                                 if isinstance(r, dict)
                                 and str(r.get("门店名称") or "").strip() == _nm), None)
                    if _row is None:
                        continue
                    fill_jobs.append((_sn, d, jd_mapping.build_write_set(_row, weather=wmap.get(d))))
        _main_names = {SHEET} | {str(m) for m in ((cfg.get("defaults") or {}).get("main_sheets") or [])}
        try:
            _results = fill_many(store["template"], fill_jobs, mode=mode,
                                 main_sheets=(cfg.get("defaults") or {}).get("main_sheets"))
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
        outputs.append(store["template"])

        # 5. LLM 解读(京东区数据少, 只喂实有字段)
        if llm_client and llm_client.configured and cfg.get("llm", {}).get("enabled", True):
            try:
                from .llm import daily_report
                last = dates[-1]
                wl = jd_mapping.build_write_set(jd_rows.get(last), weather=wmap.get(last))
                data = {"商家": store["name"], "日期": last, "平台": "京东到家",
                        "营业额": wl.get("HI"), "营业收入": wl.get("HJ"), "订单量": wl.get("HK"),
                        "天气": (wmap.get(last) or (None, None))[0]}
                text = daily_report(llm_client, data, on_delta=lambda t: sink.llm_delta("daily_report", t))
                sink.llm_done("daily_report")
                summary["llm_report"] = text
            except Exception as e:
                sink.log(f"⚠️ LLM 解读不可用(不影响数据): {str(e)[:100]}", level="warn")

        sink.result(ok=True, outputs=outputs, summary=summary)
        return {"outputs": outputs, "summary": summary, "dates": dates}

    # 1.7 美团管家支路: 多门店模式(一店一Sheet, Sheet名=门店名关键字; 脑图口径 HO~HT)
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
        plan = (sheet_match.plan_matches(all_sheets, list(stores.keys()), kw_cfg, main=main_sheets,
                                         store_map=_smap)
                if main_sheets else sheet_match.plan_matches(all_sheets, list(stores.keys()), kw_cfg,
                                                             store_map=_smap))
        # 匹配不到的店 → 按地名关键字决定 Sheet 名(真建表交给 fill_many 一次完成, 省掉逐店读写)
        for _nm, _sn, _why in _autocreate_store_sheets(sink, store["template"], plan,
                                                       _sheet_rules(cfg),
                                                       enabled=_autocreate_enabled(cfg)):
            plan["matched"].append((_nm, _sn, _why))
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
                        f"区间 {dates[0]}~{dates[-1]} 共{len(dates)}天 × {len(plan['matched'])} 店 (美团管家 HO~HT)")
        # 先算 write_set(纯计算, 不碰文件), 最后**批量写一次** —— 与淘宝/京东/美团外卖同口径。
        # 原来每店每天各自 fill_template = 每店一轮读+写+存+回读, 31 家店白花几分钟。
        # 主数据表 = 默认解析(第 3 个 sheet; 可用 config defaults.main_sheet 覆盖),
        # 且若解析结果**同时是本次要写的门店 Sheet** → 撞车保护回退 Sheet2(汇总绝不能冲门店数据)
        _main, _fell_main = resolve_main(
            store["template"], cfg,
            [m[1] for m in (plan.get("matched") or [])])
        if _fell_main:
            sink.log(f"  ⚠️ 主数据表原解析为「{_fell_main}」但它是本次要写的门店表、或模板里的门店名映射表 → 汇总改写 "
                     f"{SHEET}(避免冲掉门店当天数据); 想固定某张表请设 defaults.main_sheet", level="warn")
        # 单门店模式: **必须**写关键词匹配到的门店表(匹配不到就按关键词新建), 绝不写主表(用户口径)
        _one_sheet = _single_store_sheet(store, cfg, store["template"]) if scope == "store" else None
        fill_jobs = []
        for name, sn, _why in plan["matched"]:
            try:
                ws = gj_mapping.build_write_set(stores[name], weather=wmap.get(dates[-1]))
                for d in sorted(dates):      # 升序: top 模式下最新一天落在表头下第一行(与其它支路一致)
                    fill_jobs.append((sn, d, ws))
            except Exception as e:
                fill_errs.append(f"{name}: {str(e)[:80]}")
        try:
            _results = fill_many(store["template"], fill_jobs, mode=mode,
                                 main_sheets=(cfg.get("defaults") or {}).get("main_sheets"))
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

    # 3. 天气(区间逐日)
    def do_weather():
        out = {}
        for d in dates:
            av = all_rows.get(d)
            first = (av if isinstance(av, list) else [av])[0]
            biz = first.get("设置营业时间段")
            biz = biz if isinstance(biz, str) and "-" in biz else None
            out[d] = weather_mod.fetch(d, store.get("lat", 28.187), store.get("lon", 112.921), biz)
        return out
    wmap = timed("weather", "天气获取", do_weather) or {}
    for d in dates:
        if wmap.get(d) and wmap[d][0]:
            sink.log(f"[天气] {d} → {wmap[d][0]} {wmap[d][1]}")

    # 注: 逐店抓取已移到**抓取步骤内部**(do_capture) —— 放这里时浏览器已被关掉。

    # 4. 填表(区间逐日; top 模式从最旧到最新依次插入 → 最新日期在第一行)
    from .platforms.taobao_mapping import build_write_set
    mode = cfg.get("defaults", {}).get("fill_mode", "auto")
    # 一店一 Sheet(与美团同口径): Sheet 名=地名关键字; 匹配不到 → 新建(复刻模板表头) 后写入。
    # 注意: 网页抓到的流量/推广是**账号级**, 只写主表, **不复制**到门店 Sheet。
    store_plan = None
    per_store_filled = []
    if scope != "store":
        store_plan = _plan_store_sheets(sink, store, _store_names_from_rows(all_rows_store, dates), cfg)
    filled_rows = []
    fill_errs = []
    sink.step_start("fill", "填入模板", f"区间 {dates[0]}~{dates[-1]} 共{len(dates)}天")
    # 先只**算**出每天/每店的 write_set(纯计算, 不碰文件), 最后一次性批量落盘。
    # 原因: 逐日逐店直接调 fill_template = 每天每店一轮「读模板+写+存+回读」(京东 22 店 1 天
    # 实测 316s); 批量化后整任务 1 次读 + 1 次写 + 1 次存 + 1 次回读(见 fill.fill_many)。
    # 主数据表 = 默认解析(第 3 个 sheet; 可用 config defaults.main_sheet 覆盖) + 撞车保护
    _main, _fell_main = resolve_main(store["template"], cfg,
                                     [m[1] for m in ((store_plan or {}).get("matched") or [])])
    if _fell_main:
        sink.log(f"  ⚠️ 主数据表原解析为「{_fell_main}」但它是本次要写的门店表、或模板里的门店名映射表 → 汇总改写 "
                 f"{SHEET}(避免冲掉门店当天数据); 想固定某张表请设 defaults.main_sheet", level="warn")
    # 单门店模式: **必须**写关键词匹配到的门店表(匹配不到就按关键词新建), 绝不写主表(用户口径)
    _one_sheet = _single_store_sheet(store, cfg, store["template"]) if scope == "store" else None
    fill_jobs = []
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
        write_set = build_write_set(A, B, day_stats if day_stats else stats, weather=wmap.get(d))
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
                    _cap = (ad_holder.get("per_store_caps") or {}).get(_nm) if d == dates[-1] else None
                    fill_jobs.append((_sn, d, build_write_set(_row, _brow, _cap, weather=wmap.get(d))))
                except Exception as e:
                    fill_errs.append(f"[{_sn}] {d}: {str(e)[:70]}")
    _main_names = {SHEET} | {str(m) for m in ((cfg.get("defaults") or {}).get("main_sheets") or [])}
    try:
        _results = fill_many(store["template"], fill_jobs, mode=mode,
                             main_sheets=(cfg.get("defaults") or {}).get("main_sheets"))
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
    if discovered:
        summary["discovered_stores"] = discovered
    outputs.append(store["template"])

    # 5. LLM 解读(用区间末日数据)
    if llm_client and llm_client.configured and cfg.get("llm", {}).get("enabled", True):
        try:
            from .llm import daily_report
            last = dates[-1]
            A_last = pick_rows(last)[0]
            write_last = build_write_set(A_last, pick_rows(last)[1] or {}, stats or {}, weather=wmap.get(last))
            data = {"商家": store["name"], "日期": last,
                    "营业额": write_last.get("DG"), "订单": write_last.get("DI"),
                    "主站曝光": write_last.get("DN"), "TOP曝光": write_last.get("DO"),
                    "主站进店": write_last.get("DQ"), "TOP进店": write_last.get("DR"),
                    "天气": wmap.get(last, (None, None))[0]}
            text = daily_report(llm_client, data, on_delta=lambda t: sink.llm_delta("daily_report", t))
            sink.llm_done("daily_report")
            summary["llm_report"] = text
        except Exception as e:
            sink.log(f"⚠️ LLM 解读不可用(不影响数据): {str(e)[:100]}", level="warn")

    sink.result(ok=True, outputs=outputs, summary=summary)
    return {"outputs": outputs, "summary": summary, "dates": dates}
