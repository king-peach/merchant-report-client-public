# -*- coding: utf-8 -*-
"""填表引擎: 报表 → 运营模板。
移植自已验证的 fill_taobao_report.py(2026-09-16 e2e): 插入行(最新在第一行)/
全区公式引用平移/公式列保护/整行边框/交叉校验/回读验证。平台无关, 映射表由调用方传入。"""
import glob
import os
import re
import subprocess
import time
from datetime import datetime, timedelta

import openpyxl
from openpyxl.utils import get_column_letter as GL

WEEK = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
DATE_FMT = 'm"月"d"日"'
EPOCH = datetime(1899, 12, 30)
SHEET = "Sheet2"

# 可能**持有 xlsx 句柄**的 Office 进程(小写比较)。WPS 系常驻托盘 —— 用户"关窗"≠释放句柄
# (2026-09-13 真机: 用户以为关了, 保存仍 Permission denied, 整批零写入)。
OFFICE_PROC_NAMES = ("excel.exe", "et.exe", "wps.exe", "wpp.exe",
                     "wpscloudsvr.exe", "wpscenter.exe")
# 保存占用时的**阶梯等待**(秒): 共约 82 秒。给用户"去关掉文件"留出真实时间 ——
# 旧实现只重试 3 次共 ~6 秒, 用户还没来得及反应就已经整批放弃(零写入)。
SAVE_RETRY_DELAYS = (2, 5, 10, 15, 20, 30)
SAVE_RETRY_TOTAL_S = sum(SAVE_RETRY_DELAYS)

_sleep = time.sleep          # 可注入(测试用假 sleep 瞬时化, 绝不在单测里真等几十秒)
_OFFICE_PROC_CACHE = (0.0, None)     # (时间戳, 结果) —— 等待循环里每次 poll 都扫 tasklist 太贵


def find_office_processes(timeout_s=6.0):
    """扫一遍系统进程, 找出**可能占着模板**的 Office 进程 → [(显示名, pid), ...]。

    返回三态(语义必须区分, 下游文案不同):
      []    = 探测可用, 但确实没有 Office 进程在跑(占用另有其因: 预览窗格/网盘/杀软)
      None  = 探测不可用(非 Windows / tasklist 异常 / 超时)
    为什么点名进程: 用户看到"被占用"只会去关窗口, 而 WPS 常驻托盘、句柄不释放 ——
    必须直接告诉他是**哪个进程**(EXCEL.EXE/ET.EXE)还活着, 他才知道去哪儿关/要不要重启电脑。
    结果缓存约 8 秒: 等待循环里每 5 秒 poll 一次, 不缓存就是反复起 tasklist 子进程。
    """
    global _OFFICE_PROC_CACHE
    if os.name != "nt":
        return []
    now = time.time()
    ts, val = _OFFICE_PROC_CACHE
    if val is not None and (now - ts) < 8.0:
        return val
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        p = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True,
                           text=True, timeout=timeout_s, creationflags=flags)
        out = p.stdout or ""
        procs = []
        for line in out.splitlines():
            parts = [c.strip().strip('"') for c in line.split('","')]
            if len(parts) < 2:
                continue
            name, pid = parts[0].strip('"'), parts[1].strip('"')
            if name.lower() in OFFICE_PROC_NAMES:
                procs.append((name, int(pid) if pid.isdigit() else pid))
        _OFFICE_PROC_CACHE = (now, procs)
        return procs
    except Exception:
        _OFFICE_PROC_CACHE = (now, None)
        return None


def occupancy_hint():
    """占用失败文案的**尾巴**: 直接点名"是谁占着"或给出"没人占但就是写不进"的对策。"""
    procs = find_office_processes()
    if procs:
        names = ", ".join(f"{n.upper()}(PID {pid})" for n, pid in procs)
        return (f"（检测到: {names}。请关闭该文件 —— "
                f"WPS 需从右下角托盘完全退出，实在不行重启电脑）")
    if procs is None:
        return "（进程探测不可用）"
    return ("（未检测到 Excel/WPS 进程 —— 可能是资源管理器预览窗格/网盘同步/杀毒软件占用，"
            "可尝试关闭预览窗格后重试）")


def ensure_writable(path, what="模板"):
    """写入前自检: 不存在 / 只读 / 被独占(Windows 上最常见 = 正在 Excel 或 WPS 里打开)。

    为什么必须有: 2026-10-01 用户在 Windows 上的真实报错 —— 采集跑了几分钟, **最后保存时**才炸
    `[Errno 13] Permission denied: 'D:\\prod\\商家报表助手\\运营报表模板.xlsx'`,
    用户既不知道原因、也没有可操作提示。现在: 开工前预检(秒级失败) + 保存时给明确原因。
    ⚠️ 占用提示必须放在**最前面**: 下游多处对 message 做 [:120] 截断, 关键信息(谁占着/怎么办)
    一旦排到后面就会被截掉, 用户又只剩一句"写不进去"。
    """
    p = str(path)
    if not os.path.exists(p):
        return False, f"{what}不存在: {p}"
    if not os.access(p, os.W_OK):
        return False, f"{what}是只读文件(去掉只读属性后重试): {p}"
    try:
        with open(p, "r+b"):
            pass
        return True, "ok"
    except OSError as e:
        # Windows 上"被占用/只读/ACL 拒绝"本来就是同一个 errno 13, 区分不了 → 统一走这条路文案
        return False, (f"{what}正被其他程序占用{occupancy_hint()} —— "
                       f"最常见的是它正开在 Excel/WPS 里。请关闭该文件后重试。路径: {p}（{e}）")


def wait_writable(path, what="模板", total_s=60, poll_s=5, log=None, _sleep_fn=None):
    """开着文件也能继续: 等用户把 Excel/WPS 关掉, **释放即自动继续**(最多 total_s 秒)。

    为什么要有: 2026-09-14 真机 —— 4 平台 + 管家/京东整天全部倒在开工前检查(模板被占用),
    用户当时正开着模板看数, 只要等十几秒他关掉就能跑; 直接失败 = 一整天白等。
    返回 True=已可写(或等待中释放), False=超时仍未释放。自身异常一律不许炸任务(放行)。
    """
    sleep = _sleep_fn or _sleep
    if log:
        try:
            log(f"⏳ 模板被占用{occupancy_hint()}，已自动等待 0/{int(total_s)} 秒（关掉文件后自动继续）")
        except Exception:
            pass
    waited = 0.0
    while waited < total_s:
        sleep(poll_s)
        waited += poll_s
        try:
            ok, _why = ensure_writable(path, what)
        except Exception:
            return True          # 探测本身出问题 → 不拦任务, 交回原有逻辑
        if ok:
            if log:
                try:
                    log(f"✅ 模板已可写（等待 {int(waited)} 秒后释放）")
                except Exception:
                    pass
            return True
        if waited < total_s and log:
            try:
                log(f"⏳ 模板被占用{occupancy_hint()}，已自动等待 {int(waited)}/{int(total_s)} 秒"
                    f"（关掉文件后自动继续）")
            except Exception:
                pass
    return False


