# -*- coding: utf-8 -*-
"""美团外卖 → 运营模板 E~DF 列映射。

口径来源: ~/Downloads/商家报表整理/运营报表.pdf (脑图, 列位置级对应表)
核对依据: 真实导出门店报表 (/tmp/mt_dl/门店_全部门店_*.csv, 83 列, gb18030)
         26 对中 25 对与报表列名逐字命中, 唯一非逐字: 总营业额 ← O(优惠前总额) 语义吻合
方向: 模板列 ← 报表列字母 (报表字母 = 导出 CSV 列序, A=第1列)

数据来源分工(脑图):
  ① 全部业务报表(业务选「全部」→ 可选内容勾选「全部」) → E/F/G/H + CK~DF
  ② 拼好饭报表(业务选「拼好饭」)                        → AS~AW
  ③ 网页抓取(经营数据→流量 / 推广)                      → I~AR(主站/拼好饭流量) + CE~CJ(推广)
     ※ 主站营收/订单(I/J/K)是模板公式 =E-F 式, 公式列永不写静态值(fill.py 自动跳过)
"""
import csv
import glob
import io
import os
import re

from ..fill import num

DATE_COL = "A"
FILE_ALL = "美团_全部业务_{f}_{t}.csv"
FILE_BAO = "美团_拼好饭_{f}_{t}.csv"
TEXT_COLS = {"DC"}          # 文本列(不做数值转换)
# 全店(scope=all)汇总时可加的报表列; 其余(得分/率/时长文本)聚合无意义 → 留空
ADDITIVE = {"O", "G", "Q", "T", "AO", "AP"}

# (模板列, 报表列字母, 名称)
ALL_MAPPING = [
    # ⚠️ 2026-10-01: 平台会改列序/列数, 名字对不上就留空(build_write_set 按名取 · 不猜位置)。
    #    这 3 列原标签是近义词 → 已换成报表真实表头名: 总营业额→优惠前总额 / 总营业收入→营业收入 /
    #    总订单量→有效订单(报表没有"总订单量"这个词, 它叫 有效订单)。
    ("E", "O", "优惠前总额"),
    ("F", "G", "营业收入"),
    ("G", "Q", "有效订单"),
    ("H", "T", "平台活动补贴"),
    ("CK", "AO", "取消订单"),
    ("CL", "AP", "商责取消订单"),
    ("CM", "AU", "店铺分"),
    ("CN", "AZ", "商家评分得分"),
    ("CO", "BT", "综合体验分"),
    ("CP", "BU", "商品质量分"),
    ("CQ", "BV", "服务体验分"),
    ("CR", "BW", "商品满意度"),
    ("CS", "BX", "包装满意度"),
    ("CT", "BY", "复购率指标得分"),
    ("CU", "BZ", "复购率"),
    # ⚠️ 源列以 "=" 开头 = **按表头名取值**(抗平台列数变动)。2026-10-01 真机: 这三列按字母位置
    #    取值整体错位一格(CC/CD/CE → 实际在 CB/CC/CD) → 写进模板的是隔壁列的值(静默错数据)。
    ("CV", "=服务负反馈率指标得分", "服务负反馈率指标得分"),
    ("CW", "=食品安全负反馈率指标得分", "食品安全负反馈率指标得分"),
    ("CX", "=食品安全负反馈率", "食品安全负反馈率"),
    ("CY", "BO", "出餐完成上报率得分/配送准时率得分"),
    ("CZ", "BL", "出餐完成上报率/配送准时率"),
    ("DA", "CA", "消息回复率指标得分"),
    ("DB", "CB", "消息回复率"),
    ("DC", "AR", "营业时段"),
    ("DD", "AV", "高峰营业时长得分"),
    # ⚠️ 2026-10-14 (修复规格书 §4.5 / T05): DE/DF 一直空**不是**"报表没有这两列" —— 真机 83 列
    #    「全部业务」报表里 第 65 列=基础营业时长得分 / 第 66 列=基础营业时长(样例 refs/samples/
    #    美团_全部业务_*.csv 已核实)。空列根因 = 下载时「业务」没选「全部」(选了外卖/拼好饭就少这两列)。
    #    仍按**表头名**取值: 字母只记录位置(BM/BN), 名字对不上 → 留空 + NAME_FALLBACKS 诊断, 不猜位置。
    #    勿与 DD(高峰营业时长得分, AV)混填(规格书 §4.5 明确提示这两个是不同字段)。
    ("DE", "BM", "基础营业时长得分"),
    ("DF", "BN", "基础营业时长"),
]

