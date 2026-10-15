# -*- coding: utf-8 -*-
"""模板 XLSX 瘦身: 剪掉「无值但带样式」的越界单元格, 消除 WPS 整行套格式造成的体积膨胀。

背景(2026-10-15 定位 + 原型实测):
  《运营报表模板10.13》被 WPS 做过"整行/整表套格式" → 27 张表里 23 张的 sheet XML 各约
  8.95MB(解压合计 183.7MB), 单表 331,580 个 <c> 里只有约 3,310 个有内容, 其余全是
  「无值但带样式」的空格, 从数据区一路铺到 XFD(16384) 列; <dimension> 还谎报 A1:XFD50。
  程序用 openpyxl 载入→保存时把这些格子原样带出 → 输出文件 17.8~21.6MB。
  原型实测: 按「内容边界 + 边距」裁剪后 21.6MB → 1.2MB, 76,671 个内容格逐字节零差异,
  合并区/行高/列宽全保留, openpyxl 往返不再反弹。

裁剪口径(宁可少剪, 不能剪错 —— 剪错了是丢数据, 剪少了只是文件大):
  · 边界 = max(全部 sheet 的**有值**格最大列/行, 合并区延伸, 打印区延伸) + 边距;
  · 只删「列 > 列边界 或 行 > 行边界」**且 value is None** 的格子;
    0 / False / 空字符串都算**有值**(它们会被真的写进 xlsx, 删掉就是丢数据);
  · 边界跨**全部 sheet 取全局最大**(同一模板内各表同口径, 以最宽的表为准), 免得"某张表被
    剪得比另一张窄"造成表间不一致;
  · 合并区 / 打印区延伸到内容之外时, 边界跟着外扩 —— 那是用户真会看到的区域;
  · 绝不碰 ws.row_dimensions / ws.column_dimensions(行高列宽是用户的可见格式);
  · **幂等**: 对已瘦身的工作簿再次调用, removed == 0(保存重试阶梯里重复调用也安全)。

开关: config `defaults.slim_xlsx`(默认 true) / 环境变量 `MRC_SLIM_XLSX=0/1`(调试, 0=关)
—— 优先级见 slim_enabled()。
"""
import os

SLIM_COL_MARGIN = 30   # 内容边界外额外保留的宽(列数): 防裁剪到可见格式区(边框/底纹/列宽)
SLIM_ROW_MARGIN = 15   # 内容边界外额外保留的高(行数): 防裁剪到可见格式区(行高/边框)

# name 属性在 xlsx 里落地为 _xlnm.Print_Area(openpyxl 读回来是 Print_Area)
_PRINT_AREA_NAMES = ("Print_Area", "_xlnm.Print_Area")


def _config_slim_flag():
    """从客户端 config.yaml 读 defaults.slim_xlsx。

    壳侧不写这个键时返回 None → 走默认 True。读配置本身**绝不许炸保存流程**: 任何异常一律
    当成"没配"(None)。保存一次才读一次, 量级可忽略, 因此不缓存(避免配置改了不生效的怪事)。
    """
    try:
        from .config import load_config
        cfg = load_config() or {}
        return ((cfg.get("defaults") or {}).get("slim_xlsx"))
    except SystemExit:
        return None          # config.load_config 在缺 pyyaml 时会 raise SystemExit —— 不能让它炸保存
    except Exception:
        return None


def slim_enabled(cfg=None, env=None):
    """是否启用瘦身。

    优先级: 环境变量 MRC_SLIM_XLSX(调试/救急, 压过一切) > 传入的 cfg > config.yaml 的
    defaults.slim_xlsx > 默认 True。环境变量必须能压过配置文件 —— 客户机上出了怪问题,
    让用户设个变量就能立刻回到旧行为, 不用改配置、不用等新版本。
    """
    raw = os.environ.get("MRC_SLIM_XLSX", "") if env is None else env
    if str(raw).strip() != "":
        return str(raw).strip().lower() not in ("0", "false", "no", "off")
    val = ((cfg or {}).get("defaults") or {}).get("slim_xlsx")
    if val is None:
        val = _config_slim_flag()
    if val is not None:
        return bool(val)
    return True


def _sheet_print_area_bounds(ws):
    """该表打印区的延伸 → (max_col, max_row); 没有打印区 → (0, 0)。

    openpyxl 读盘时已把 Print_Area 挂到 ws._print_area(PrintArea, 可能多段), 直接取更可靠;
    解析不了就抛给调用方记 skipped_sheets(宁可跳过这张表, 不猜)。
    """
    pa = getattr(ws, "_print_area", None)
    if pa is None:
        return 0, 0
    col = row = 0
    for rng in (getattr(pa, "ranges", ()) or ()):
        col = max(col, int(rng.max_col))
        row = max(row, int(rng.max_row))
    return col, row


def _wb_print_areas(wb):
    """工作簿级 defined_names 里的 Print_Area → {localSheetId(或 None): [值字符串, ...]}。

    实测本模板只有一条垃圾 definedName(name 是 64 个 a、值 '#REF!')—— 它**不是** Print_Area,
    这里不会碰它, slim 也不会删它。
    """
    out = {}
    try:
        items = list(wb.defined_names.items())
    except Exception:
        return out
    for key, dn in items:
        try:
            nm = str(getattr(dn, "name", None) or key)
            if nm not in _PRINT_AREA_NAMES:
                continue
            out.setdefault(getattr(dn, "localSheetId", None), []).append(str(getattr(dn, "value", "")))
        except Exception:
            continue
    return out