def save_wb(wb, path, attempts=None, delay=None, on_retry=None, log=None, slim=None):
    """保存工作簿; Windows 上被 Excel 占用时是 PermissionError(errno 13) → **阶梯等待**后给明确错误。

    为什么改阶梯(2026-10-15): 旧实现 3 次 × 2s ≈ 6 秒就放弃 —— 用户根本来不及去关文件
    (2026-09-13 美团外卖: 数据全抓到, 最后保存被占用 → 整批放弃、零写入)。
    现在共等约 82 秒, 期间通过 on_retry 把"还差多少秒/谁占着"实时打给用户;
    attempts/delay 仅作向后兼容保留(显式传参时退化为等间隔重试)。

    模板瘦身(M5, 2026-10-15): 保存前把"无值但带样式"的越界空格剪掉(WPS 整行套格式遗留),
    否则 21.6MB 的模板会原样写出。**只在第一次保存尝试前剪一次** —— 幂等, 重试阶梯里不重复
    扫几百万格(见 xlsx_slim.slim_workbook)。slim=None 表示按开关自动判定(默认开);
    slim=False 强制不剪(测试/救急用)。
    """
    if attempts is not None or delay is not None:      # 旧调用方式: 等间隔
        delays = [float(delay if delay is not None else 2.0)] * max(0, int(attempts or 3) - 1)
    else:
        delays = list(SAVE_RETRY_DELAYS)
    _slim_before_save(wb, log=log, slim=slim)
    last = None
    waited = 0.0
    for i, d in enumerate(delays + [None]):
        try:
            wb.save(path)
            return True
        except OSError as e:
            if not isinstance(e, PermissionError) and getattr(e, "errno", None) != 13:
                raise
            last = e
            if d is None:
                break
            waited += d
            if on_retry:
                try:
                    on_retry(f"⏳ 保存被占用（第 {i + 1}/{len(delays)} 次重试，"
                             f"已等待约 {int(waited)} 秒）{occupancy_hint()}")
                except Exception:
                    pass
            _sleep(d)
    ok, why = ensure_writable(path, "模板")
    raise RuntimeError(f"保存模板失败: {'文件被占用 → 关闭 Excel/WPS 后重试' if ok else why} [{last}]\n"
                       f"已自动重试约 {int(SAVE_RETRY_TOTAL_S)} 秒仍未释放。{occupancy_hint()}")


def _slim_before_save(wb, log=None, slim=None):
    """保存前瘦身(幂等; 已瘦过的 wb 再调 removed==0)。返回统计 dict 或 None(未启用/失败)。

    瘦身是**优化**不是功能 —— 任何异常都必须放行原保存流程(宁可文件大, 不能保存不了);
    但也不静默: 有 log 出口时说明一句"跳过 + 原因", 免得"瘦身坏了"永远没人知道。
    """
    try:
        from .xlsx_slim import slim_enabled, slim_workbook
        if slim is None:
            slim = slim_enabled()
        if not slim:
            return None
        st = slim_workbook(wb)
    except Exception as e:
        if log:
            try:
                log(f"⚠️ 模板瘦身跳过（{type(e).__name__}: {str(e)[:80]}），按原样保存")
            except Exception:
                pass
        return None
    if st.get("removed") and log:
        try:
            log(f"🧹 模板瘦身: 剪掉 {st['removed']} 个空格式格（{st['sheets_touched']} 张表）")
        except Exception:
            pass
    return st


def pick_base_sheet(wb, base_sheet=None, main_sheets=None):
    """选「复制结构用的样板表」= 一张**真门店表**(新建门店 Sheet 复刻它的结构)。

    用户口径(2026-09-29 A 方案): 模板第 **3** 个位置放**汇总/主数据表**(非门店表), 门店表从
    后面开始 —— 所以样板不能盲取第 3 个(那会把门店 Sheet 复刻成汇总表的结构!), 要**跳过主表**:
      ① 调用方明确指定的 base_sheet
      ② 第 3 个起第一张**不是主数据表**的 sheet(真门店表)
      ③ Sheet2(老模板兼容: 只有主表+一两个 sheet 时沿用历史样板, 行为不变)
      ④ 第一个非主表 sheet → 第一个 sheet
    """
    if base_sheet and base_sheet in wb.sheetnames:
        return base_sheet
    sns = list(wb.sheetnames)
    main = main_sheet_in(wb)
    mains = {str(m) for m in (main_sheets or [])} | {main}
    for s in sns[2:]:
        if s not in mains:
            return s
    if SHEET in sns and SHEET not in {str(m) for m in (main_sheets or [])}:
        return SHEET
    cand = [s for s in sns if s not in mains]
    return (cand or sns or [None])[0]


def ensure_sheet(template_path, name, base_sheet=None, main_sheets=None):
    """门店 Sheet 不存在时**新建**(复刻样板表的整表结构: 表头/公式/列宽)。

    用户口径: 匹配不到 Sheet 就建一个, 复刻**第 3 个 sheet**(不是 Sheet2, 见 pick_base_sheet),
    再写数据; 新建只新增, **不改动已有 Sheet**。返回 True=已新建, False=已存在或失败。
    """
    name = str(name or "").strip()
    if not name:
        return False
    try:
        wb = openpyxl.load_workbook(template_path)
    except Exception:
        return False
    try:
        if name in wb.sheetnames:
            return False
        base = pick_base_sheet(wb, base_sheet, main_sheets)
        if not base:
            return False
        ws = wb.copy_worksheet(wb[base])
        ws.title = name
        _clear_data_area(ws)          # 关键: 别把样板表当天的数据复刻进新表
        save_wb(wb, template_path)
        return True
    except Exception:
        return False
    finally:
        try:
            wb.close()
        except Exception:
            pass
DATE_COL = "A"
ROW_START = 6          # 默认数据起始行(常规门店表); 结构不同的表用 data_start_row(ws) 现算
SCAN_MAX_COL = 220
SCAN_ROWS = 45


