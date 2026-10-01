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
        for _ in range(14):
            time.sleep(0.5)
            try:
                if page.evaluate("() => !!document.querySelector('.org-switch-modal')"):
                    return True
            except Exception:
                # 点击可能触发 SPA 导航 → evaluate 报 "Execution context was destroyed"
                continue
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
        time.sleep(2.0)
        from .. import sheet_match as _sm          # 注意: platforms 子包内是 `..`(曾误写 `.` → ImportError)
        cands = [name]
        _short = _sm.extract_place(name)
        if _short and _short != name:
            cands.append(_short)
        hit = None
        for i, kw in enumerate(cands):
            if i:      # 第二次用关键字: 先清空搜索框重输, 否则过滤结果为空
                try:
                    inp = page.locator("input[placeholder*='机构名称']").first
                    inp.fill("")
                    inp.type(kw, delay=25)
                    time.sleep(2.0)
                except Exception:
                    pass
            hit = page.evaluate("""(kw) => {
              const rows = [...document.querySelectorAll('.org-switch-modal tr')];
              for (const tr of rows) {
                const t = (tr.innerText || '').replace(/\\s+/g, ' ');
                if (!t.includes(kw) || t.includes('当前')) continue;
                const cell = tr.querySelector('td') || tr;
                const r = cell.getBoundingClientRect();
                if (r.width <= 0) continue;
                return {x: r.x + Math.min(60, r.width / 2), y: r.y + r.height / 2, txt: t.slice(0, 60)};
              }
              return null;
            }""", kw)
            if hit:
                break
        if not hit:
            self.sink.log(f"⚠️ 切换弹窗里没找到「{name}」的行(搜索词 {cands} 都试过)", level="warn")
            return False
        page.mouse.move(hit["x"], hit["y"])
        time.sleep(0.3)
        page.mouse.click(hit["x"], hit["y"])
        waited = 0.0
        for _ in range(int(timeout_s * 2)):
            time.sleep(0.5)
            waited += 0.5
            try:
                now = self._header_store(page)
            except Exception:
                now = ""     # 切店=SPA 导航, 中途 evaluate 会报 "Execution context was destroyed"
            if now and name[:6] in now:
                time.sleep(2)
                self.sink.log(f"🔀 右上角切换门店 → {now}（约 {waited:.1f}s，未退回选择页）")
                return True
        self.sink.log(f"⚠️ 点了「{hit['txt'][:30]}」但表头没变成目标店(当前: {self._header_store(page)})",
                      level="warn")
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
        管家页面会把「品牌商-张泽来 01459」刷几百遍, 直接截前 200 字 → 诊断包里全是噪音,
        等于没信息(2026-09-28 用户诊断包实测)。"""
        try:
            t = re.sub(r"\s+", " ", self._text_of(page) or "").strip()
        except Exception:
            return "(页面读取失败)"
        parts = t.split(" ")
        out, i = [], 0
        while i < len(parts) and len(" ".join(out)) < n:
            # 找最小重复单元(1~3 词): 管家页面是「品牌商-张泽来 01459」这种**两词交替**刷屏,
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
        rms-report/home(文本被「品牌商-张泽来 01459」刷屏), 靠重试点击救不回来。
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
        """营业概览日期: antd v3 Calendar(.ant-calendar-*), 面板挂 body 下;
        快捷按钮有 今日/昨日/本周/本月/上月。单日区间=点两次同日格(开始/结束)。"""
        df, dt = df.replace("/", "-"), dt.replace("/", "-")   # 统一格式(上游可能传 YYYY/MM/DD)
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

        if df == dt:
            # 单日: 点昨日/今日快捷(若匹配) 否则开面板点格
            shortcut = "昨日" if df < (time.strftime("%Y-%m-%d")) else "今日"
            if click(shortcut):
                time.sleep(4)
                return
        # 通用: 开面板 → 点开始格 → 点结束格(antd v3 range 点两次)
        page.locator("input[placeholder*='请选择日期']").first.click()
        time.sleep(2.5)

        def get_cells():
            return page.evaluate("""() => {
              const dd = document.querySelector('.ant-calendar-picker-container:not([style*="display: none"])');
              if (!dd) return null;
              const out = [];
              for (const a of dd.querySelectorAll('.ant-calendar-date')) {
                const r = a.getBoundingClientRect();
                if (r.width <= 0) continue;
                const title = a.getAttribute('aria-selected') !== null ? '' : '';
                // antd v3: 日期在 .ant-calendar-date 的文本; 月由面板头决定, 但每格有 data-month? 用 title 属性兜底
                out.push({t: (a.getAttribute('title') || a.textContent || '').trim(),
                          x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2)});
              }
              return out.length ? out : null;
            }""")

        cells = get_cells()
        if not cells:
            raise RuntimeError("美团管家: 日期面板未弹出")
        # antd v3 格子文本=日号(无月份), 需结合面板头月份; df/dt 格式 YYYY-MM-DD → 取 日号
        d1n, d2n = df.split("-")[2].lstrip("0"), dt.split("-")[2].lstrip("0")
        c1 = next((c for c in cells if c["t"] == d1n), None)
        c2 = next((c for c in cells if c["t"] == d2n), None)
        if not (c1 and c2):
            raise RuntimeError(f"美团管家: 日历中无 {df}~{dt}(格子数={len(cells)})")
        page.mouse.click(c1["x"], c1["y"])
        time.sleep(1.5)
        # antd v3 单面板: 点完开始再点结束(同一面板)
        cells = get_cells() or cells
        c2 = next((c for c in cells if c["t"] == d2n), None)
        if c2:
            page.mouse.click(c2["x"], c2["y"])
        time.sleep(2)
        if click("查询"):
            time.sleep(5)

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

        txt = self._text_of(page)
        ran = 0
        if self._SELECT_PAGE_MARK in txt:
            stores = page.evaluate("""() => {
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
                  if (m) {
                    if (!seen.has(m[2])) {
                      seen.add(m[2]);
                      out.push({name: m[1].replace(/^门店/, ''), mid: m[2]});
                    }
                    break;
                  }
                  card = card.parentElement;
                }
              }
              return out;
            }""")
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
                f"{c} 收入{d.get('营业收入')}/单{d.get('订单量')}" for c, d in parsed.items() if c != "meta"))
            return parsed
        except Exception as e:
            self._last_err = f"页面操作异常: {str(e)[:120]}"
            self.sink.log(f"⚠️ {store_name} 抓取失败: {self._last_err}", level="warn")
            return None

    def capture_stats(self, date_from=None, date_to=None) -> dict:
        """DOM 直读结果已在 download_reports 采集(self.scraped); 组装给编排层。"""
        out = {"platform": "meituan_gj", "stores": getattr(self, "scraped", {}) or {},
               "failed": getattr(self, "failed", []) or [],
               "skipped": getattr(self, "skipped_by_sheet", []) or []}
        self.sink.log(f"🔎 美团管家: {len(out['stores'])} 店成功"
                      + (f", {len(out['failed'])} 店失败" if out["failed"] else "")
                      + "(DOM直读)")
        return out