# 拼好饭报表(独立下载) → 模板拼好饭营收区
BAO_MAPPING = [
    # ⚠️ 换用**拼好饭报表的真实表头名**(原标签"拼好饭XX"在报表里不存在 → 会退回错位字母取错值)
    ("AS", "H", "优惠前总额"),
    ("AT", "G", "营业收入"),
    ("AU", "J", "有效订单"),
    ("AV", "K", "实付单均价"),
    ("AW", "L", "活动补贴"),
]

# ---------- 商圈Top 区(仅单门店视角可得, 2026-09-23 实测确认) ----------
# 路径: 报表页右上角切单店(wmPoiId=门店编号) → 经营数据/流量 → 「流量转化」漏斗(右=对比基准)
# 口径: 对比基准选「商圈同行前10%均值」(没有则用默认「商圈同行均值」, 日志会写明)
# 用户口径: 只在做**单门店**数据时采集; 全部门店模式不采集(列留空)
CIRCLE_MAP = {
    "main": {          # 主站(业务=外卖): 模板 L~AR 区的 TOP 列
        "整体": {"曝光": "M", "进店": "P", "进店转化率": "S", "下单": "V", "下单转化率": "Y"},
        # ⚠️ 2026-10-13: 新客/老客组的**第 3 列列名是「下单转化率」**(AH/AQ 是"商圈TOP下单转化率"),
        #    以前键写成「进店转化率」→ 把同漏斗上一级指标写进了下单列(规格书 §4.2 取错指标)。
        "新客": {"曝光": "AB", "进店": "AE", "下单转化率": "AH"},
        "老客": {"曝光": "AK", "进店": "AN", "下单转化率": "AQ"},
    },
    "bao": {           # 拼好饭: 模板 AX~CD 区的 TOP 列
        "整体": {"曝光": "AY", "进店": "BB", "进店转化率": "BE", "下单": "BH", "下单转化率": "BK"},
        "新客": {"曝光": "BN", "进店": "BQ", "下单转化率": "BT"},
        "老客": {"曝光": "BW", "进店": "BZ", "下单转化率": "CC"},
    },
}


def circle_write_set(circle, biz="main"):
    """把抓到的商圈值(capture_circle 返回)映射成 {模板列: 值}。

    circle: {"整体": {"曝光": 1186, "进店": 91, ...}, "新客": {...}, "老客": {...}}
    缺失的字段跳过(不写 None, 也不写 0 占位)。
    新客/老客的下单转化率: 页面没给「下单转化率」时按 下单/进店 自算; 两者缺一 → 留空。
    """
    out = {}
    for grp, cols in (CIRCLE_MAP.get(biz) or {}).items():
        vals = ((circle or {}).get(grp) or {})
        for field, col in cols.items():
            v = vals.get(field)
            if v is None and field == "下单转化率":
                # 页面缺该率值时的自算兜底(分母为 0/缺失 → 不写)
                _o, _e = vals.get("下单"), vals.get("进店")
                if _o is not None and _e:
                    v = round((_o or 0.0) / _e, 6)
            if v is not None:
                out[col] = v
    return out


def circle_write_set_all(captured):
    """capture_circle 的整体返回 → 两张表(主站/拼好饭)的写入集合。"""
    captured = captured or {}
    return {"main": circle_write_set(captured.get("main"), "main"),
            "bao": circle_write_set(captured.get("bao"), "bao")}