def data_start_row(ws):
    """该 Sheet 的**首个数据行**行号 —— 模板里各表结构并非完全一致。

    为什么必须现算(2026-10-14 核实): 模板 19 个门店表里「天麓」是**旧结构** —— 表头(含「日期」)
    在第 4 行、数据从第 5 行开始(其余 18 张是表头第 5 行、数据第 6 行, 首行日期 2026-04-01)。
    写死 ROW_START=6 会让天麓:
      · auto 模式扫不到第 5 行那个已有日期 → 同一天被**追加成第二行**(重复日期);
      · top 模式把新行插到第 6 行 → 第 5 行的旧日期反而留在"最新在第一行"的位置之上。
    做法: 在 A 列找「日期」表头行 h, 且 h+1 行的 A 列**确实是日期**(序列号/datetime/字符串),
    取 h+1; 数据区被清空的表(表头下一行还没日期)用「找到的第一个日期表头 + 1」兜底;
    两个线索都没有就退回 ROW_START(老行为, 其它调用方/合成夹具完全不受影响)。
    """
    hdr = None
    try:
        max_r = min(int(ws.max_row or 0), 20)
        for h in range(1, max_r):
            if str(ws.cell(row=h, column=1).value or "").strip() != "日期":
                continue
            nxt = ws.cell(row=h + 1, column=1).value
            if isinstance(nxt, (int, float, datetime)) or _parse_date(nxt):
                return h + 1
            if hdr is None:
                hdr = h
    except Exception:
        pass
    return (hdr + 1) if hdr else ROW_START


def _parse_date(v):
    """A 列值 → 'YYYY-MM-DD'; 认不出 → None(Excel 序列号/datetime/字符串三吃)。"""
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d")
    if isinstance(v, (int, float)):
        try:
            return (EPOCH + timedelta(days=int(v))).strftime("%Y-%m-%d")
        except (OverflowError, ValueError):
            return None
    if isinstance(v, str):
        try:
            return datetime.strptime(v.strip()[:10], "%Y-%m-%d").strftime("%Y-%m-%d")
        except ValueError:
            return None
    return None


def num(v):
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = re.match(r"^\s*([\d.]+)小时([\d.]+)分?\s*$", str(v))
    if m:
        return round(float(m.group(1)) + float(m.group(2)) / 60, 4)
    try:
        return float(str(v).replace(",", "").replace("%", ""))
    except ValueError:
        return str(v)


# ---------------- 报表读取(平台适配器产出标准 dict 后由 mapping 消费) ----------------
def broken_report_files(directory, date, date_to=None, prefix="门店下载_"):
    """本目录里"看着像报表但读不了"的文件 → [(文件名, 原因)]。

    存在意义: 平台"任务已提交但文件还没生成好"时会落地 0 字节文件, 若不区分,
    用户只会看到"缺「全部」报表"这种误导性报错(2026-09-23 实测踩到)。
    """
    key_from = date.replace("-", "")
    key_to = (date_to or date).replace("-", "")
    out = []
    for fp in sorted(glob.glob(os.path.join(directory, f"{prefix}*.xlsx"))):
        base = os.path.basename(fp)
        if key_from != key_to:
            ok = (f"{key_from}至{key_to}" in base) or (key_from in base and key_to in base)
        else:
            ok = key_from in base
        if not ok:
            continue
        try:
            size = os.path.getsize(fp)
        except Exception:
            continue
        if size < 1024:
            out.append((base, f"只有 {size} 字节(空文件)"))
            continue
        try:
            openpyxl.load_workbook(fp, data_only=True)["data"]
        except Exception as e:
            out.append((base, f"不是有效报表({str(e)[:36]})"))
    return out


# ── 报表文件「该选哪一份」───────────────────────────────────────────────
# 平台给的文件名形如: 门店下载_20260917至20260922_全部门店_1111111111_20260923164541553.xlsx
# 结尾那串是**下载时间戳**(2026-09-23 16:45:41), 不是报表日期! 旧逻辑「文件名含 20260923
# 就算 09-23 的报表」会把 09-17~09-22 的旧文件当成 09-23 的 → 文件里根本没有 09-23 的行,
# 报「报表内缺少这些日期的数据行」(2026-09-24 用户实测踩到)。
# 两条对策:
#   ① 下载落地时按**内容表头签名**改成语义化规范名: 淘宝_{类型}_{from}_{to}_{平台原名}
#   ② 选文件时对**内容**校验请求日期 —— 名字只是线索, 内容才是真相。
CANON_PREFIX = "淘宝_"


def report_kind(path):
    """按 data 表表头签名判报表类型: '全部' / '爆品团' / ''(认不出或坏文件)。"""
    try:
        ws = openpyxl.load_workbook(path, data_only=True)["data"]
        hdr = {ws.cell(row=1, column=c).value for c in range(1, ws.max_column + 1)}
    except Exception:
        return ""
    if "满减活动订单数" in hdr:
        return "全部"
    if "曝光人数" in hdr:
        return "爆品团"
    return ""


def canonical_report_name(kind, date_from, date_to, original):
    """我方给下载文件起的规范名(保留平台原名便于追溯)。"""
    a = str(date_from or "").replace("-", "")
    b = str(date_to or date_from or "").replace("-", "")
    return f"{CANON_PREFIX}{kind or '未知'}_{a}_{b}_{original}"


def parse_canonical_name(name):
    """规范名 → (类型, from, to); 非规范名返回 None。"""
    m = re.match(r"^淘宝_(全部|爆品团|未知)_(\d{8})_(\d{8})_", name)
    return (m.group(1), m.group(2), m.group(3)) if m else None


def platform_range_in_name(name):
    """平台原名里**实际提交的区间**: 门店下载_20260929至20260929_... → ('2026-09-29','2026-09-29')。

    用途: 校验"下回来的这份到底是不是我要的那天"。2026-09-30 真机: 请求 09-28, 平台生成的是
    09-29(下载页日期没生效) → 若用**请求区间**给它起规范名, 会把别的日期的报表伪装成本次报表。
    """
    m = re.search(r"(20\d{2})(\d{2})(\d{2})至(20\d{2})(\d{2})(\d{2})", str(name or ""))
    if not m:
        return None
    g = m.groups()
    return (f"{g[0]}-{g[1]}-{g[2]}", f"{g[3]}-{g[4]}-{g[5]}")


def downloaded_but_unusable(directory, date, date_to=None, limit=4, prefix="门店下载_"):
    """「缺报表」报错自证用: 目录里**最新下到的报表文件**及其真实日期/大小/类型。

    为什么需要: 2026-09-30 真机的报错是「缺全部/爆品团报表（已有: 无）」, 可目录里明明躺着
    两份刚下好的 18KB/10KB 报表 —— 只是平台表单日期没生效, 内容与文件名都是 09-29(请求 09-28)。
    把这份清单打进报错, 用户一眼就能看出"不是没下到, 是下错了日期"。
    """
    try:
        items = [c for c in report_file_candidates(directory, date, date_to, prefix)
                 if c["size"] >= 1024]
    except Exception:
        return []
    items.sort(key=lambda c: os.path.getmtime(c["path"]), reverse=True)
    out = []
    for c in items[:limit]:
        rng = platform_range_in_name(c["name"])
        out.append({"name": c["name"], "kb": c["size"] // 1024, "kind": c["kind"] or "认不出",
                    "content_dates": dates_span_text(c["dates"]),
                    "named_range": f"{rng[0]}~{rng[1]}" if rng else "未标注",
                    "covers": c["covers"]})
    return out