def _bounds_from_area_text(text):
    """'门店A'!$A$1:$H$40,'门店A'!$D$1:$E$5 → (max_col, max_row)。

    值**非空却解析不出任何单元格**(如 '#REF!' 这类垃圾) → 抛 ValueError, 由调用方把这张表
    记进 skipped_sheets。为什么不静默返回 (0,0): 那等于"这张表没有打印区", 边界会少算 →
    可能把用户可见区域里的空格剪掉。宁可整张不剪(文件偏大), 也不误剪。
    """
    from openpyxl.utils.cell import range_boundaries
    from openpyxl.worksheet.print_settings import PRINT_AREA_RE
    text = str(text or "").strip()
    if not text:
        return 0, 0
    col = row = 0
    hit = False
    for m in PRINT_AREA_RE.finditer(text):
        cells = m.group("cells")
        if not cells:
            continue
        _c1, _r1, c2, r2 = range_boundaries(cells)   # 已是绝对坐标, 不会为 None
        col, row = max(col, c2), max(row, r2)
        hit = True
    if not hit:
        raise ValueError(f"打印区解析失败: {text[:60]}")
    return col, row


def _cell_value(cell):
    """取单元格的"有没有内容"口径值: 只认 value(None = 没有内容)。"""
    return getattr(cell, "value", None)


def slim_workbook(wb, col_margin=SLIM_COL_MARGIN, row_margin=SLIM_ROW_MARGIN):
    """就地剪掉「越界且无值」的单元格(带样式的空格), 返回统计 dict。

    {'removed': 剪掉的格子数, 'kept_blanks': 保留的空格数(边界内的),
     'col_bound': 列边界, 'row_bound': 行边界, 'skipped_sheets': [打印区解析失败的表名],
     'sheets_touched': 真的被剪过格子的表数(日志文案用「M 张表」; 任务书列出的键之外
     多这一个, 是给调用方打日志用的统计, 其余键与任务书一致)}

    removed == 0 表示这张工作簿已经很干净(或已瘦过身) —— 调用方据此决定打不打日志。
    """
    stats = {"removed": 0, "kept_blanks": 0, "col_bound": 0, "row_bound": 0,
             "skipped_sheets": [], "sheets_touched": 0}
    try:
        sheets = list(getattr(wb, "worksheets", None) or [])
    except Exception:
        return stats
    if not sheets:
        return stats

    wb_areas = _wb_print_areas(wb)

    # ---- 第一趟: 求全局边界(只扫一遍 _cells, 不做 O(n²) 的逐格查合并区) ----
    col_max = row_max = 0
    skip_titles = set()          # 打印区解析不了的表: 宁可整张不剪, 也不拿猜出来的边界去剪
    for idx, ws in enumerate(sheets):
        for (_r, _c), cell in (getattr(ws, "_cells", None) or {}).items():
            if _cell_value(cell) is None:
                continue
            if _c > col_max:
                col_max = _c
            if _r > row_max:
                row_max = _r
        mc = getattr(ws, "merged_cells", None)
        for rng in (getattr(mc, "ranges", ()) or ()):
            col_max = max(col_max, int(rng.max_col))
            row_max = max(row_max, int(rng.max_row))
        # 打印区延伸(表自带 + 工作簿 defined_names 里挂到这张表上的全局/本表条目)
        try:
            pc, pr = _sheet_print_area_bounds(ws)
            col_max, row_max = max(col_max, pc), max(row_max, pr)
            for text in wb_areas.get(idx, []) + wb_areas.get(None, []):
                ac, ar = _bounds_from_area_text(text)
                col_max, row_max = max(col_max, ac), max(row_max, ar)
        except Exception:
            # 解析失败 → 跳过这张表并记账(不猜边界; 边界少算会误剪, 多算只是文件大)
            title = getattr(ws, "title", "?")
            skip_titles.add(title)
            if title not in stats["skipped_sheets"]:
                stats["skipped_sheets"].append(title)

    col_bound = max(int(col_max), 1) + int(col_margin)
    row_bound = max(int(row_max), 1) + int(row_margin)
    stats["col_bound"], stats["row_bound"] = col_bound, row_bound

    # ---- 第二趟: 剪越界空格(先取 key 列表, 防遍历中改 dict) ----
    for ws in sheets:
        if getattr(ws, "title", "?") in skip_titles:
            continue
        cells = getattr(ws, "_cells", None)
        if not cells:
            continue
        removed_here = 0
        for key in list(cells):
            try:
                r, c = key
            except (TypeError, ValueError):
                continue
            outside = (c > col_bound) or (r > row_bound)
            if not outside:
                if _cell_value(cells.get(key)) is None:
                    stats["kept_blanks"] += 1
                continue
            if _cell_value(cells.get(key)) is not None:
                continue                      # 越界但有值(0/False/'' 都算) → 绝不删
            del cells[key]
            removed_here += 1
        if removed_here:
            stats["sheets_touched"] += 1
        stats["removed"] += removed_here
    return stats