# 实测(2026-09-18): 83 列全部业务报表 X~AN 自带 曝光/入店/下单+转化率+新老客拆分;
# 拼好饭 31 列报表 O~AE 同构。数据与流量页同源(09-17 全部门店 48053 人 = 页面一致)。
# 只填本店列; 商圈TOP列/差异列不写(差异公式会显示本店值, 见 spec「实测补充」)。
TRAFFIC_MAPPING = {
    # ⚠️ 2026-10-01 真机审计: 主站这 11 项的**字母位置全错**(L←X, 但报表里 曝光人数 在 Y),
    #    拼好饭那 11 项字母基本对、但标签是近义词(进店 vs 报表的 入店)。
    #    现在: 字母取自真实报表表头位置 + 名字用**报表原词**(build_write_set 按名取值优先)。
    #    报表口径字段: 曝光人数/入店人数/入店转化率/下单转化率/曝光新客/入店新客/新客入店转化率/
    #                  曝光老客/入店老客/老客入店转化率/下单人数 (主站与拼好饭报表同名)
    # -- 主站流量(全部业务报表) --
    "L":  ("all", "Y", "曝光人数"),
    "O":  ("all", "Z", "入店人数"),
    "R":  ("all", "AA", "入店转化率"),
    "U":  ("all", "AM", "下单人数"),
    "X":  ("all", "AB", "下单转化率"),
    "AA": ("all", "AC", "曝光新客"),
    "AD": ("all", "AD", "入店新客"),
    # ⚠️ 2026-10-13 修复规格书 P02.6: AG/AP 以前取的是**「入店转化率」**(同一漏斗的上一级指标)
    #    → 必须取「下单转化率」。真机报表字段名逐字核实(83 列全部业务表):
    #    AE=新客下单转化率、AI=老客下单转化率(另有 AC=入店新客 作率值兜底的自算分母)。
    "AG": ("all", "AE", "新客下单转化率"),
    "AJ": ("all", "AG", "曝光老客"),
    "AM": ("all", "AH", "入店老客"),
    "AP": ("all", "AI", "老客下单转化率"),
    # -- 拼好饭流量(拼好饭报表) --
    "AX": ("bao", "O", "曝光人数"),
    "BA": ("bao", "P", "入店人数"),
    "BD": ("bao", "Q", "入店转化率"),
    "BG": ("bao", "AC", "下单人数"),
    "BJ": ("bao", "R", "下单转化率"),
    "BM": ("bao", "S", "曝光新客"),
    "BP": ("bao", "T", "入店新客"),
    # 拼好饭报表字段名核实(31 列): U=新客入店转化率 / V=新客下单转化率 / Y=老客入店转化率 /
    # Z=老客下单转化率 → BS 取 V、CB 取 Z。
    "BS": ("bao", "V", "新客下单转化率"),
    "BV": ("bao", "W", "曝光老客"),
    "BY": ("bao", "X", "入店老客"),
    "CB": ("bao", "Z", "老客下单转化率"),
}

# 「下单转化率」率值列(AG/AP/BS/CB)的**兜底来源**: 报表里没有该率值列时, 用 下单人数/进店人数 自算。
# 两者都取不到 → 留空 + 记 NAME_FALLBACKS 告警(宁缺勿错: 绝不退回同漏斗上一级的「进店转化率」)。
RATE_FALLBACK = {
    "AG": ("all", "下单新客", "入店新客"),
    "AP": ("all", "下单老客", "入店老客"),
    "BS": ("bao", "下单新客", "入店新客"),
    "CB": ("bao", "下单老客", "入店老客"),
}