def unusable_report_hint(items):
    """下载清单 → 一行人话(给报错文案用)。"""
    if not items:
        return "目录里没有任何可用大小的报表文件(平台可能根本没生成)"
    parts = []
    for it in items[:3]:
        flag = "内容含请求日期" if it["covers"] else "内容不含请求日期 ✗"
        parts.append(f"{it['name'][:40]}（{it['kind']}, {it['kb']}KB, 名字区间 {it['named_range']}, "
                     f"内容 {it['content_dates']}, {flag}）")
    return "；".join(parts)


def _file_dates(path):
    """文件内容里实际含有的日期集合(YYYY-MM-DD); 坏文件返回 set()。"""
    try:
        return set(load_report_rows(path).keys())
    except Exception:
        return set()


def _shops_in(path):
    """报表里的门店数(第 3 列门店 id 去重) —— 品牌账号会为每店各出一份, 混入单店报表
    会让聚合营业额=0, 所以同类多份要挑门店数最多的。"""
    try:
        ws = openpyxl.load_workbook(path, data_only=True)["data"]
        ids = set()
        for r in range(2, min(ws.max_row, 500) + 1):
            v = ws.cell(row=r, column=3).value
            if v:
                ids.add(str(v))
        return len(ids)
    except Exception:
        return 0


def report_file_candidates(directory, date, date_to=None, prefix="门店下载_"):
    """列出目录里所有**可能是本次报表**的文件及其真相(给选文件与失败报错共用):
    {"path","name","kind","canonical","size","dates","covers"}
    covers = 文件内容是否真的含有请求日期(单日=该日; 区间=含起始日)。
    """
    out = []
    for fp in sorted(glob.glob(os.path.join(directory, "*.xlsx"))):
        name = os.path.basename(fp)
        if not parse_canonical_name(name) and not name.startswith(prefix):
            continue
        try:
            size = os.path.getsize(fp)
        except OSError:
            continue
        good = size >= 1024
        dates = _file_dates(fp) if good else set()
        if date_to and date_to != date:
            covers = bool(dates) and date in dates
        else:
            covers = bool(dates) and date in dates
        out.append({"path": fp, "name": name, "size": size,
                    "kind": report_kind(fp) if good else "",
                    "canonical": bool(parse_canonical_name(name)),
                    "dates": sorted(dates), "covers": covers})
    return out


def dates_span_text(dates):
    """['2026-09-17',...] → '2026-09-17~2026-09-22' (给报错用的人话)"""
    if not dates:
        return "无(空表或解析失败)"
    return dates[0] if len(dates) == 1 else f"{dates[0]}~{dates[-1]}({len(dates)}天)"


def find_report_files(directory, date, date_to=None, prefix="门店下载_"):
    """挑出 (全部, 爆品团) 两份报表路径。挑法:
    ① 我方规范名 → 按类型 + 区间**精确**命中(不看名字里别的数字);
    ② 平台原名 → 名字匹配 **且内容真的含请求日期**;
    ③ 同类型多份 → 优先规范名, 否则取门店数最多的那份。
    返回 (all_path, bom_path), 任一缺失为 None。"""
    key_from = date.replace("-", "")
    key_to = (date_to or date).replace("-", "")

    def _name_ok(c):
        if c["canonical"]:
            _, a, b = parse_canonical_name(c["name"])
            return a == key_from and b == key_to
        name = c["name"]
        if key_from == key_to:
            return key_from in name
        return (f"{key_from}至{key_to}" in name) or (key_from in name and key_to in name)

    usable = [c for c in report_file_candidates(directory, date, date_to, prefix)
              if c["size"] >= 1024 and c["kind"] and _name_ok(c) and c["covers"]]
    all_f = _pick_best([c for c in usable if c["kind"] == "全部"])
    bom_f = _pick_best([c for c in usable if c["kind"] == "爆品团"])
    return (all_f["path"] if all_f else None), (bom_f["path"] if bom_f else None)


def _pick_best(cands):
    if not cands:
        return None
    canon = [c for c in cands if c["canonical"]]
    return max(canon or cands, key=lambda c: _shops_in(c["path"]))


def load_report_rows(path):
    """报表 → {标准化日期: {字段名: 原始值}}; 日期格式归一(20260915/2026-09-15/日期序列)。
    品牌账号报表每天多行(每店一行) → 同日期各店数值列**求和**(可加口径),
    文本列取首个非空; 否则最后一行覆盖前面, 聚合值错成"最后一家店"(2026-09-20 实测)。"""
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb["data"]
    headers = [ws.cell(row=1, column=c).value for c in range(1, ws.max_column + 1)]

    def _num(v):
        try:
            if v in (None, ""):
                return None
            return float(str(v).replace(",", ""))
        except (TypeError, ValueError):
            return None

    rows = {}
    for r in range(2, ws.max_row + 1):
        d = ws.cell(row=r, column=1).value
        if isinstance(d, str):
            d = d.strip().replace("/", "-")
            if re.match(r"^\d{8}$", d):
                d = f"{d[:4]}-{d[4:6]}-{d[6:]}"
        else:
            d = (EPOCH + timedelta(days=int(d))).strftime("%Y-%m-%d")
        key = str(d)[:10]
        cur = rows.get(key)
        if cur is None:
            rows[key] = {h: ws.cell(row=r, column=c).value for c, h in enumerate(headers, 1) if h}
            continue
        # 聚合: 数值可加则加, 文本取首个非空
        for c, h in enumerate(headers, 1):
            if not h:
                continue
            v = ws.cell(row=r, column=c).value
            nv = _num(v)
            ov = _num(cur.get(h))
            if nv is not None and ov is not None:
                cur[h] = ov + nv
            elif cur.get(h) in (None, "") and v not in (None, ""):
                cur[h] = v
    return rows


