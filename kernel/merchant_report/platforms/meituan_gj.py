# -*- coding: utf-8 -*-
"""美团管家(POS收银)后台适配器 (https://pos.meituan.com/)。
登录结构(实测): 登录表单在子 iframe eepassport.meituan.com/portal/login(懒加载, 时序 12s~20s+)。
iframe 内: tab「验证码登录|账号登录」(默认手机号验证码) → 需点「账号登录」→ 填账密 →
协议「我已阅读并同意《服务协议》《隐私政策》」→ 登录 → 可能短信验证(自动点发送, 人工输码)。
报表: 待真实账号实测(收银/对账数据, M5)。"""
import re
import time

from ..events import EventSink, HumanBridge

GJ_LOGIN = "https://pos.meituan.com/web/rms-account?redirect=https%3A%2F%2Fpos.meituan.com%2Fweb%23%2Flogin"
# 报表中心「营业概览」直连路由(2026-09-28 实测可直接渲染, 绕开侧边栏菜单)
GJ_BIZ_OVERVIEW = "https://pos.meituan.com/web/report/main#/rms-report/business-report"

# 切店弹窗「是否可见」(antd 关闭后 DOM 残留: 存在≠打开 —— 2026-10-10 探针 v4 踩到)
MODAL_VIS_JS = """() => {
  const m = document.querySelector('.org-switch-modal');
  if (!m) return false;
  const r = m.getBoundingClientRect();
  return r.width > 0 && r.height > 0;
}"""


_ROW_HIT_JS = r"""(kw) => {
  const m = document.querySelector('.org-switch-modal');
  if (!m) return null;
  const rows = [...m.querySelectorAll('tr')];
  for (const tr of rows) {
    const t = (tr.innerText || '').replace(/\s+/g, ' ');
    if (!t.includes(kw) || t.includes('当前')) continue;
    try { tr.scrollIntoView({block: 'center'}); } catch (e) {}
    const cell = tr.querySelector('td') || tr;
    const rr = tr.getBoundingClientRect();
    const r = cell.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) continue;
    const px = r.x + Math.min(60, r.width / 2), py = r.y + r.height / 2;
    const cx = rr.x + rr.width / 2, cy = rr.y + rr.height / 2;
    const hit = (e) => !!(e && tr.contains(e));
    return {px: px, py: py, cx: cx, cy: cy, txt: t.slice(0, 60),
            ok1: hit(document.elementFromPoint(px, py)),
            ok2: hit(document.elementFromPoint(cx, cy))};
  }
  return null;
}"""

_VERIFY_JS = r"""(a) => {
  const [x, y, kw] = a;
  const el = document.elementFromPoint(x, y);
  if (!el || !el.closest) return {ok: false};
  const tr = el.closest('tr');
  if (!tr || !tr.closest('.org-switch-modal')) return {ok: false};
  const t = (tr.innerText || '').replace(/\s+/g, ' ');
  return {ok: t.includes(kw) && !t.includes('当前')};
}"""



def _crosscheck_detail(meta):
    """交叉校验失败 → 把"哪一项、差多少"写进错误文案(现场一眼能判断是没刷完还是真缺列)。"""
    parts = []
    metrics = ((meta.get("probe") or {}).get("metrics") or {})
    for name in (meta.get("crosscheck_failed") or []):
        p = metrics.get(name) or {}
        total, known = p.get("total"), (p.get("known") or {})
        if total:
            parts.append(f"{name}: 渠道合计 {sum(known.values()):.2f} vs 页面总量 {total:.2f}")
        else:
            parts.append(f"{name}: 未能定位总量")
    return "；".join(parts) or "未知"


def _gj_norm_name(s):
    """门店名归一化: NFKC(全角→半角/兼容字符) + 去所有空白 —— 比表头/卡片店名用。"""
    import unicodedata
    t = unicodedata.normalize("NFKC", str(s or ""))
    return "".join(t.split())


def _gj_same_store(now, name):
    """表头店名是否就是目标店（全名, 非前缀）。

    2026-10-11: 旧判据 `name[:6] in now` 把「长沙西服务区南区店」误判成「…北区店」
    (前 6 字相同) → 误判后下一店读到的还是上一店的数据。规则:
      · 归一化后全等 → 是;
      · 一方以另一方**完整开头/结尾**且被包含方 ≥6 字 —— 容忍「(装修中)」这类装饰后缀,
        同时挡住短名互相包含的误判。
    """
    a, b = _gj_norm_name(now), _gj_norm_name(name)
    if not a or not b:
        return False
    if a == b:
        return True
    if len(b) >= 5 and (a.startswith(b) or a.endswith(b)):
        return True
    if len(a) >= 5 and (b.startswith(a) or b.endswith(a)):
        return True
    return False


def _sens_from_bodies(bodies):
    """敏感操作详情页响应体们 → {"times": 操作次数, "amt": 敏感金额} 或 None。

    真机结构(2026-10-08/09): POST /web/api/v2/bi/runtime/query/graph (queryErpngSenCountSQL)
    响应 data.summary 里**精确键**:
      "sensitiveTimes": {"targetNum": 6, ...}   ← IA 敏感操作次数
      "sensitiveAmt":   {"targetNum": 131.4,..} ← IB 敏感操作金额
    ⚠️ summary 里还有**同名后缀干扰键** sensitiveTimes_<id> = 操作次数占比(值 100.00%),
    必须精确键匹配, 前缀匹配会把百分比当次数写进模板。空壳响应(summary={})自然跳过。
    要求两个键同时存在才返回(缺一个 → None, 留空不写)。"""
    import json as _json
    for b in bodies:
        if "sensitiveAmt" not in b:          # 快速排除(连 json 都不用解)
            continue
        try:
            j = _json.loads(b)
        except Exception:
            continue
        s = ((j.get("data") or {}).get("summary")) or {}
        st, sa = s.get("sensitiveTimes"), s.get("sensitiveAmt")
        if not (isinstance(st, dict) and isinstance(sa, dict)):
            continue
        try:
            return {"times": float(st["targetNum"]), "amt": float(sa["targetNum"])}
        except (KeyError, TypeError, ValueError):
            continue
    return None