def col_index(letter):
    """列字母 → 0 基下标 (A=0, AA=26)"""
    n = 0
    for ch in letter.upper():
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def _decode(path):
    raw = open(path, "rb").read()
    for enc in ("gb18030", "gbk", "utf-8-sig"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("gb18030", "ignore")


def norm_date(v):
    """'20260915' / '2026/9/5' / '2026-09-15' → '2026-09-15'; 无效返回 None"""
    s = str(v or "").strip().replace("/", "-").replace(".", "-")
    if not s:
        return None
    m = re.match(r"^(\d{4})-?(\d{1,2})-?(\d{1,2})$", s)
    if m:
        y, mo, d = m.groups()
        if 1 <= int(mo) <= 12 and 1 <= int(d) <= 31:
            return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"
    return None


def load_csv(path):
    """→ (headers, rows); rows 为 str 列表的列表(跳过表头)"""
    text = _decode(path)
    it = csv.reader(io.StringIO(text))
    rows = [r for r in it if any(str(c).strip() for c in r)]
    if not rows:
        return [], []
    return rows[0], rows[1:]


def rows_by_date(path):
    """→ {日期: [ {列字母: 值} ... ]} (一天多行 = 多门店)"""
    headers, data = load_csv(path)
    letters = []
    for i in range(len(headers)):
        n, s = i + 1, ""
        while n:
            n, r = divmod(n - 1, 26)
            s = chr(65 + r) + s
        letters.append(s)
    out = {}
    for r in data:
        if not r:
            continue
        d = norm_date(r[0] if r else None)
        if not d:
            continue
        row = {letters[i]: (r[i] if i < len(r) else None) for i in range(len(letters))}
        # 同时提供**表头名**索引: 平台按指标集给列(同一商家 82 列/83 列都出现过), 按字母位置
        # 取值会整体错位 —— 2026-10-01 真机: CV/CW/CX 三列错位一格, 写进模板的是**隔壁列的值**
        # (静默错数据, 比留空危险得多)。有了名字索引, 关键列就能按名取值, 不受列数变化影响。
        for i, h in enumerate(headers):
            h = (h or "").strip()
            if h and i < len(r):
                row.setdefault(h, r[i])
        out.setdefault(d, []).append(row)
    return out


def pick_row(rows, store_id="", keyword=""):
    """单店: 按门店id(报表C列) → 门店名称(B列)含关键词 匹配; 全店报表该日单行时直接返回"""
    if not rows:
        return None
    if len(rows) == 1:
        return rows[0]
    sid = str(store_id or "").strip()
    if sid:
        for r in rows:
            if str(r.get("C", "")).strip() == sid:
                return r
    kw = str(keyword or "").strip()
    if kw:
        for r in rows:
            if kw in str(r.get("B", "")):
                return r
    return None


def agg_row(rows):
    """全店汇总: 数值列分两类聚合(2026-09-20 口径修正):
    - 可加列(营收/订单/补贴/取消): 求和
    - 均量列(得分/评分/率/店铺分): 算术平均(空值不计分母) —— 衡量品牌整体服务水平
    文本列(营业时段/资质项): 取首个非空。绝不因"聚合无意义"整列丢空(会把品牌服务指标全灭)。"""
    if not rows:
        return None
    if len(rows) == 1:
        return rows[0]
    out = {}
    all_letters = set()
    for r in rows:
        all_letters.update(r.keys())
    for letter in all_letters:
        vals = [num(r.get(letter)) for r in rows]
        nums = [v for v in vals if isinstance(v, (int, float))]   # 严格数值(文本不留)
        if not nums:
            # 文本列: 取首个非空(营业时段/达成项等)
            for r in rows:
                v = r.get(letter)
                if v not in (None, ""):
                    out[letter] = v
                    break
            else:
                out[letter] = None
            continue
        if letter in ADDITIVE:
            out[letter] = round(sum(nums), 4)
        else:
            # 均量: 简单算术平均(4位小数, 与报表精度一致)
            out[letter] = round(sum(nums) / len(nums), 4)
    return out


def biz_hours(row):
    """报表营业时段 '[00:00:00-02:50:00,09:30:00-23:59:59]' → 最长时段 'HH:MM-HH:MM'(天气取用)"""
    v = str((row or {}).get("AR") or "")
    spans = re.findall(r"(\d{1,2}):(\d{2}):\d{2}\s*-\s*(\d{1,2}):(\d{2}):\d{2}", v)
    if not spans:
        return None
    best, best_len = None, -1
    for h1, m1, h2, m2 in spans:
        a, b = int(h1) * 60 + int(m1), int(h2) * 60 + int(m2)
        ln = b - a if b >= a else 1440 - a + b
        if ln > best_len:
            best, best_len = (h1, m1, h2, m2), ln
    h1, m1, h2, m2 = best
    return f"{int(h1):02d}:{m1}-{int(h2):02d}:{m2}"


def find_files(ddir, date_from, date_to):
    """按本次客户端写出的规范文件名找 全部业务 / 拼好饭 CSV"""
    f, t = date_from.replace("-", ""), (date_to or date_from).replace("-", "")
    all_p = os.path.join(ddir, FILE_ALL.format(f=f, t=t))
    bao_p = os.path.join(ddir, FILE_BAO.format(f=f, t=t))
    return (all_p if os.path.exists(all_p) else None,
            bao_p if os.path.exists(bao_p) else None)


def collect(ddir, date_from, date_to):
    """目录内美团报表 → (all_rows, bao_rows, files)。供抓取与复用已有报表两处共用"""
    all_p, bao_p = find_files(ddir, date_from, date_to)
    all_rows = rows_by_date(all_p) if all_p else {}
    bao_rows = rows_by_date(bao_p) if bao_p else {}
    return all_rows, bao_rows, {"all": all_p, "bao": bao_p}


# 诊断用: 本次运行里"表头名对不上、退回按字母位置取值"的列(说明平台改了表头名或映射里写了错字)。
# 每轮抓取前应清空, 结束时若有内容说明仍有列只能靠位置取 → 需要人工核对。
NAME_FALLBACKS = []

# ---------- 数值合理性校验(规格书 铁律 8 / T11, 美团侧) ----------
# 率值列(存**小数**, 模板按 % 显示: 0.2115 = 21.15%) → 必须 ∈ [0, 1](即 0~100%);
# 其余数值列(人数/订单/金额/得分/时长) → 必须 ≥ 0。超界**不写该值** + 固定文案告警(可 grep)。
# 三态校验: 全部 ≈ 新客 + 老客(人数类, ±1 容差) → 只提示, 不拦写入。
RATE_COLS = frozenset({"R", "S", "X", "Y", "AG", "AH", "AP", "AQ",
                       "BD", "BE", "BJ", "BK", "BS", "BT", "CB", "CC"})
THREE_STATE = (("曝光人数", "曝光新客", "曝光老客"),
               ("入店人数", "入店新客", "入店老客"),
               ("下单人数", "下单新客", "下单老客"))
VALIDATE_WARNINGS = []


def validate_value(col, v):
    """单值校验 → 通过原样返回; 超界返回 None(**不写该值**)并记固定文案。"""
    if v is None or isinstance(v, str):
        return v
    if not isinstance(v, (int, float)):
        return v
    if col in RATE_COLS:
        if not (0.0 <= float(v) <= 1.0):
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
    """三态校验: 全部 ≈ 新客 + 老客(人数类, ±1 容差)。不通过 → 记提示(不拦写入)。"""
    row = row or {}
    for tot, a, b in THREE_STATE:
        T, A, B = num(row.get(tot)), num(row.get(a)), num(row.get(b))
        if not all(isinstance(x, (int, float)) for x in (T, A, B)):
            continue
        if abs(T - (A + B)) > 1:
            VALIDATE_WARNINGS.append(f"[三态校验] {tag}{tot}={T} ≠ 新客{A}+老客{B}(容差±1) → 提示(不拦写入)")


def build_write_set(all_row, bao_row, weather=(None, None)):
    """报表行(+拼好饭行) + 天气 → write_set(模板列→值); 公式列由 fill.py 自动跳过"""
    vals = {}
    # 取报表值: **按表头名优先** —— 平台会改列的顺序/数量(真机: 同一商家 82 列/83 列都出现过),
    # 按"列字母位置"取值会整体错位 → 写进模板的是隔壁列的值(静默错数据, 比留空危险)。
    # 2026-10-01 审计: 全部业务表 13 列、流量区 6 列都因错位取错值 → 统一改按名取, 名字不在才退回字母。
    def _pick(row, letter, label=None):
        row = row or {}
        if letter.startswith("="):
            return row.get(letter[1:])
        if label and label in row:
            return row.get(label)
        if label:
            # ⚠️ 表头名对不上 → **不写**(返回 None), 不再按字母位置猜。
            #    2026-10-01 真机审计: 13 列因错位取到隔壁列的值(如 G"总营业收入" ← Q 有效订单=75),
            #    静默错数据比留空危险得多 → 宁可不写。要恢复这些列, 必须把 label 改成报表真实表头名。
            NAME_FALLBACKS.append(f"{letter}={label}")
            return None
        return row.get(letter)

    for col, letter, label in ALL_MAPPING:
        v = _pick(all_row, letter, label)
        vals[col] = (v if v not in (None, "") else None) if col in TEXT_COLS else num(v)
    for col, letter, label in BAO_MAPPING:
        vals[col] = num(_pick(bao_row, letter, label))
    # 流量区(本店列): 全部业务/拼好饭 报表自带, 见 TRAFFIC_MAPPING 注释
    for col, (src, letter, label) in TRAFFIC_MAPPING.items():
        row = all_row if src == "all" else bao_row
        vals[col] = num(_pick(row, letter, label))
    # 新客/老客「下单转化率」(AG/AP/BS/CB) 兜底: 报表缺该率值列 → 用 下单人数/进店人数 自算;
    # 两者都取不到 → 留空 + 记一笔待人工核对(宁缺勿错: 绝不拿「进店转化率」顶替)。
    for col, (src, num_f, den_f) in RATE_FALLBACK.items():
        if vals.get(col) is not None:
            continue
        row = (all_row if src == "all" else bao_row) or {}
        _a, _b = num(row.get(num_f)), num(row.get(den_f))
        if _a is not None and _b:
            vals[col] = round((_a or 0.0) / _b, 6)
        else:
            NAME_FALLBACKS.append(f"{col}={num_f}/{den_f}")
    if weather and weather[0] is not None:
        vals["C"] = weather[0]
    if weather and len(weather) > 1 and weather[1] is not None:
        vals["D"] = weather[1]
    # 数值合理性校验(规格书 铁律 8 / T11): 率值 ∈ [0,100%]、人数/金额 ≥ 0 —— 超界值不写,
    # 三态(全部≈新客+老客, ±1)只提示。校验结果在 VALIDATE_WARNINGS(固定文案, 供编排层打印)。
    three_state_check(all_row, tag="主站/全部业务 ")
    if bao_row is not None:
        three_state_check(bao_row, tag="拼好饭 ")
    return validate_write_set(vals)


def validate_report():
    """数值校验结果 → 去重后的固定文案列表(编排层打印 + 复验 grep 断言用)。"""
    seen, out = set(), []
    for w in VALIDATE_WARNINGS:
        if w not in seen:
            seen.add(w)
            out.append(w)
    return out


def describe(all_path=None):
    """自检: 打印每个映射对(模板列名 ← 报表字母:报表列名)"""
    lines = []
    hdr = []
    if all_path and os.path.exists(all_path):
        hdr, _ = load_csv(all_path)
    for col, letter, name in ALL_MAPPING + BAO_MAPPING:
        i = col_index(letter)
        rh = hdr[i] if i < len(hdr) else "?"
        lines.append(f"  {col:<3}{name:<22}← {letter:<3}{rh}")
    return "\n".join(lines)