def load_report_rows_by_store(path):
    """报表 → {标准化日期: [逐店行 dict, ...]} —— **不做账号级聚合**。

    与 load_report_rows 的分工: 后者把同一天多行按「可加列求和」聚成一行(给主表写汇总用),
    代价是门店名只剩**文件首行那家** → 一店一 Sheet 的门店计划/逐店写入若用它, 永远只能匹配
    到 1 家店(2026-09-30 实测: 淘宝 17 家门店的报表, 模板里只有第 1 行那家 Sheet 被写入)。
    逐店场景必须用本函数(与京东 rows_by_store 同口径)。
    """
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb["data"] if "data" in wb.sheetnames else wb.active
    headers = [ws.cell(row=1, column=c).value for c in range(1, ws.max_column + 1)]
    out = {}
    for r in range(2, ws.max_row + 1):
        d = ws.cell(row=r, column=1).value
        if d in (None, ""):
            continue
        if isinstance(d, str):
            d = d.strip().replace("/", "-")
            if re.match(r"^\d{8}$", d):
                d = f"{d[:4]}-{d[4:6]}-{d[6:]}"
        else:
            try:
                d = (EPOCH + timedelta(days=int(d))).strftime("%Y-%m-%d")
            except (TypeError, ValueError):
                continue
        row = {h: ws.cell(row=r, column=c).value for c, h in enumerate(headers, 1) if h}
        if not str(row.get("门店名称") or "").strip():
            continue          # 汇总行/空行不算门店
        out.setdefault(str(d)[:10], []).append(row)
    return out


# ---------------- 映射求值 ----------------
def evaluate_mapping(mapping, all_row, bom_row):
    """mapping: [(模板列, spec, 说明)]; spec: ALL:字段 / BOM:字段 / CALC:字段-字段 / RATE:A/B
    返回 ({列: 值}, 明细列表)"""
    vals, detail = {}, []
    for col, spec, note in mapping:
        try:
            if spec.startswith("ALL:"):
                v = num(all_row.get(spec[4:]))
            elif spec.startswith("BOM:"):
                v = num(bom_row.get(spec[4:]))
            elif spec.startswith("CALC:"):
                f1, f2 = spec[5:].split("-")
                v1, v2 = num(all_row.get(f1)), num(bom_row.get(f2))
                v = None if v1 is None or v2 is None else round(v1 - v2, 4)
            elif spec.startswith("RATE:"):
                a, b = spec[5:].split("/")
                va, vb = vals.get(a), vals.get(b)
                v = None if not va or not vb else round(va / vb, 6)
            else:
                v = None
        except Exception:
            v = None
        vals[col] = v
        detail.append((col, note, v))
    return vals, detail


def cross_checks(vals):
    """交叉校验(淘宝口径): 营收/订单类(精确0.02); 流量类不在 write_set 时不校验(浏览器跳过场景)。
    DJ/DK/DL(主站营收, 修复后模板列位)是模板公式列(=全部-爆品团), write_set 恒为 None → 不能当数据校验,
    改校验恒等变形: 全部 − 爆品团 ≥ 0 且 爆品团 ≤ 全部(爆品团是全部的子集, 2026-09-20)。"""
    def ck(name, a, b, c, tol=0.02):
        if a is None:
            return (name, None)   # 无数据源(浏览器跳过), 不判失败
        diff = abs(a - ((b or 0) + (c or 0)))
        return (name, diff < tol or (a != 0 and diff / a < 0.10))

    def subset(name, total, part):
        if total is None or part is None:
            return (name, None)
        ok = part <= total * (1 + 0.10) and total >= 0
        return (name, ok)

    return [
        subset("营业额: 爆品团⊆全部", vals.get("DG"), vals.get("ET")),
        subset("收入: 爆品团⊆全部", vals.get("DH"), vals.get("EU")),
        subset("订单: 爆品团⊆全部", vals.get("DI"), vals.get("EV")),
    ]


# ---------------- 模板写入 ----------------
def scan_formula_cols(ws):
    fmap = {}
    start = data_start_row(ws)
    for c in range(1, SCAN_MAX_COL):
        for r in range(start, start + SCAN_ROWS):
            v = ws.cell(row=r, column=c).value
            if isinstance(v, str) and v.startswith("="):
                fmap[GL(c)] = (r, v)
                break
    return fmap


def shift_formula(formula, row):
    return re.sub(r"(?<![A-Za-z0-9_!$])([A-Z]{1,2})(\d{1,3})", lambda m: f"{m.group(1)}{row}", formula)


def shift_formula_by(formula, delta):
    """**相对平移**公式里的行号(全部 +delta)。

    追加行时从上一行复制公式用它: 同行引用 `=EZ44-FA44` → `=EZ45-FA45` ✓,
    跨行引用(如 `=SUM(EZ6:EZ44)`)也保持关系 ✓。
    (上面的 shift_formula 是把所有行号都换成同一行, 只适合"整行快照"那一种场景。)
    """
    if not isinstance(formula, str) or not formula.startswith("="):
        return formula
    return re.sub(r"(?<![A-Za-z0-9_!$])([A-Z]{1,2})(\d{1,3})",
                  lambda m: f"{m.group(1)}{int(m.group(2)) + delta}", formula)