class MeituanGjAdapter:
    platform = "meituan_gj"

    def __init__(self, ctx, sink: EventSink, human_bridge, store_id="", keyword="", want=None):
        self.ctx = ctx
        self.sink = sink
        self.human = human_bridge
        self.page = None
        self.store_id = store_id or ""
        self.keyword = keyword or ""
        # want: 模板里的门店工作表名(地名关键词); 只有对得上的门店才进店抓取, 省时间
        self.want = [w for w in (want or []) if w]
        self.skipped_by_sheet = []

    # ---------- 登录 ----------
    def _find_login_frame(self, page, max_wait_s=75):
        """轮询等待 eepassport 登录 iframe (懒加载, 时序不稳)。"""
        end = time.time() + max_wait_s
        while time.time() < end:
            for fr in page.frames:
                if "eepassport.meituan.com" in fr.url:
                    return fr
            time.sleep(3)
        return None

    def _find_logged_in(self):
        """pos.meituan.com 页面即视为有会话(含 rms-account——门店选择页就挂在这个URL);
        排除的只是登录表单页(eepassport iframe / 验证码)。"""
        for p in self.ctx.pages:
            try:
                if "pos.meituan.com" not in p.url:
                    continue
                t = p.evaluate("() => document.body.innerText.slice(0, 600)") or ""
                if "请输入验证码" in t or "账号登录" in t:
                    continue
                if "pos.meituan.com/web/rms-account" in p.url and "请选择要登录" not in t and "运营中心" not in t:
                    # 纯登录跳转页(无会话)
                    continue
                return p
            except Exception:
                pass
        return None

    def _is_logged_in(self, page) -> bool:
        """有会话 = 页面出现 门店选择页 或 管家后台内容(顶栏菜单)。"""
        try:
            t = page.evaluate("() => document.body.innerText.slice(0, 900)") or ""
            if "请选择要登录" in t or "运营中心" in t or "报表中心" in t:
                return True
            for fr in page.frames:
                if "eepassport.meituan.com" in fr.url:
                    return False
            return False
        except Exception:
            return False

    def ensure_login(self, username="", password="", timeout_s=300):
        page = self._find_logged_in()
        if page:
            self.page = page
            self.sink.log("✅ 复用美团管家登录会话")
            return True
        self.page = page = self.ctx.new_page()
        page.goto(GJ_LOGIN, wait_until="commit")

        # 已在登录态(门店选择页 / 管家后台) → 直接复用, 别让用户白登一次。
        # 复现(2026-09-23): 编排器刚启动时 context 里没有 pos.meituan.com 页面,
        # _find_logged_in() 扫不到 → 走到这里; 页面其实就是门店选择页, 但旧的
        # 逻辑接着等 75s 登录表单(等不到) → 误报「转人工登录」。
        if self._is_logged_in(page):
            self.sink.log("✅ 复用美团管家登录会话(导航后确认)")
            return True

        lf = self._find_login_frame(page)
        if not lf:
            if self._is_logged_in(page):
                self.sink.log("✅ 复用美团管家登录会话(二次确认)")
                return True
            self.sink.log("⚠️ 登录表单加载超时, 转人工登录")
            self.sink.need_human("login", "请在浏览器手动登录美团管家", platform="meituan_gj")
            return self._wait_manual(page, timeout_s)

        self.sink.log("✅ 登录表单就绪")
        if not (username and password):
            self.sink.need_human("login", "未配置美团管家账号密码, 请在浏览器手动登录", platform="meituan_gj")
            return self._wait_manual(page, timeout_s)

        self.sink.log("📝 自动填充美团管家账号密码…")
        try:
            # ① 切「账号登录」tab (默认手机号验证码)
            self._switch_to_account_tab(lf)
            time.sleep(1)
            # ② 填账号密码 (iframe 内 locator)
            lf.locator("input[type='text']:visible").first.click(timeout=5000)
            lf.locator("input[type='text']:visible").first.fill(username)
            lf.locator("input[type='password']:visible").first.click(timeout=5000)
            lf.locator("input[type='password']:visible").first.fill(password)
            time.sleep(1)
            # ③ 勾选协议
            self._check_agreement(lf)
            time.sleep(1)
            # ④ 点登录
            self._click_login(lf)
            self.sink.log("已点击登录, 检测验证方式…")
            time.sleep(4)
            # ⑤ 短信验证: 自动点发送, 人工输码
            self._handle_sms(lf, timeout_s)
        except Exception as e:
            self.sink.log(f"⚠️ 自动登录异常: {str(e)[:80]}, 可转人工")

        return self._wait_manual(page, timeout_s)

    def _wait_manual(self, page, timeout_s):
        end = time.time() + timeout_s
        while time.time() < end:
            time.sleep(4)
            if self._is_logged_in(page):
                self.sink.log("✅ 美团管家登录成功")
                return True
        self.sink.step_fail("login", "登录", "美团管家登录超时", "E_LOGIN_TIMEOUT")
        return False

    def _switch_to_account_tab(self, lf):
        """默认手机号验证码 tab → 点「账号登录」。locator 优先, evaluate 兜底。"""
        try:
            for txt in ["账号登录", "账号密码登录", "密码登录"]:
                loc = lf.locator(f"text={txt}").first
                try:
                    if loc.count() > 0:
                        loc.click(timeout=4000)
                        self.sink.log("🔀 已切换到账号登录 tab")
                        return
                except Exception:
                    continue
        except Exception:
            pass
        # evaluate 兜底 (React 合成事件: bubbles MouseEvent 可触发)
        ok = lf.evaluate("""() => {
          const cands = [...document.querySelectorAll('*')].filter(el => {
            const own = Array.from(el.childNodes).filter(n=>n.nodeType===3)
              .map(n=>n.textContent).join('').trim();
            return ['账号登录','账号密码登录','密码登录'].includes(own);
          });
          for (const el of cands) {
            const r = el.getBoundingClientRect();
            if (r.width > 0) {
              el.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true, view: window}));
              return true;
            }
          }
          return false;
        }""")
        self.sink.log("🔀 已切换到账号登录 tab" if ok else "⚠️ 未找到「账号登录」tab, 按当前表单继续")

    def _check_agreement(self, lf):
        """勾选协议(文字行左侧自绘checkbox, 在 iframe 内用 evaluate 定位后 locator 点击)。"""
        try:
            box = lf.evaluate("""() => {
              const els = [...document.querySelectorAll('*')].filter(el => {
                const own = Array.from(el.childNodes).filter(n=>n.nodeType===3)
                  .map(n=>n.textContent).join('');
                return own.includes('我已阅读');
              });
              if (!els.length) return null;
              const r = els[0].getBoundingClientRect();
              return {x: r.x, y: r.y, w: r.width, h: r.height};
            }""")
            if not box:
                self.sink.log("ℹ️ 无协议勾选行, 跳过")
                return
            # 点「我已阅读」文字本身(多数实现点文字也切换; 左侧坐标在 iframe 外无法用 page.mouse)
            loc = lf.locator("text=我已阅读").first
            try:
                loc.click(timeout=4000)
                self.sink.log("☑️ 已勾选协议")
            except Exception:
                # 兜底: 对文字元素本身派发 click
                lf.evaluate("""() => {
                  const els = [...document.querySelectorAll('*')].filter(el => {
                    const own = Array.from(el.childNodes).filter(n=>n.nodeType===3)
                      .map(n=>n.textContent).join('');
                    return own.includes('我已阅读');
                  });
                  if (els.length) els[0].dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true, view: window}));
                }""")
                self.sink.log("☑️ 已勾选协议(dispatch)")
        except Exception as e:
            self.sink.log(f"⚠️ 勾选协议失败: {str(e)[:60]}")

    def _click_login(self, lf):
        for sel in ["button:has-text('登录')", "[class*='btn']:has-text('登录')"]:
            try:
                loc = lf.locator(sel).first
                if loc.count() > 0:
                    loc.click(timeout=4000)
                    return
            except Exception:
                continue
        # evaluate 兜底: 找「登录」叶子元素派发 click
        lf.evaluate("""() => {
          const cands = [...document.querySelectorAll('*')].filter(el => {
            const own = Array.from(el.childNodes).filter(n=>n.nodeType===3)
              .map(n=>n.textContent).join('').trim();
            return own === '登录';
          });
          for (const el of cands) {
            const r = el.getBoundingClientRect();
            if (r.width > 20) {
              el.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true, view: window}));
              return true;
            }
          }
          return false;
        }""")

    def _handle_sms(self, lf, timeout_s=240) -> bool:
        """短信验证(iframe 内): 自动点「发送验证码」→ 提醒人工输码 → 轮询弹窗消失。"""
        def sms_present():
            try:
                t = lf.evaluate("() => document.body ? document.body.innerText : ''") or ""
                return ("发送验证码" in t or "获取验证码" in t)
            except Exception:
                return False

        appeared = False
        for _ in range(4):
            time.sleep(3)
            if sms_present():
                appeared = True
                break
        if not appeared:
            return False
        self.sink.log("📱 检测到短信验证, 自动点击发送验证码…")
        try:
            for sel in ["text=发送验证码", "text=获取验证码", "text=重新获取"]:
                loc = lf.locator(sel).first
                if loc.count() > 0:
                    loc.click(timeout=4000)
                    break
            self.sink.log("✅ 已点击发送验证码")
        except Exception as e:
            self.sink.log(f"⚠️ 点击发送验证码失败: {str(e)[:60]}")
        self.sink.need_human("sms_code", "请查收手机短信, 在浏览器中输入验证码并确认", platform="meituan_gj")
        end = time.time() + timeout_s
        while time.time() < end:
            time.sleep(4)
            if not sms_present():
                return True
        return False

    # ---------- 报表下载+抓取(2026-09-21 实测链路) ----------
    # 页面要点(实测):
    # ① 登录成功后停在「请选择要登录的集团/门店」卡片页(每店一个「选 择」按钮, 文案带空格)
    # ② 点卡片「选择」→ pos 后台; 顶部菜单 报表中心 → 报表中心首页 → 点「营业概览」卡片
    # ③ 营业概览页: 日期双input(readonly, 默认今天) + 查询 + 「导出」按钮 → 直接下载 xlsx
    # ④ 导出文件 4 sheets(营业/收款/菜品/顾客); 「营业」sheet 的订单分类构成表 = HO~HT 数据源
    # ⑤ 无全部门店聚合视图 → scope=all = 循环每家门店逐店导出(脑图口径: 一店一Sheet)
    _SELECT_PAGE_MARK = "请选择要登录"
    _SELECT_URL = "https://pos.meituan.com/web/rms-account#/selectorg"

    def _wait_until(self, cond, timeout_s=20, poll=0.4, what=""):
        """条件等待: 满足就立刻返回(比固定 sleep 快), 超时返回 False(不静默拖长)。

        cond() 内部自己 evaluate, **必须自己吞掉导航异常**(切店/进店会触发 SPA 导航,
        期间 evaluate 抛 "Execution context was destroyed")。
        用途: 把「等页面渲染」的拍脑袋 sleep 换成等条件。
        注意: 用**轮次计数**而不是 time.time() —— 单测会把 time 换成只有 sleep 的替身。
        """
        for _ in range(max(1, int(timeout_s / poll))):
            try:
                if cond():
                    return True
            except Exception:
                pass
            time.sleep(poll)
        if what:
            self.sink.log(f"⏱ 等待「{what}」超时({timeout_s}s)，按原流程继续", level="warn")
        return False

    def _text_of(self, page):
        """安全取页面文本(导航期间 evaluate 会抛, 返回空串)。"""
        try:
            return page.evaluate("() => document.body.innerText") or ""
        except Exception:
            return ""

    def _visible(self, page, text, maxw=300):
        """页面上是否存在可见的、文本等于 text 的元素。"""
        try:
            return bool(page.evaluate("""(a) => {
              const [t, maxw] = a;
              for (const el of document.querySelectorAll('span,div,li,a,button')) {
                if ((el.textContent || '').trim() !== t) continue;
                if (el.children.length > 3) continue;
                const r = el.getBoundingClientRect();
                if (r.width <= 0 || r.height <= 0 || r.width > maxw) continue;
                return true;
              }
              return false;
            }""", [text, maxw]))
        except Exception:
            return False

    def _open_store_switch(self, page):
        """打开右上角「选择机构」弹窗。返回 True=弹窗已出现。

        入口: 顶栏 `.perspective-switch`(显示当前店名+商户号) —— 必须用**真鼠标事件**,
        JS 合成 click 唤不出 React 弹窗(实测)。弹窗本体 `.org-switch-modal`。
        """
        # 已开着(上一店失败可能留下) → 直接用, 别再点入口(会把刚开的弹窗点关掉)
        if page.evaluate(MODAL_VIS_JS):
            return True
        box = page.evaluate("""() => {
          const el = document.querySelector('.perspective-switch') ||
                     document.querySelector('.p-switch-container');
          if (!el) return null;
          const r = el.getBoundingClientRect();
          if (r.width <= 0) return null;
          return {x: r.x + r.width / 2, y: r.y + r.height / 2};
        }""")
        if not isinstance(box, dict) or "x" not in box:
            return False
        page.mouse.move(box["x"], box["y"])
        time.sleep(0.4)
        page.mouse.click(box["x"], box["y"])
        # 判定用**可见性**(antd 关闭后 DOM 残留: 存在≠打开); 首次点击可能落空(还在导航) → 再点一次
        for round_i in range(2):
            for _ in range(10):
                time.sleep(0.5)
                try:
                    if page.evaluate(MODAL_VIS_JS):
                        return True
                except Exception:
                    # 点击可能触发 SPA 导航 → evaluate 报 "Execution context was destroyed"
                    pass
            if round_i == 0:
                page.mouse.move(box["x"], box["y"])
                time.sleep(0.4)
                page.mouse.click(box["x"], box["y"])
        return False

    def _header_store(self, page):
        """表头当前门店名(用于确认切换真的生效)。"""
        return (page.evaluate("""() => {
          const el = document.querySelector('.perspective-switch') ||
                     document.querySelector('.p-switch-container');
          return el ? (el.innerText || '').trim().split('\\n')[0] : '';
        }""") or "").strip()

    def _switch_store(self, page, name, timeout_s=45):
        """右上角弹窗切店(不必退回门店选择页)。返回 True=已切到 name。

        步骤: 开弹窗 → 搜索框输门店全名 → 表格里点该行(跳过标着「当前」的行) →
        等弹窗关闭且表头店名变成目标店。

        ⚠️ 2026-10-11 真机修复(探针 v3/v5+e2e 钉死切店失败/切错店的根因):
          ① 列表异步加载: 行进 DOM 时上面还盖着 `saas-ui-spin` 遮罩, 盲点坐标会点在
             遮罩上被吞(表头不变), 旧代码只点一次然后干等 45s → 现在必须等
             "该点真的命中该行"(elementFromPoint, 且连续两次读数稳定)才点;
          ② 点前**即时复核**坐标仍命中目标行(防列表重排后点到别的行 —— e2e 实测
             同一坐标 0.3s 后可能变成另一家店的行); 复核不过就刷新坐标再点;
          ③ 点后 8s 表头没变 → 重取坐标**再点**(弹窗被点关就重开), 最多 3 轮 ——
             探针实测第二点 5~7s 成功;
          ④ 成功判据从 `name[:6]` 前缀改为**全名**(修复「长沙西服务区南区店/北区店」
             这类前 6 字相同的店互相误判 —— 误判后下一店读到的还是上一店的数据)。
        """
        if not self._open_store_switch(page):
            self.sink.log("⚠️ 未找到右上角门店切换入口(.perspective-switch)", level="warn")
            return False
        # 搜索: 必须用**真键盘输入** —— React 受控输入直接赋 value 不触发 onChange,
        # 表格不会过滤, 目标行不在首屏就找不到(2026-09-23 真机踩到)。
        typed = False
        try:
            inp = page.locator("input[placeholder*='机构名称']").first
            inp.click(timeout=6000)
            inp.fill("")
            inp.type(name, delay=25)
            typed = True
        except Exception as e:
            self.sink.log(f"⚠️ 切换弹窗搜索框输入失败(试 JS 兜底): {str(e)[:60]}", level="warn")
        if not typed:
            typed = page.evaluate("""(kw) => {
              const ins = [...document.querySelectorAll('input')].filter(i => {
                const r = i.getBoundingClientRect();
                return r.width > 60 && r.height > 0 &&
                       String(i.placeholder || '').includes('机构名称');
              });
              const el = ins[0];
              if (!el) return false;
              el.focus(); el.value = kw;
              el.dispatchEvent(new Event('input', {bubbles: true}));
              el.dispatchEvent(new Event('change', {bubbles: true}));
              return true;
            }""", name)
        if not typed:
            self.sink.log("⚠️ 门店切换弹窗里没找到搜索框", level="warn")
            return False
        # 点该行(真鼠标; 带「当前」的那行不点)。搜索词退化: 全名 → 地名关键字
        from .. import sheet_match as _sm          # 注意: platforms 子包内是 `..`(曾误写 `.` → ImportError)
        cands = [name]
        _short = _sm.extract_place(name)
        if _short and _short != name:
            cands.append(_short)
        t_all0 = time.time()
        last_txt = ""
        for attempt in range(3):
            if attempt:
                # 上一轮疑似已生效只是慢? 先看表头; 弹窗被点关了 → 重开(开前会自检)
                try:
                    now0 = self._header_store(page)
                except Exception:
                    now0 = ""
                if _gj_same_store(now0, name):
                    self.sink.log(f"🔀 右上角切换门店 → {now0}"
                                  f"（约 {time.time() - t_all0:.1f}s，未退回选择页·迟生效）")
                    return True
                try:
                    if not page.evaluate(MODAL_VIS_JS) and not self._open_store_switch(page):
                        break
                except Exception:
                    pass
            hit = None
            kw_used = ""
            for i, kw in enumerate(cands):
                if attempt or i:   # 重试/换退化词: 先清空搜索框重输, 否则过滤结果不会刷新
                    try:
                        inp = page.locator("input[placeholder*='机构名称']").first
                        inp.fill("")
                        inp.type(kw, delay=25)
                    except Exception:
                        pass
                hit = self._wait_row_clickable(page, kw, wait_s=10 if attempt == 0 else 6)
                if hit:
                    kw_used = kw
                    break
            if not hit:
                if attempt == 0:
                    self.sink.log(f"⚠️ 切换弹窗里没找到「{name}」的行(搜索词 {cands} 都试过)",
                                  level="warn")
                break
            last_txt = hit.get("txt", "")
            if not self._click_row_hit(page, hit, kw_used):
                continue    # 坐标都没通过复核 → 下一轮重找
            # 点后每 0.5s 看表头; 实测 5~7s 变(单轮最多 8s, 没变就重取坐标再点)
            for _w in range(16):
                time.sleep(0.5)
                try:
                    now = self._header_store(page)
                except Exception:
                    now = ""     # 切店=SPA 导航, 中途 evaluate 会报 "Execution context was destroyed"
                if _gj_same_store(now, name):
                    time.sleep(1.5)
                    extra = f"，第 {attempt + 1} 次点击" if attempt else ""
                    self.sink.log(f"🔀 右上角切换门店 → {now}（约 {time.time() - t_all0:.1f}s，"
                                  f"未退回选择页{extra}）")
                    return True
            # 没变 → 下一轮: 表头可能已变到"别的店"(点错行) → 重开弹窗重找即可纠正
        try:
            _cur = self._header_store(page)
        except Exception:
            _cur = ""
        if _gj_same_store(_cur, name):
            self.sink.log(f"🔀 右上角切换门店 → {_cur}（稍晚生效，未退回选择页）")
            return True
        if last_txt:
            self.sink.log(f"⚠️ 点了「{last_txt[:30]}」但表头没变成目标店(当前: {_cur})",
                          level="warn")
        return False

    def _wait_row_clickable(self, page, kw, wait_s=10):
        """在切店弹窗里找目标行; 等「真的点得到」且**连续两次读数稳定**再给坐标。

        真机根因(探针 v3/v5): 列表异步加载时行已进 DOM, 但上面还盖着 `saas-ui-spin`
        遮罩 —— 盲点坐标会点在遮罩上被吞(表头永不变, 白等 45s)。e2e 补充证据: 列表
        重排会让同一坐标在 0.3s 后变成另一家店的行 → 必须"稳定"后才交给点击环节
        (点击前还会再复核一次)。超时返回最后一次读数(外层会复核/重试), 没有行则 None。
        """
        tries = max(1, int(float(wait_s) / 0.3))
        prev = None
        for _ in range(tries):
            try:
                hit = page.evaluate(_ROW_HIT_JS, kw)
            except Exception:
                hit = None
            if hit and (hit.get("ok1") or hit.get("ok2")):
                if prev and abs(hit["px"] - prev["px"]) <= 1 and abs(hit["py"] - prev["py"]) <= 1 \
                        and abs(hit["cx"] - prev["cx"]) <= 1 and abs(hit["cy"] - prev["cy"]) <= 1:
                    return hit
                prev = hit
            time.sleep(0.3)
        return prev

    def _click_row_hit(self, page, hit, kw):
        """按已定位坐标点行; 点前**即时复核**坐标仍命中该行(防列表重排点到别行)。

        复核不过 → 重新取行坐标再试, 最多刷 2 次; 全部不过 → 不发点击(返回 False,
        由外层重试, 绝不点"没验证过"的坐标)。
        """
        for _ in range(3):
            pts = []
            if hit.get("ok1"):
                pts.append((hit["px"], hit["py"]))
            pts.append((hit["cx"], hit["cy"]))
            if not hit.get("ok1"):
                pts.append((hit["px"], hit["py"]))
            for x, y in pts:
                page.mouse.move(x, y)
                time.sleep(0.1)
                try:
                    ver = page.evaluate(_VERIFY_JS, [x, y, kw])
                except Exception:
                    ver = None
                if ver and ver.get("ok"):
                    page.mouse.click(x, y)
                    return True
            # 两点都没过复核(列表在动) → 重新取行坐标(含 scrollIntoView)再试
            try:
                hit = page.evaluate(_ROW_HIT_JS, kw)
            except Exception:
                hit = None
            if not hit:
                return False
        return False

    def _select_store(self, page, keyword):
        """门店选择页: 按 keyword 匹配卡片并点「选择」。返回 True=成功进入后台。"""
        for attempt in range(3):
            txt = self._text_of(page)
            if not txt:            # 还在导航 → 这一轮不算, 继续等
                time.sleep(1)
                continue
            if self._SELECT_PAGE_MARK not in txt:
                return True   # 已在后台
            got = False
            for _wait_i in range(20):   # 卡片异步渲染: 等它出现再点(最多 ~10s)
                got = page.evaluate("""(kw) => {
              const cards = [...document.querySelectorAll('div,li')].filter(el => {
                const t = el.textContent || '';
                return t.includes(kw) && el.querySelectorAll('div').length <= 8 && t.length < 100;
              });
              for (const card of cards) {
                for (const el of card.querySelectorAll('button,span,div,a')) {
                  if ((el.textContent || '').replace(/\\s/g, '') === '选择') {
                    if (el.getBoundingClientRect().width > 0) { el.click(); return true; }
                  }
                }
              }
              return false;
            }""", keyword)
                if got:
                    break
                time.sleep(0.5)

            if not got:
                self.sink.log(f"⚠️ 门店选择页无匹配「{keyword}」的卡片", level="warn")
                return False
            # 进店 = SPA 导航 → 等「门店选择页」消失(实测 2~4s; 固定 12s 是白等)
            self._wait_until(lambda: self._SELECT_PAGE_MARK not in self._text_of(page),
                             timeout_s=20, what="进入门店后台")
        return self._SELECT_PAGE_MARK not in self._text_of(page)

    def _biz_ready(self, page):
        """营业概览页**真的就绪**的判据: URL 含 business-report + 有日期框 + 有收入区块。
        只看 URL 不够(SPA 路由已变但内容还没渲染), 只看文本也不够(别处也有"营业额"字样)。"""
        try:
            if "business-report" not in (page.url or ""):
                return False
            return bool(page.evaluate("""() => {
              const t = document.body.innerText || '';
              if (!(t.includes('营业收入') || t.includes('营业额'))) return false;
              return [...document.querySelectorAll('input')].some(i =>
                (i.placeholder || '').includes('请选择日期') ||
                String(i.className || '').includes('ant-calendar-picker'));
            }"""))
        except Exception:
            return False

    def _text_brief(self, page, n=220):
        """页面文本摘要: **折叠连续重复片段**。
        管家页面会把「品牌商-示例店长 00001」刷几百遍, 直接截前 200 字 → 诊断包里全是噪音,
        等于没信息(2026-09-28 用户诊断包实测)。"""
        try:
            t = re.sub(r"\s+", " ", self._text_of(page) or "").strip()
        except Exception:
            return "(页面读取失败)"
        parts = t.split(" ")
        out, i = [], 0
        while i < len(parts) and len(" ".join(out)) < n:
            # 找最小重复单元(1~3 词): 管家页面是「品牌商-示例店长 00001」这种**两词交替**刷屏,
            # 只折叠单词重复没用 —— 2026-09-28 诊断包里就是这么被灌满的
            unit = 1
            for k in (2, 3, 1):
                if i + 2 * k <= len(parts) and parts[i:i + k] == parts[i + k:i + 2 * k]:
                    unit = k
                    break
            cnt = 1
            while parts[i + cnt * unit:i + (cnt + 1) * unit] == parts[i:i + unit] and \
                    len(parts[i + cnt * unit:i + (cnt + 1) * unit]) == unit:
                cnt += 1
            seg = " ".join(parts[i:i + unit])
            out.append(seg + (f"×{cnt}" if cnt > 1 else ""))
            i += cnt * unit
        return " ".join(out)[:n]

    def _open_biz_overview(self, page):
        """打开「营业概览」(返回时页面已就绪, 日期框可取)。

        2026-09-28 真机定位: 31 店连跑约 4 家报「报表中心未找到「营业概览」入口」——
        侧边栏现在只有「营业报表」这个可展开父级, 菜单异步渲染时机不稳, 页面停在
        rms-report/home(文本被「品牌商-示例店长 00001」刷屏), 靠重试点击救不回来。
        改为 **优先直连路由**(#/rms-report/business-report 实测直接渲染营业概览,
        含营业收入 + 两个 antd 日期框), 直连不成才回退点菜单老路。
        """
        # ① 首选: 直连路由(不依赖菜单渲染时机)
        for attempt in range(2):
            try:
                if not self._biz_ready(page):
                    page.goto(GJ_BIZ_OVERVIEW, wait_until="commit")
                if self._wait_until(lambda: self._biz_ready(page), timeout_s=25,
                                    what="营业概览页"):
                    return page
            except Exception as e:
                self.sink.log(f"ℹ️ 直连营业概览第{attempt + 1}次失败: {str(e)[:70]}", level="warn")
            time.sleep(2)
        self.sink.log("ℹ️ 直连营业概览未就绪 → 回退点菜单", level="warn")

        # ② 回退: 点菜单(路由若再改版还有救)
        def click(t, maxw=260):
            box = page.evaluate("""(a) => {
              const [t, maxw] = a;
              const out = [];
              for (const el of document.querySelectorAll('span,div,li,a,button')) {
                const tt = (el.textContent || '').trim();
                if (tt !== t) continue;
                if (el.children.length > 3) continue;
                const r = el.getBoundingClientRect();
                if (r.width <= 0 || r.height <= 0 || r.width > maxw) continue;
                out.push({x: r.x + r.width / 2, y: r.y + r.height / 2, w: r.width});
              }
              out.sort((p, q) => p.w - q.w);
              return out[0] || null;
            }""", [t, maxw])
            if box:
                page.mouse.click(box["x"], box["y"])
            return box

        sub_ok = False
        for rnd in range(3):
            if click("报表中心"):
                self._wait_until(lambda: self._visible(page, "营业概览")
                                 or self._visible(page, "营业报表"),
                                 timeout_s=12, what="报表中心子菜单")
                sub_ok = True
                break
            time.sleep(3)
        got = None
        for rnd in range(4):
            # 入口名改过版: 旧名「营业概览」/ 新名「营业报表」
            got = click("营业概览") or click("营业报表")
            if got:
                break
            time.sleep(2 + rnd * 2)
        if not got:
            raise RuntimeError(
                f"美团管家: 直连与点菜单都没能打开「营业概览」"
                f"（报表中心={'已点开' if sub_ok else '未点开'}｜{page.url[:80]}"
                f"｜页面文本: {self._text_brief(page)}）")
        # 等营业概览页真的就绪(URL + 日期框 + 收入区块 三者齐全)
        if not self._wait_until(lambda: self._biz_ready(page), timeout_s=25, what="营业概览页"):
            raise RuntimeError(f"美团管家: 营业概览未就绪({page.url[:80]}"
                               f"｜页面文本: {self._text_brief(page)}）")
        return page

    def _set_gj_dates(self, page, df, dt):
        """营业概览日期选择(antd v3 Calendar, 面板挂 body 下)。

        2026-10-09 根治(探针 v1/v2 真机取证) —— 旧版两个致命 bug:
          ① 旧「快捷」分支逻辑写错: 任何比昨天更早的日期都被当成「昨日」处理(且该按钮
             实际找不到, 分支空跑);
          ② 面板格子**只按日号匹配、不认月份**: 跨月时点到错误月/未来格(点不动),
             且**静默继续** → 页面日期没变、数据是别天的, 却被写进目标日期行(用户 09-24 回补踩到)。
        现在: 一律走面板 —— 按年月差翻月 → `td[title='2026年9月24日']` **全日期**精确匹配 →
        起点/终点各点一次(单日=同格点两次; 真机行为: 第一次点击后视图会**跳回当前月**,
        所以每次点击前都重新翻月) → **读回两个输入框值必须 == 目标, 否则重试一轮、再不符即抛错**
        (宁可不写, 不写错)。
        """
        df, dt = self._gj_norm_date(df), self._gj_norm_date(dt)
        # 「查询」按钮(页面筛选区): 点它同时把面板收起、数据按新日期刷新(保留旧行为)
        def click(t, maxw=260):
            box = page.evaluate("""(a) => {
              const [t, maxw] = a;
              const out = [];
              for (const el of document.querySelectorAll('span,div,li,a,button,input')) {
                const tt = (el.textContent || '').trim() || (el.placeholder || '');
                if (tt !== t) continue;
                if (el.children.length > 3) continue;
                const r = el.getBoundingClientRect();
                if (r.width <= 0 || r.height <= 0 || r.width > maxw) continue;
                out.push({x: r.x + r.width / 2, y: r.y + r.height / 2, w: r.width});
              }
              out.sort((p, q) => p.w - q.w);
              return out[0] || null;
            }""", [t, maxw])
            if box:
                page.mouse.click(box["x"], box["y"])
            return box

        # 开面板: 先等输入框**可见**(切店/重渲染后可能短暂不存在), 再短超时点击(不闷等 30s)
        inp = None
        for _ in range(10):
            try:
                inp = page.locator("input[placeholder*='请选择日期']").first
                if inp.is_visible():
                    break
            except Exception:
                inp = None
            time.sleep(1)
        if inp is None or not inp.is_visible():
            raise RuntimeError(f"美团管家: 日期输入框未就绪(期望 {df}~{dt})")
        inp.click(timeout=8000)
        opened = False
        for _ in range(10):
            time.sleep(1)
            if self._gj_panel_visible(page):
                opened = True
                break
        if not opened:
            raise RuntimeError(f"美团管家: 日期面板未弹出(期望 {df}~{dt})")

        # 起点 → (面板还开着的话)终点; 单日=同一格点两次
        self._gj_click_day(page, df)
        if self._gj_panel_visible(page):
            self._gj_click_day(page, dt)

        got = self._gj_dates_applied(page, df, dt)
        if not got:
            # 自动重试一轮(真机偶发: 面板半状态/点击未登记); 仍不符 → 抛错, 绝不带错日期继续
            self.sink.log(f"⚠️ 日期未生效({df}~{dt}), 自动重试一轮…", level="warn")
            if not self._gj_panel_visible(page):
                page.locator("input[placeholder*='请选择日期']").first.click(timeout=8000)
                for _ in range(8):
                    time.sleep(1)
                    if self._gj_panel_visible(page):
                        break
            self._gj_click_day(page, df)
            if self._gj_panel_visible(page):
                self._gj_click_day(page, dt)
            got = self._gj_dates_applied(page, df, dt)
        if not got:
            raise RuntimeError(f"美团管家: 日期未生效(期望 {df}~{dt}, 实际 {self._gj_date_inputs(page)})")
        self.sink.log(f"  📅 日期已选: {got[0]}~{got[1]}")
        if click("查询"):
            time.sleep(4.5)

    @staticmethod
    def _gj_norm_date(v):
        """'2026/09/24' → '2026-09-24'(统一比较格式)。"""
        return (v or "").strip().replace("/", "-")

    def _gj_date_inputs(self, page):
        """日期输入框(『请选择日期』)当前值列表。两个只读框=已生效的起止日; 面板打开时或出现第三个副本。"""
        return page.evaluate("""() => { /*GJ:inputs*/
          const o = [];
          for (const el of document.querySelectorAll("input[placeholder*='请选择日期']")) o.push(el.value || '');
          return o;
        }""") or []

    def _gj_panel_visible(self, page):
        return bool(page.evaluate("""() => { /*GJ:panel-open*/
          return !!document.querySelector('.ant-calendar-picker-container:not(.ant-calendar-picker-container-hidden)');
        }"""))

    def _gj_panel_head(self, page):
        """面板头文本(如 '2026年10月'); 面板不在则 None。"""
        return page.evaluate("""() => { /*GJ:head*/
          const dd = document.querySelector('.ant-calendar-picker-container:not(.ant-calendar-picker-container-hidden)');
          const h = dd ? dd.querySelector('.ant-calendar-header') : null;
          return h ? h.innerText.replace(/\\n/g, ' ') : null;
        }""")

    def _gj_panel_nav(self, page, y, m, max_click=30):
        """按年月差翻月到 (y, m)(以面板头为准)。到达返回 True。"""
        for _ in range(max_click + 1):
            mm = re.search(r"(\d{4})年(\d{1,2})月", self._gj_panel_head(page) or "")
            if mm and (int(mm.group(1)), int(mm.group(2))) == (y, m):
                return True
            cur = (int(mm.group(1)), int(mm.group(2))) if mm else None
            box = page.evaluate("""(d) => { /*GJ:navbtn*/
              const dd = document.querySelector('.ant-calendar-picker-container:not(.ant-calendar-picker-container-hidden)');
              if (!dd) return null;
              const b = dd.querySelector(d > 0 ? '.ant-calendar-next-month-btn' : '.ant-calendar-prev-month-btn');
              if (!b) return null;
              const r = b.getBoundingClientRect();
              if (r.width <= 0) return null;
              return {x: r.x + r.width / 2, y: r.y + r.height / 2};
            }""", 1 if (cur is None or (y, m) > cur) else -1)
            if not box:
                return False
            page.mouse.click(box["x"], box["y"])
            time.sleep(1.1)
        return False

    def _gj_click_day(self, page, ymd):
        """翻到该日所在月 → `td[title]` 全日期精确匹配 → 点一次。返回是否点出。"""
        y, m, d = (int(x) for x in ymd.split("-"))
        if not self._gj_panel_nav(page, y, m):
            return False
        box = page.evaluate("""(t) => { /*GJ:cell*/
          const dds = [...document.querySelectorAll('.ant-calendar-picker-container:not(.ant-calendar-picker-container-hidden)')];
          for (const dd of dds) {
            const td = dd.querySelector("td[title='" + t + "']");
            if (!td) continue;
            const c = String(td.className || '');
            if (c.includes('disabled')) continue;
            const r = td.getBoundingClientRect();
            if (r.width <= 0 || r.height <= 0) continue;
            return {x: r.x + r.width / 2, y: r.y + r.height / 2};
          }
          return null;
        }""", f"{y}年{m}月{d}日")
        if not box:
            return False
        page.mouse.click(box["x"], box["y"])
        time.sleep(1.6)
        return True

    def _gj_dates_applied(self, page, df, dt, timeout_s=8):
        """等「面板已关 且 两个输入框值==目标」。成功返回 [起, 止], 否则 None。"""
        t0 = time.time()
        while time.time() - t0 < timeout_s:
            got = [self._gj_norm_date(v) for v in self._gj_date_inputs(page)]
            if (not self._gj_panel_visible(page)) and len(got) >= 2 and got[0] == df and got[1] == dt:
                return got[:2]
            time.sleep(0.6)
        return None

    def _read_store_cards(self, page, tries=30, wait=0.5):
        """读「门店选择页」的门店卡片 [{name, mid, sid}]。

        sid=机构编码(12位, 2026-10-11 探针: 卡片文本含「机构编码：xxxxxxxxxxxx」,
        与《门店明细》H 列同口径) —— 门店明细「编号锁定」映射用。

        ⚠️ 卡片是**异步渲染**的(2026-10-09 真机): 「请选择要登录…」文本先出现、门店卡片后到 ——
        直接读一次会偶发拿到空列表 → 误报「无匹配门店」把任务拦死。这里等卡片出现(默认最多 15s)。
        """
        cards = []
        for _i in range(tries):
            cards = page.evaluate("""() => {
              const out = [];
              const seen = new Set();
              for (const btn of document.querySelectorAll('button,span,div,a')) {
                if ((btn.textContent || '').replace(/\\s/g, '') !== '选择') continue;
                const r0 = btn.getBoundingClientRect();
                if (r0.width <= 0) continue;
                let card = btn.parentElement;
                for (let i = 0; i < 5 && card; i++) {
                  const t = card.textContent || '';
                  const m = t.match(/([\\u4e00-\\u9fa5A-Za-z0-9·\\(\\)（）]+店)\\s*商户号[：:]?(\\d+)/);
                  const m2 = t.match(/机构编码[：:]?([0-9]+)/);
                  if (m) {
                    if (!seen.has(m[2])) {
                      seen.add(m[2]);
                      out.push({name: m[1].replace(/^门店/, ''), mid: m[2], sid: m2 ? m2[1] : ""});
                    }
                    break;
                  }
                  card = card.parentElement;
                }
              }
              return out;
            }""")
            if cards:
                break
            time.sleep(wait)
        return cards or []


    def download_reports(self, ddir, timeout_s=300, date_from=None, date_to=None):
        """DOM 直读模式(脑图口径: 内容抓取页面数据, 免导出):
        逐店进入后台 → 营业概览 → 设日期 → 直接读页面数字(带渠道合计交叉校验) → 存 self.scraped
        scope=all: 遍历门店选择页全部卡片; 单店: keyword 匹配一家。"""
        import os as _os
        _os.makedirs(str(ddir), exist_ok=True)
        self.download_dir = str(ddir)
        page = self.page if (self.page and not self.page.is_closed()) else self.ctx.new_page()
        self.page = page
        if "pos.meituan.com" not in page.url:
            page.goto(GJ_LOGIN, wait_until="commit")
            # 等门店选择页/后台就绪(实测 3~5s; 固定 12s 是白等)
            self._wait_until(lambda: (self._SELECT_PAGE_MARK in self._text_of(page)
                                      or self._is_logged_in(page)),
                             timeout_s=30, what="管家页面就绪")
        txt_now = self._text_of(page)
        if not txt_now:
            # 还在导航(document.body 为 null) → 等页面可读再判定, 否则误报「未登录」
            self._wait_until(lambda: bool(self._text_of(page)), timeout_s=15, what="页面可读")
            txt_now = self._text_of(page)
        if not self._is_logged_in(page) and self._SELECT_PAGE_MARK not in txt_now:
            raise RuntimeError("美团管家未登录")

        df = (date_from or "").replace("-", "/")
        dt = (date_to or date_from or "").replace("-", "/")
        self.scraped = {}
        self.store_ids = {}     # 选择页卡片读到的「机构编码」(编号锁定映射用)

        txt = self._text_of(page)
        ran = 0
        if self._SELECT_PAGE_MARK in txt:
            stores = self._read_store_cards(page)
            self.store_ids = {s["name"]: s.get("sid") for s in (stores or []) if s.get("sid")}
            if not stores:
                self.sink.log("⚠️ 门店选择页等 15s 没渲染出门店卡片(网络慢或页面改版)", level="warn")
            if self.keyword:
                stores = [s for s in stores if self.keyword in s["name"]]
                if not stores:
                    raise RuntimeError(f"美团管家门店选择页无匹配「{self.keyword}」")
            # 只抓模板里有对应 Sheet 的门店(模板是权威): 其余连进店都不进
            self.skipped_by_sheet = []
            if self.want:
                from .. import sheet_match
                keep = [s for s in stores if sheet_match.match_sheet(self.want, s["name"])[0]]
                if not keep:
                    raise RuntimeError(
                        f"门店选择页 {len(stores)} 家没有一家能对上模板 Sheet（模板门店表: "
                        f"{'、'.join(self.want[:8])}）")
                self.skipped_by_sheet = [s["name"] for s in stores if s not in keep]
                stores = keep
                if self.skipped_by_sheet:
                    self.sink.log(f"⏭ 跳过 {len(self.skipped_by_sheet)} 家模板里没有的门店: "
                                  + "、".join(self.skipped_by_sheet[:6])
                                  + ("…" if len(self.skipped_by_sheet) > 6 else ""), level="warn")
            self.sink.log(f"🏪 门店选择页共 {len(stores)} 家待跑(DOM直读模式)")
            self.failed = []
            aborted = False
            entered = False     # 已在某店后台? 第一家从门店选择页进, 之后用右上角弹窗切(省 ~10s/店)
            for i, s in enumerate(stores):
                _t0 = time.time()
                self.sink.log(f"▶ [{i + 1}/{len(stores)}] {s['name']}")
                data = None
                self._last_err = ""
                for attempt in range(2):
                    # 进店: 第一家(或掉回选择页后)走门店选择页「选 择」;
                    # 其余走**后台右上角「选择机构」弹窗**直接切 —— 不必退回上一页再进来
                    if entered:
                        try:
                            ok_enter = self._switch_store(page, s["name"])
                        except Exception as e:
                            # 切店环节任何异常都不该弄死整个门店循环(实测: SPA 导航期间 evaluate 抛错)
                            self.sink.log(f"⚠️ 右上角切店异常({str(e)[:60]})，回退旧路径", level="warn")
                            ok_enter = False
                        if not ok_enter:
                            self.sink.log("↩️ 弹窗切换没成功，回退到门店选择页重进", level="warn")
                            page.goto(self._SELECT_URL, wait_until="commit")
                            # 等门店卡片回来(实测 2~3s; 固定 6s 是白等)
                            self._wait_until(lambda: self._SELECT_PAGE_MARK in self._text_of(page),
                                             timeout_s=15, what="门店选择页")
                            entered = False
                            ok_enter = self._select_store(page, s["name"])
                            entered = ok_enter
                    else:
                        ok_enter = self._select_store(page, s["name"])
                        entered = ok_enter
                    if not ok_enter:
                        err = "没能进入该店后台（门店选择页「选 择」和右上角切换弹窗都试过了）"
                    else:
                        data = self._scrape_one(page, s["name"], df, dt)
                        err = self._last_err or "进入后台成功，但页面上没读到营业概览的渠道数据"
                    if data:
                        break
                    from ..failure_ui import ask_failure
                    res = ask_failure(self.sink, self.human,
                                      platform="美团管家", store=s["name"],
                                      date=df.replace("/", "-"),
                                      step=f"第 {i + 1}/{len(stores)} 家门店 · 营业概览读数",
                                      error_text=err, page=page, allow_skip=True,
                                      diagnose_dir=self.download_dir or None,
                                      attempt=attempt)
                    act = res.get("action")
                    if act == "abort":
                        aborted = True
                        break
                    if act in ("skip", "diagnose"):
                        data = None
                        break
                    # retry: 已在后台就直接再切一次同店; 否则下一轮会从选择页重进
                    if entered and self._switch_store(page, s["name"]):
                        continue
                    entered = False
                if aborted:
                    self.failed.append({"name": s["name"], "reason": "用户选择结束任务"})
                    self.sink.log("⛔ 已按你的选择结束任务（已完成的门店数据保留）", level="warn")
                    break
                if data:
                    self.scraped[s["name"]] = data
                    ran += 1
                else:
                    self.failed.append({"name": s["name"], "reason": (err or "")[:120]})
                self.sink.log(f"⏱ 本店用时 {time.time() - _t0:.1f}s（切店+读数+敏感操作，供提速分析）")
                # 不再每家都回门店选择页: 下一家直接用右上角弹窗切(实测 7.3s/店 vs 回页 18s+)
            if self.failed:
                self.sink.log(f"⚠️ {len(self.failed)} 家门店未取得数据: "
                              + "、".join(f["name"] for f in self.failed), level="warn")
        else:
            name = self.keyword or "当前门店"
            data = self._scrape_one(page, name, df, dt)
            if data:
                self.scraped[name] = data
                ran += 1
        if not ran:
            raise RuntimeError("美团管家: 无任何门店抓到数据")
        self.sink.step_ok("download", "页面抓取", f"美团管家 DOM 直读 {ran} 店")
        return True

    # ---------- 敏感操作(IA/IB) ----------
    # 「综合统计→敏感操作 详情」是 DPAAS 懒加载报表: 数据画在 canvas 上, DOM 文本读不到
    # (真机: 详情页 innerText 只有 463 字空壳)。唯一可靠取数 = 点进详情页, 拦
    # POST /web/api/v2/bi/runtime/query/graph 响应 (modelName=queryErpngSenCountSQL)。
    # 真机验证(2026-10-06 绿地之窗): times=6 / amt=131.4。
    def _capture_sensitive(self, page, timeout_s=40):
        """点「敏感操作 详情」→ 拦 XHR → {"times":.., "amt":..} 或 None(抓不到 → 留空不写)。
        结束后尽量回营业概览页(失败也无妨: 下一家 _scrape_one 会重新进概览)。

        2026-10-10 提速(仪器探针真机取证): 无数据店(全零日 12/12 实测)的查询响应是
        一次性到达的空壳包(t≈10~15s 共 9 个, 无敏感键), 之后页面**再无任何响应** ——
        旧逻辑傻等 40s 上限(实测 41.8s/店 → 12 家全零日店每轮白等 ≈8 分钟)。现在:
        查询响应到过、且**静默 ≥12s** 仍无可取值 → 提前收工(留空语义不变);
        有值 → 即时返回; 从未有响应 → 维持 40s 上限(情况不明不提前放弃)。
        """
        bodies = []
        state = {"seen": False, "last": 0.0}

        def _on_resp(r):
            try:
                if "runtime/query" in r.url:
                    state["seen"] = True
                    state["last"] = time.time()
                    try:
                        bodies.append(r.text())
                    except Exception:
                        pass
            except Exception:
                pass

        page.on("response", _on_resp)
        clicked = False
        try:
            for fr in page.frames:
                try:
                    loc = fr.locator("a[href*='sensitive-op']")
                    if loc.count():
                        loc.first.click(timeout=15000)
                        clicked = True
                        break
                except Exception:
                    continue
            if not clicked:
                return None
            deadline = time.time() + timeout_s
            quiet_s = 12.0
            while time.time() < deadline:
                got = _sens_from_bodies(bodies)
                if got:
                    return got
                if state["seen"] and (time.time() - state["last"]) >= quiet_s:
                    return None                    # 查询已完成+静默 → 该店该日无敏感数据
                time.sleep(0.75)                   # 0.75s 轮询(原 2.5s): 响应到了就走, 上限同 40s
                try:
                    page.mouse.wheel(0, 1200)      # 轻滚触发懒加载
                except Exception:
                    pass
            return None
        finally:
            try:
                page.remove_listener("response", _on_resp)
            except Exception:
                pass
            if clicked:
                try:
                    page.go_back(wait_until="domcontentloaded", timeout=15000)
                    time.sleep(1.5)
                except Exception:
                    pass

    def _scrape_one(self, page, store_name, df, dt):
        """单店: 进后台→营业概览→设日期→读页面数字。返回 parse_biz_xlsx 同构 dict 或 None。
        失败原因写入 self._last_err(供卡点面板展示)。"""
        from . import gj_mapping
        self._last_err = ""
        try:
            self._open_biz_overview(page)
            self._set_gj_dates(page, df, dt)
            # 等数字渲染出来(等条件, 不再固定等 3s)
            self._wait_until(lambda: ("营业收入" in self._text_of(page)
                                      or "营业额" in self._text_of(page)),
                             timeout_s=12, what="营业概览读数")
            txt = self._text_of(page)
            parsed, meta = gj_mapping.scrape_overview_text(txt)
            if not parsed:
                if meta.get("zero_data"):
                    # 2026-10-01 用户口径: 平台渲染出了标签、数值全 0 = **真实数据**(当天确实无营业),
                    # 照写 0, 不再整店跳过(跳过会让模板缺这家店, 用户还得猜是没数据还是没抓到)。
                    # gj_mapping 已按 GJ_MAPPING 把各映射类别填 0, 所以正常情况根本不会走到这里。
                    self.sink.log(f"ℹ️ {store_name}: 平台渲染出标签但数值全 0(当天确实无营业数据"
                                  + ("，页面显示「暂无数据」" if meta.get("no_data_hint") else "")
                                  + ") → 照写 0(真实值)")
                else:
                    self._last_err = "营业概览页上没有找到「营业收入/营业额/订单量」区块，页面结构可能已更新"
                    self.sink.log(f"⚠️ {store_name}: {self._last_err}", level="warn")
                    return None
            if meta.get("unknown_channels"):
                # 渠道名是平台随时会加的(2026-09-30 新增「自营外卖」): 已计入校验但没模板列,
                # 必须让用户看见"有哪些值没写进去", 而不是静默丢掉整店。
                self.sink.log(f"ℹ️ {store_name}: 页面出现未映射渠道 "
                              + "、".join(f"{k}({v})" for k, v in meta["unknown_channels"].items())
                              + " —— 已计入交叉校验, 但模板无对应列(未写入)", level="warn")
            if meta.get("crosscheck_failed"):
                self._last_err = (f"渠道合计与页面总量对不上({_crosscheck_detail(meta)})，"
                                  f"可能是页面尚未刷新完，该店数据已弃用以免写错")
                self.sink.log(f"⚠️ {store_name} 交叉校验未过: {meta['crosscheck_failed']}", level="warn")
                return None
            self.sink.log(f"✅ {store_name}: " + " | ".join(
                f"{c} 收入{d.get('营业收入')}/单{d.get('订单量')}"
                for c, d in parsed.items() if c != "meta" and not str(c).startswith("_")))
            # 敏感操作(IA/IB): 进详情页拦 XHR。抓不到 → 留空(宁可不写), 不影响本店其它列。
            # 2026-10-10 用户拍板: 全零日店(平台渲染出标签但数值全 0 = 当天确实无营业)**直接跳过**敏感 ——
            # 证据: 09-26 全零日 12/12 店敏感为空(平台侧本无数据, 非丢失); 空店即使静默收工也要 ~23s → 再省。
            if meta.get("zero_data"):
                self.sink.log(f"⏭ {store_name}: 当日全零(无营业数据) → 敏感操作跳过(IA/IB 留空, 平台侧无数据)")
                return parsed
            try:
                _sens = self._capture_sensitive(page)
            except Exception as e:
                _sens = None
                self.sink.log(f"⚠️ {store_name}: 敏感操作抓取异常: {str(e)[:100]}(留空)", level="warn")
            if _sens:
                parsed["_sensitive"] = _sens
                self.sink.log(f"✍️ {store_name}: 敏感操作 {_sens['times']:g} 次 / {_sens['amt']:g} 元(写 IA/IB)")
            else:
                self.sink.log(f"ℹ️ {store_name}: 敏感操作没抓到数据(IA/IB 留空不写)", level="warn")
            return parsed
        except Exception as e:
            self._last_err = f"页面操作异常: {str(e)[:120]}"
            self.sink.log(f"⚠️ {store_name} 抓取失败: {self._last_err}", level="warn")
            return None

    def capture_stats(self, date_from=None, date_to=None) -> dict:
        """DOM 直读结果已在 download_reports 采集(self.scraped); 组装给编排层。"""
        out = {"platform": "meituan_gj", "stores": getattr(self, "scraped", {}) or {},
               "failed": getattr(self, "failed", []) or [],
               "skipped": getattr(self, "skipped_by_sheet", []) or [],
               "store_ids": getattr(self, "store_ids", {}) or {}}
        self.sink.log(f"🔎 美团管家: {len(out['stores'])} 店成功"
                      + (f", {len(out['failed'])} 店失败" if out["failed"] else "")
                      + "(DOM直读)")
        return out