def resolve_row(ws, target, mode="top"):
    """top=数据区首行上方插入新行(最新在第一行); 同日重跑原地更新。auto=按日期找行否则尾部追加。

    数据区首行 = data_start_row(ws) —— 常规门店表 6, 「天麓」这种旧结构表 5(见 data_start_row)。
    """
    start = data_start_row(ws)
    if mode == "top":
        anchor = ws[f"{DATE_COL}{start}"]
        old = anchor.value
        same_day = _parse_date(old) == target
        if not same_day:
            fmap_old = {}
            for c in range(1, SCAN_MAX_COL):
                v = ws.cell(row=start, column=c).value
                if isinstance(v, str) and v.startswith("="):
                    fmap_old[c] = v
            ws.insert_rows(start)
            new = start
            ws[f"{DATE_COL}{new}"] = datetime.strptime(target, "%Y-%m-%d")
            ws[f"{DATE_COL}{new}"].number_format = DATE_FMT
            nxt = ws[f"B{new + 1}"].value
            ws[f"B{new}"] = f'=TEXT({DATE_COL}{new},"AAAA")' if isinstance(nxt, str) and nxt.startswith("=") \
                else WEEK[datetime.strptime(target, "%Y-%m-%d").weekday()]
            # 全区公式引用平移: 引用行号>=new 的 +1
            shifted = 0
            for r in range(new, min(ws.max_row, new + SCAN_ROWS) + 1):
                for c in range(1, SCAN_MAX_COL):
                    v = ws.cell(row=r, column=c).value
                    if isinstance(v, str) and v.startswith("="):
                        nv = re.sub(r"(?<![A-Za-z0-9_!$])([A-Z]{1,2})(\d{1,3})",
                                    lambda m: f"{m.group(1)}{int(m.group(2)) + 1}"
                                    if int(m.group(2)) >= new else m.group(0), v)
                        if nv != v:
                            ws.cell(row=r, column=c).value = nv
                            shifted += 1
            # 新行公式(快照平移到本行) + 样式克隆
            for c, f0 in fmap_old.items():
                ws.cell(row=new, column=c).value = shift_formula(f0, new)
            from copy import copy as _copy
            for c in range(1, SCAN_MAX_COL):
                s = ws.cell(row=new + 1, column=c)
                if s.has_style:
                    ws.cell(row=new, column=c)._style = _copy(s._style)
            return new, f"top-inserted(引用平移{shifted}格)"
        return start, "top-overwrite(同日重跑)"
    for r in range(start, ws.max_row + 1):
        if _parse_date(ws[f"{DATE_COL}{r}"].value) == target:
            return r, "existing"
    last = start
    for r in range(start, ws.max_row + 1):
        if ws[f"{DATE_COL}{r}"].value is not None:
            last = r
    r = last + 1
    ws[f"{DATE_COL}{r}"] = datetime.strptime(target, "%Y-%m-%d")
    ws[f"{DATE_COL}{r}"].number_format = DATE_FMT
    prev_b = ws[f"B{last}"].value
    ws[f"B{r}"] = f'=TEXT({DATE_COL}{r},"AAAA")' if isinstance(prev_b, str) and prev_b.startswith("=") \
        else WEEK[datetime.strptime(target, "%Y-%m-%d").weekday()]
    # 追加行必须像普通数据行: 克隆上一行样式 + **相对平移复制公式**(否则模板的差异/ROI 等
    # 公式列在追加行是空的)。**不复制上一行的静态值** —— 那会把别的日期的数据串进来。
    from copy import copy as _copy
    for c in range(1, SCAN_MAX_COL):
        src = ws.cell(row=last, column=c)
        if src.has_style:
            ws.cell(row=r, column=c)._style = _copy(src._style)
        v = src.value
        if c != 1 and isinstance(v, str) and v.startswith("="):
            ws.cell(row=r, column=c).value = shift_formula_by(v, r - last)
    return r, f"appended(after {last})"


def apply_row_borders(ws, row, max_col=SCAN_MAX_COL):
    from openpyxl.styles import Border, Side
    thin = Side(style="thin")
    bd = Border(left=thin, right=thin, top=thin, bottom=thin)
    for c in range(1, max_col):
        ws.cell(row=row, column=c).border = bd


def _clear_data_area(ws, max_col=260, min_row=None):
    """复刻样板表后**清空数据区**(数据首行起, 除 A 列日期脚手架), 只留表头/公式/列宽。

    为什么必须清(2026-09-30 真机事故): 样板表 = 第 3 个起的真门店表, 任务跑到建新表时它
    **已经被本任务写过数据** → `copy_worksheet` 把那些数字一起复刻过去 →
    新建的 4 张门店表(坡子街/天马公寓/杜甫江阁/浏城桥)一开出来就带着「铁道」当天的
    营业额/收入/单量, 用户看到的是"这不是我这家店的数"。

    保留: 表头行(数据首行之上)、所有公式列(结构)、A 列日期(日期行脚手架, 写入要靠它定位行号)。
    数据首行 = data_start_row(ws): 常规门店表 6, 「天麓」那种旧结构表 5 —— 写死 6 的话
    天麓的第 5 行旧数据会被复刻进新表(正是这里要防的事)。
    """
    start = min_row or data_start_row(ws)
    last = max(int(getattr(ws, "max_row", 0) or 0), start)
    for row in ws.iter_rows(min_row=start, max_row=last, max_col=max_col):
        for c in row:
            if c.column == 1:            # A 列日期: 结构, 保留
                continue
            v = c.value
            if isinstance(v, str) and v.startswith("="):
                continue                 # 公式: 结构, 保留
            if v is not None:
                c.value = None


def _ensure_sheet_in_wb(wb, name, base_sheet=None, main_sheets=None):
    """在**已打开**的 workbook 里确保 Sheet 存在(不存在则复刻 base 表结构 + 清空数据区)。

    返回 (ws, created)。与 ensure_sheet() 的区别: 不落盘 —— 批量写入靠它把
    「每店一次读+存」压成「整任务一次读+一次存」。
    """
    name = str(name or "").strip()
    if name in wb.sheetnames:
        return wb[name], False
    base = pick_base_sheet(wb, base_sheet, main_sheets)   # 样板=第 3 个 sheet(见其文档)
    if not base:
        raise KeyError("模板里没有可用于复制结构的 sheet")
    ws = wb.copy_worksheet(wb[base])
    ws.title = name
    _clear_data_area(ws)          # 关键: 别把样板表当天的数据复刻进新表
    return ws, True


def default_sheet_name(template_path, cfg=None):
    """模板的「主数据表」名 = **第 3 个 sheet**(用户口径 2026-09-29)。

    用户原话: 模板第 1、2 个 sheet **不一定是门店统计的表格**, 汇总/主数据应落在第 3 个
    (或之后)的表格里。可在 config 里用 `defaults.main_sheet: "某Sheet名"` 明确覆盖。
    模板读不到时退回历史值 Sheet2(不改变"读不了就别崩"的行为)。
    """
    forced = ((cfg or {}).get("defaults") or {}).get("main_sheet")
    try:
        wb = openpyxl.load_workbook(template_path, read_only=True)
        names = list(wb.sheetnames)
        wb.close()
    except Exception:
        names = []
    if forced and str(forced) in names:
        return str(forced)
    if forced:
        return str(forced)
    if len(names) >= 3:
        return names[2]
    return SHEET


def main_sheet_in(wb):
    """**主数据表**(汇总/账号级数据落这里) —— 与 pick_base_sheet 语义不同, 别混用。

    规则(用户口径 2026-09-29): 第 3 个 sheet; 模板不足 3 个 sheet 时退回历史值 Sheet2。
    ⚠️ 注意: 这里**不能**退化成"第一个非主表" —— 那样在只有 2 个 sheet 的模板上, 第一轮
    新建的门店 Sheet 会占住索引 2, 第二轮汇总就被写进**门店表**(把门店数据冲掉)。
    """
    names = list(wb.sheetnames)
    return names[2] if len(names) >= 3 else SHEET


def _looks_like_store_map(ws):
    """是不是「门店名映射表」(表头里有「门店」列 + 某个「…门店名称」列)。

    为什么要判: 用户模板第 3 个 sheet 常是这张映射表 —— 汇总数据写进去会**污染映射数据**
    (2026-09-29 用户新模板 Sheet1 就是), 且它不会出现在门店写入任务里, 撞车保护拦不住。
    """
    try:
        for r in range(1, min(ws.max_row, 12) + 1):
            txt = [str(ws.cell(row=r, column=c).value or "").strip()
                   for c in range(1, min(ws.max_column, 24) + 1)]
            if "门店" in txt and any(t.endswith("门店名称") for t in txt):
                return True
    except Exception:
        pass
    return False


def main_fallback_note(fell_name, why=None):
    """撞车保护回退 Sheet2 时给用户的说明(**信息级: 这是设计内避让, 不是错误**)。

    2026-10-09 用户报告「跑美团时报错」= 这条旧文案(⚠️ 主数据表原解析为…→汇总改写…)
    吓人且没说是哪种原因。避让本身是对的(不写映射表/不冲门店表), 只需要说清楚:
    为什么避开、落到了哪、怎么固定。原因: "taken" = 与本次要写的门店表同名; "map" = 是门店名映射表。
    """
    cause = ("是门店名映射表" if why == "map"
             else "与本次要写的门店表同名" if why == "taken"
             else "不宜作汇总落点")
    return (f"汇总表避让: 「{fell_name}」{cause} → 汇总落「{SHEET}」"
            f"（想固定主表名可设 defaults.main_sheet）")


def resolve_main(template_path, cfg=None, taken=()):
    """主数据表名 + **撞车保护**。返回 (名字, 被回退掉的名字或 None, 回退原因或 None)。

    规则: config `defaults.main_sheet` → 第 3 个 sheet → Sheet2。
    两道保护(命中任一就回退 Sheet2, 绝不写坏客户模板; 原因返回 "taken"/"map"):
      ① 解析出的表同时出现在 `taken`(本次要写的门店 Sheet) 里 → 汇总会把该店当天那行冲掉;
      ② 解析出的表是**门店名映射表**(第 3 个位置放映射表的模板很常见) → 会污染映射数据。
    """
    nm = default_sheet_name(template_path, cfg)
    if nm in {str(x) for x in (taken or ()) if x}:
        return SHEET, nm, "taken"
    try:
        import openpyxl
        wb = openpyxl.load_workbook(template_path, read_only=True)
        try:
            if nm in wb.sheetnames and _looks_like_store_map(wb[nm]):
                return SHEET, nm, "map"
        finally:
            wb.close()
    except Exception:
        pass
    return nm, None, None


def _pick_sheet(wb, sheet_name=None):
    """按名取工作表(支持门店 Sheet 模糊匹配); 无 sheet_name → 主数据表(默认=第 3 个 sheet)。"""
    if not sheet_name:
        return wb[main_sheet_in(wb)]
    if sheet_name in wb.sheetnames:
        return wb[sheet_name]
    for sn in wb.sheetnames:
        if sheet_name in sn or sn in sheet_name:
            return wb[sn]
    raise KeyError(f"模板中无匹配「{sheet_name}」的工作表(现有: {wb.sheetnames})")


def _write_into(ws, date, write_set, mode="top", formula_protect=True, borders=True):
    """把一组值写进**已打开**的某个 Sheet(不落盘)。逻辑与 fill_template 完全一致。"""
    fmap = scan_formula_cols(ws) if formula_protect else {}
    row, how = resolve_row(ws, date, mode=mode)
    filled = skipped_formula = skipped_nodata = 0
    for col, v in write_set.items():
        if v is None:
            skipped_nodata += 1
            continue
        if col in fmap:
            skipped_formula += 1
            continue
        ws[f"{col}{row}"].value = v
        filled += 1
    # 公式还原(历史覆盖/追加行缺失)
    fixed = []
    for col, (r0, f0) in fmap.items():
        cell = ws[f"{col}{row}"]
        if not (isinstance(cell.value, str) and cell.value.startswith("=")):
            cell.value = shift_formula(f0, row)
            fixed.append(col)
    if borders:
        apply_row_borders(ws, row)
    return {"row": row, "how": how, "filled": filled, "skipped_formula": skipped_formula,
            "skipped_nodata": skipped_nodata, "fixed_formulas": fixed, "sheet": ws.title}


def _find_row_by_date(ws, date, max_rows=60):
    """在 Sheet 的日期列里按日期找行号(Excel 序列号/日期对象/字符串都认)。找不到返回 None。

    批量写入必须用它校验: top 模式后一次写入会把先写的行**往下顶**, 写入时记的行号已失效。
    扫描自该表的**数据首行**起(data_start_row; 天麓=5, 常规=6) —— 否则天麓第 5 行的日期
    永远找不到, 回读校验会把"写对了"判成没写。
    """
    start = data_start_row(ws)
    for r in range(start, min(ws.max_row, start + max_rows) + 1):
        if _parse_date(ws[f"{DATE_COL}{r}"].value) == date:
            return r
    return None


def template_date_scan(template_path, date, main_sheets=None, max_rows=60):
    """扫模板「门店 Sheet」的 A 列, 看 date 是否已经存在 → (可扫描门店表数, 含该日期的表数)。

    需求 B(2026-10-12 用户口径): 跑流程前先确认模板里有没有这次要填的日期; 一张都没有 →
    停下问用户(继续=填表时自动新建该行; 结束=干净中止, 不打开浏览器/不下载)。
    本函数只负责"看", 决策在 orchestrator._template_date_gate。

    门店表口径与 pick_base_sheet / _looks_like_store_map 一致: 排除前 2 张主表、
    defaults.main_sheets、以及外观是「门店名映射表」的 sheet —— 否则映射表里恰好存在的日期
    会让我们误判"模板里有这一天", 白白跳过一次该有的确认。
    日期解析复用 _find_row_by_date(EPOCH 序列号 / datetime / 字符串 三吃)。
    读不了模板 → (0, 0)(调用方跳过检查, 不打扰用户)。
    """
    try:
        wb = openpyxl.load_workbook(template_path)
    except Exception:
        return 0, 0
    try:
        names = list(wb.sheetnames)
        mains = {str(m) for m in (main_sheets or [])} | {SHEET}
        skip = set(names[:2])                 # 前 2 张 = 主表(用户口径: 汇总落在第 3 个起)
        n_sheets = n_hit = 0
        for sn in names:
            if sn in skip or sn in mains:
                continue
            try:
                ws = wb[sn]
                if _looks_like_store_map(ws):  # 映射表不是门店数据表, 不参与日期判断
                    continue
                n_sheets += 1
                if _find_row_by_date(ws, date, max_rows=max_rows):
                    n_hit += 1
            except Exception:
                continue
        return n_sheets, n_hit
    finally:
        try:
            wb.close()
        except Exception:
            pass


def _reread_verify(path, writes):
    """回读校验: writes = [(sheet_name, row, write_set, formula_cols), ...] → {sheet: [坏列]}。

    一次性打开文件核对所有写入(旧实现每写一次就重开一次文件读校验)。
    """
    bad = {}
    if not writes:
        return bad
    try:
        wb = openpyxl.load_workbook(path)
    except Exception:
        return bad
    try:
        for sheet_name, row, write_set, fcols in writes:
            if sheet_name not in wb.sheetnames:
                continue
            ws = wb[sheet_name]
            for col, v in write_set.items():
                if v is None or col in fcols:
                    continue
                got = ws[f"{col}{row}"].value
                if isinstance(v, str):
                    ok = got == v
                else:
                    try:
                        ok = got is not None and abs(float(got) - float(v)) < 1e-6
                    except (TypeError, ValueError):
                        ok = False
                if not ok:
                    bad.setdefault(sheet_name, []).append(col)
    finally:
        try:
            wb.close()
        except Exception:
            pass
    return bad


def fill_template(template_path, date, write_set, mode="top",
                  formula_protect=True, borders=True, sheet_name=None, on_retry=None, log=None):
    """write_set: {模板列: 值} (静态值; 公式列自动跳过)。
    sheet_name: 目标工作表(默认 Sheet2); 美团管家多门店模式按门店名匹配 Sheet。
    on_retry: 保存被占用重试时回调(把"还差多少秒/谁占着"透传给用户), 见 save_wb。
    log: 瘦身等提示的打印出口(可省, 见 save_wb/`_slim_before_save`)。
    返回 dict(row, how, filled, skipped_formula, skipped_nodata, reread_ok, fixed_formulas)"""
    wb = openpyxl.load_workbook(template_path)
    try:
        ws = _pick_sheet(wb, sheet_name)
        res = _write_into(ws, date, write_set, mode=mode,
                          formula_protect=formula_protect, borders=borders)
        save_wb(wb, template_path, on_retry=on_retry, log=log)
    finally:
        try:
            wb.close()
        except Exception:
            pass
    # 回读验证(静态列) —— 校验写到**同一个 Sheet**, 别拿 Sheet2 校门店 Sheet
    fmap = scan_formula_cols(openpyxl.load_workbook(template_path)[res["sheet"]])
    bad = _reread_verify(template_path, [(res["sheet"], res["row"], write_set, fmap)]).get(res["sheet"], [])
    return {"row": res["row"], "how": res["how"], "filled": res["filled"],
            "skipped_formula": res["skipped_formula"], "skipped_nodata": res["skipped_nodata"],
            "fixed_formulas": res["fixed_formulas"], "reread_bad": bad, "sheet": res["sheet"]}


def fill_many(template_path, jobs, mode="top", formula_protect=True, borders=True,
              base_sheet=None, main_sheets=None, create_missing=True, verify=True,
              on_retry=None, log=None, slim=None):
    """**批量填表**: 整任务只读一次模板、只存一次盘, 顺带把缺的门店 Sheet 一起建出来。

    jobs = [(sheet_name, date, write_set), ...] —— sheet_name=None 表示主数据表(Sheet2);
    date 用 'YYYY-MM-DD' 字符串。同一 Sheet 的多天会**按日期升序**写入(保证"最新永远第一行",
    与逐日调用 fill_template 的语义一致)。

    返回 [(sheet, date, result_dict), ...]; 依赖 `create_missing` 时 base_sheet/main_sheets
    决定复刻哪张表(默认主数据表 Sheet2)。

    log / slim: 保存前的模板瘦身(M5)出口与开关, 见 save_wb —— log 收"剪掉 N 个空格式格"一行提示。

    为什么需要它: 一店一 Sheet 场景下逐店调用 fill_template 是「每店一次读+写+存+回读」,
    22 家店 ≈ 44 次 openpyxl 读写(实测 316s, 1 天); 批量化后 ≈ 1 次读写(实测 <20s)。
    """
    jobs = [(sn, d, ws_) for (sn, d, ws_) in jobs if ws_ is not None]
    if not jobs:
        return []
    # 同一 Sheet 按日期升序写: insert 模式新行插在表头上方, 倒序会让最早写的被压到最下面
    jobs = sorted(jobs, key=lambda j: (str(j[0] or ""), str(j[1])))
    wb = openpyxl.load_workbook(template_path)
    # 主数据表(sheet_name=None 的 job)在这里解析**一次**、全程复用: 第 3 个 sheet;
    # 若它同时是本次要写的门店 Sheet → 撞车保护回退 Sheet2(见 resolve_main 的说明)。
    _main, _fell, _fell_why = resolve_main(template_path, None, [j[0] for j in jobs if j[0]])
    out = []
    try:
        for sheet_name, date, write_set in jobs:
            if sheet_name and sheet_name not in wb.sheetnames and create_missing:
                _ensure_sheet_in_wb(wb, sheet_name, base_sheet=base_sheet, main_sheets=main_sheets)
            try:
                ws = _pick_sheet(wb, sheet_name or _main)
            except KeyError:
                continue
            res = _write_into(ws, date, write_set, mode=mode,
                              formula_protect=formula_protect, borders=borders)
            res["requested"] = sheet_name or _main   # 请求的 Sheet 名(可能与实际命中不同)
            if _fell:
                res["main_fallback_from"] = _fell    # 撞车保护: 本想去 _fell(门店表/映射表) → 实际落 Sheet2
                res["main_fallback_why"] = _fell_why
            out.append((res["sheet"], date, res))
        save_wb(wb, template_path, on_retry=on_retry, log=log, slim=slim)
    finally:
        try:
            wb.close()
        except Exception:
            pass
    if verify and out:
        rw = openpyxl.load_workbook(template_path)
        try:
            fmaps = {sn: scan_formula_cols(rw[sn]) for sn, _, _ in out if sn in rw.sheetnames}
            # 行号按**日期**重新定位: top 模式下后写的行会把先写的顶下去, 写入时的行号已失效
            rows_now = {}
            for (sn, date, r), j in zip(out, jobs):
                if sn not in rw.sheetnames:
                    continue
                rows_now[(sn, date)] = _find_row_by_date(rw[sn], date) or r["row"]
        finally:
            try:
                rw.close()
            except Exception:
                pass
        bad = _reread_verify(template_path, [
            (sn, rows_now.get((sn, date), r["row"]), j[2], fmaps.get(sn, {}))
            for (sn, date, r), j in zip(out, jobs)])
        for sn, _d, r in out:
            r["reread_bad"] = bad.get(sn, [])
        for (sn, date, r) in out:
            if (sn, date) in rows_now:
                r["row"] = rows_now[(sn, date)]
    for _sn, _d, r in out:
        r.setdefault("reread_bad", [])
    return out
