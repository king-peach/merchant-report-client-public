# -*- coding: utf-8 -*-
"""京东到家商家后台适配器 (https://store.jddj.com/, appCode=lsp-store)。
登录: 账号密码表单(请输入用户名/请输入密码/登录)。
报表: 脑图记录 经营罗盘→报表下载(门店范围全部门店/选定日期/自定义指标全选)。
状态: 登录已实现并实测入口; 报表/抓取待真实账号实测页面结构后完善(M5)。"""
import os
import re
import time

from ..events import EventSink, HumanBridge

JD_LOGIN = "https://store.jddj.com/base/login?appCode=lsp-store&backUrl=https%3A%2F%2Fstore.jddj.com%2F"
JD_HOME = "https://store.jddj.com/"
# 报表下载列表页(用户 2026-10-08 给出); 菜单点不出来时直接导航用。frame 号随账号可能不同,
# 可用环境变量 JD_DOWNLOAD_URL 覆盖。
JD_DOWNLOAD_URL = "https://store.jddj.com/frame/4734/5087"


class JDDateNotAvailable(RuntimeError):
    """目标日期超出平台可选范围(格子被禁用) —— 不可用键盘兜底, 直接报出。"""


class JDAdapter:
    platform = "jd"

    def __init__(self, ctx, sink: EventSink, human_bridge, store_id="", keyword=""):
        self.ctx = ctx
        self.sink = sink
        self.human = human_bridge
        self.page = None
        self.store_id = store_id or ""
        self.keyword = keyword or ""
        self.nav_override = None
        self.wait_override_ms = None

    def _is_login_page(self, page) -> bool:
        try:
            t = page.evaluate("() => document.body.innerText.slice(0, 400)") or ""
            return "账号登录" in t or "请输入用户名" in t
        except Exception:
            return False

    def _find_logged_in(self):
        for p in self.ctx.pages:
            try:
                if "store.jddj.com" in p.url and not self._is_login_page(p):
                    return p
            except Exception:
                pass
        return None

    def ensure_login(self, username="", password="", timeout_s=300):
        # 先试首页(会话有效时直接进后台, 不碰登录页——登录页会重置视图)
        page = self.ctx.new_page()
        page.goto(JD_HOME, wait_until="commit")
        for _ in range(16):                        # 轮询就绪(原固定 8s): 登录页或内容出现即走
            time.sleep(0.5)
            try:
                if self._is_login_page(page):
                    break
                if len(page.evaluate("() => document.body ? document.body.innerText : ''") or "") > 200:
                    break
            except Exception:
                pass
        if not self._is_login_page(page):
            self.page = page
            self.sink.log("✅ 复用京东登录会话")
            return True
        # 真没登录: 回登录页
        page.goto(JD_LOGIN, wait_until="commit")
        time.sleep(6)

        def logged():
            return not self._is_login_page(page)

        if logged():
            return True
        if username and password:
            self.sink.log("📝 自动填充京东账号密码…")
            try:
                try:
                    page.evaluate("() => document.body ? 1 : 1")
                except Exception:
                    page = self.page = self.ctx.new_page()
                    page.goto(JD_LOGIN, wait_until="commit")
                    time.sleep(5)
                page.evaluate("""(args) => {
                  const [u, p] = args;
                  const set = (el, v) => {
                    const s = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
                    s.call(el, v); el.dispatchEvent(new Event('input', {bubbles: true}));
                  };
                  const acc = [...document.querySelectorAll('input')].find(i => (i.placeholder||'').includes('用户名'));
                  const pw = [...document.querySelectorAll('input')].find(i => i.type === 'password');
                  if (acc) set(acc, u);
                  if (pw) set(pw, p);
                }""", [username, password])
                time.sleep(1)
                for el in page.locator("button:has-text('登录'):visible").all():
                    try:
                        el.click()
                        break
                    except Exception:
                        pass
                time.sleep(5)
            except Exception as e:
                self.sink.log(f"⚠️ 自动填充失败: {str(e)[:60]}")
        else:
            self.sink.need_human("login", "未配置京东账号密码, 请在浏览器手动登录", platform="jd")
        end = time.time() + timeout_s
        while time.time() < end:
            time.sleep(4)
            if logged():
                self.sink.log("✅ 京东登录成功")
                return True
        self.sink.step_fail("login", "登录", "京东登录超时", "E_LOGIN_TIMEOUT")
        return False

    # ---------- 报表下载(经营罗盘→报表下载; 2026-09-20 实测链路) ----------
    # 页面要点(实测):
    # ① 主页点「经营罗盘」展开子菜单 → 点「报表下载」→ 主页路由 /frame/<id>/<id>(内容在子 frame)
    # ② 客服面板会挡点击, 先关(jd-im-icon-close)
    # ③ 表单: 门店范围(默认全部门店) + 数据日期(默认近7天) + 指标(默认24项含收入/营业额/有效订单/曝光/入店)
    # ④ 「下载数据」提交 → 「下载列表」tab 出文件 门店_<起>_<止>_<账号>_<时间>.xlsx → 行内「下载」链接触发浏览器下载
    # ⑤ 文件 sheet「数据」, 表头 29 列(门店名称/门店id/日期/城市/收入/营业额/…), 每店每天一行
    def _jd_click(self, page, text, maxw=220):
        return page.evaluate("""(a) => {
          const [t, maxw] = a;
          const out = [];
          for (const el of document.querySelectorAll('span.menu-item,li,a,div')) {
            const tt = (el.textContent || '').trim();
            if (tt !== t) continue;
            if (el.children.length > 2) continue;
            const r = el.getBoundingClientRect();
            if (r.width <= 0 || r.height <= 0 || r.width > maxw) continue;
            out.push({x: r.x + r.width / 2, y: r.y + r.height / 2, w: r.width});
          }
          out.sort((p, q) => p.w - q.w);
          return out[0] || null;
        }""", [text, maxw])

    def _close_im_panel(self, page):
        try:
            page.evaluate("""() => {
              for (const el of document.querySelectorAll('[class*=jd-im-icon-close],[class*=close]')) {
                const r = el.getBoundingClientRect();
                if (r.x > 900 && r.y > 300) { el.click(); return; }
              }
            }""")
        except Exception:
            pass

    def _open_report_page(self, page, rounds=4):
        """打开报表下载页: 先走菜单(点击时灵时不灵) → 失败则**直接导航到下载列表 URL**。

        2026-10-08 用户给出下载列表地址 https://store.jddj.com/frame/<a>/<b> ——
        菜单导航失败(日志 `[京东] 报表下载未跳转`)时用它兜底, 绕开点击不稳的菜单。
        frame 号可能随账号不同 → 允许用环境变量 JD_DOWNLOAD_URL 覆盖。
        """
        self._close_im_panel(page)
        time.sleep(1.5)
        for attempt in range(rounds):
            b = self._jd_click(page, "经营罗盘")
            if not b:
                time.sleep(2)
                continue
            page.mouse.click(b["x"], b["y"])
            time.sleep(1.8)
            sb = self._jd_click(page, "报表下载")
            if not sb:
                continue
            page.mouse.click(sb["x"], sb["y"])
            for _ in range(6):
                time.sleep(2)
                if "/frame/" in page.url:
                    break
            if "/frame/" in page.url:
                break
            self.sink.log(f"[京东] 报表下载未跳转, 重试{attempt + 1}/{rounds}")
        if "/frame/" not in page.url:
            # 菜单没点出来 → 直接导航(用户给的下载列表地址)
            _url = os.environ.get("JD_DOWNLOAD_URL", JD_DOWNLOAD_URL)
            self.sink.log(f"🧭 菜单没点出来 → 直接导航到下载列表: {_url}")
            try:
                page.goto(_url, wait_until="domcontentloaded", timeout=90000)
                for _ in range(20):                # 内容出现即走(原固定 10s 白等)
                    time.sleep(0.5)
                    try:
                        if any("下载数据" in (f.evaluate("() => document.body ? document.body.innerText : ''") or "")
                               for f in page.frames):
                            break
                    except Exception:
                        pass
                self.sink.log(f"   落地: {page.url[:100]}")
            except Exception as e:
                self.sink.log(f"   直接导航也失败: {str(e)[:70]}", level="warn")
        if "/frame/" not in page.url:
            raise RuntimeError("京东报表下载页未打开(菜单导航失败, 直接导航也未成功)")
        # 找内容 frame —— **主 frame 也算**(2026-10-08 真机: 直接导航到 /frame/<a>/<b> 时,
        # 报表下载内容就在主 frame 里(2886 字含「下载数据/门店范围/数据日期」), 老代码写死
        # `if fr == page.main_frame: continue` 把它跳过了 → 40 秒都找不到 → 误报"内容 frame 未就绪"。
        # 现在: 全 frame 搜, 且要求**确实有报表控件**(下载数据/数据日期)才算数。
        # 找内容 frame —— 主 frame 也算(直接导航时内容在主 frame; 菜单导航时在 kunce-store 子 frame)。
        # 2026-10-09 增强: 菜单"时灵时不灵"——可能点进**别的** /frame/xxx(不是下载页), 于是内容
        # 永远搜不到; 现在搜一轮不中 → 用直接 URL 重定向一次 → 再搜一轮, 仍不中才报错。
        def _search_frames(rounds=12):
            for _ in range(rounds):
                time.sleep(2)
                for fr in page.frames:
                    try:
                        _ok = fr.evaluate("""() => {
                            const t = document.body ? document.body.innerText : '';
                            if (t.length < 100) return false;
                            const hasBtn = [...document.querySelectorAll('button,a,span')]
                              .some(e => (e.textContent || '').trim() === '下载数据');
                            const hasDate = !!document.querySelector('.ant-picker-range, input[placeholder*="日期"]');
                            return hasBtn || hasDate || (t.includes('下载数据') && t.includes('数据日期'));
                        }""")
                    except Exception:
                        _ok = False
                    if _ok:
                        return fr
            return None

        tgt = _search_frames()
        if tgt is None:
            self.sink.log("🧭 菜单落点未就绪 → 直接用下载页 URL 再来一轮")
            try:
                page.goto(os.environ.get("JD_DOWNLOAD_URL", JD_DOWNLOAD_URL),
                          wait_until="domcontentloaded", timeout=90000)
                for _ in range(20):                # 内容出现即走(原固定 10s 白等)
                    time.sleep(0.5)
                    try:
                        if any("下载数据" in (f.evaluate("() => document.body ? document.body.innerText : ''") or "")
                               for f in page.frames):
                            break
                    except Exception:
                        pass
                self.sink.log(f"   落地: {page.url[:100]}")
            except Exception as e:
                self.sink.log(f"   直接导航失败: {str(e)[:70]}", level="warn")
            tgt = _search_frames()
        if tgt is None:
            self.sink.log("🧭 直连后内容帧仍空白 → 点壳内「报表下载」导航触发子应用挂载(handshake)")
            try:
                box = page.evaluate("""() => {
                  for (const el of document.querySelectorAll('a,span,div,li')) {
                    if ((el.textContent || '').trim() === '报表下载') {
                      const r = el.getBoundingClientRect();
                      if (r.width > 0 && r.width < 300 && r.height > 0 && r.height < 80)
                        return {x: r.x + r.width / 2, y: r.y + r.height / 2};
                    }
                  }
                  return null;
                }""")
                if box:
                    page.mouse.click(box["x"], box["y"])
                    time.sleep(3)
            except Exception:
                pass
            tgt = _search_frames()
        if tgt is None:
            raise RuntimeError("京东报表下载内容 frame 未就绪(菜单/直连/壳内导航 各搜一轮后)")
        time.sleep(2.5)   # 子应用挂载后仍在重渲染, 稍歇再让下游操作(2026-10-09)
        return tgt

    def _tab_click(self, tgt, label):
        tgt.evaluate("""(label) => {
          for (const el of document.querySelectorAll('.ant-tabs-tab')) {
            const t = (el.textContent || '');
            if (label === '数据下载' ? (t.includes('数据下载') && !t.includes('列表')) : t.includes(label)) {
              el.click(); return;
            }
          }
        }""", label)
        time.sleep(2.5)

    def download_reports(self, ddir, timeout_s=300, date_from=None, date_to=None):
        """提交「门店」报表下载(全部门店, 默认指标已含 收入/营业额/有效订单/曝光/入店)
        → 下载列表等文件 → 行内「下载」触发浏览器下载 → 落盘 ddir/京东_门店_<起>_<止>.xlsx"""
        if not (date_from and date_to):
            raise ValueError("京东报表下载需要 date_from/date_to")
        import os as _os
        _os.makedirs(ddir, exist_ok=True)
        self.download_dir = str(ddir)
        page = self.page if (self.page and not self.page.is_closed()) else self.ctx.new_page()
        self.page = page
        if "store.jddj.com" not in page.url:
            page.goto(JD_HOME, wait_until="commit")
            for _ in range(20):                    # 轮询就绪(原固定 10s): 登录页或内容出现即走
                time.sleep(0.5)
                try:
                    if self._is_login_page(page):
                        break
                    if len(page.evaluate("() => document.body ? document.body.innerText : ''") or "") > 200:
                        break
                except Exception:
                    pass
        tgt = self._open_report_page(page)
        self.sink.log("✅ 京东报表下载页就绪")

        # 设日期区间: antd RangePicker readonly, 直接日历点选(实测 value setter 会回弹)
        self._set_dates_by_calendar(tgt, page, date_from, date_to)
        got = tgt.evaluate("""() => {
          const out = [];
          for (const el of document.querySelectorAll('input')) {
            const ph = el.placeholder || '';
            if (ph.includes('日期')) out.push(el.value);
          }
          return out;
        }""")
        if got[:2] != [date_from, date_to]:
            raise RuntimeError(f"京东日期区间设置未生效: {got[:2]} (期望 {date_from}~{date_to}, 已中止)")
        self.sink.log(f"📅 日期区间生效: {got[0]} ~ {got[1]}")

        # 提交
        self._tab_click(tgt, "数据下载")
        dl = tgt.evaluate("""() => {
          for (const el of document.querySelectorAll('button')) {
            if ((el.textContent || '').trim() === '下载数据') {
              const r = el.getBoundingClientRect();
              if (r.width > 0) { el.click(); return true; }
            }
          }
          return false;
        }""")
        if not dl:
            raise RuntimeError("未找到「下载数据」按钮")
        self.sink.log("⬇️ 已提交下载任务, 等待生成…")
        time.sleep(6)

        # 下载列表等文件
        fname_expect = f"门店_{date_from.replace('-', '')}_{date_to.replace('-', '')}_"
        path = None
        for i in range(24):   # 最多 2 分钟
            time.sleep(5)
            try:
                self._tab_click(tgt, "下载列表")
                time.sleep(2)
                hit = tgt.evaluate("""(prefix) => {
                  if (!document.body.innerText.includes(prefix)) return null;
                  const rows = [...document.querySelectorAll('tr,li')];
                  for (const el of rows) {
                    const t = el.textContent || '';
                    if (t.includes(prefix) && t.includes('下载')) return true;
                  }
                  return true;
                }""", fname_expect)
                if not hit:
                    continue
                # 触发行内「下载」(浏览器下载事件)
                with page.expect_download(timeout=60000) as dl_info:
                    tgt.evaluate("""(prefix) => {
                      const rows = [...document.querySelectorAll('tr,li,div')];
                      for (const el of rows) {
                        const t = el.textContent || '';
                        if (t.includes(prefix) && t.includes('下载')) {
                          for (const a of el.querySelectorAll('a,span,button')) {
                            if ((a.textContent || '').trim() === '下载' && a.getBoundingClientRect().width > 0) {
                              a.click(); return;
                            }
                          }
                        }
                      }
                    }""", fname_expect)
                d = dl_info.value
                dest = os.path.join(str(ddir), f"京东_门店_{date_from.replace('-', '')}_{date_to.replace('-', '')}.xlsx")
                d.save_as(dest)
                path = dest
                break
            except Exception as e:
                if i >= 20:
                    self.sink.log(f"⚠️ 下载等待异常: {str(e)[:80]}", level="warn")
                continue
        if not path:
            raise RuntimeError("京东报表未生成/未取回(下载列表无目标文件)")
        self.sink.log(f"💾 已保存 {os.path.basename(path)} ({os.path.getsize(path) / 1024:.0f}KB)")
        self.sink.step_ok("download", "下载报表", f"京东门店报表 {date_from}~{date_to}")
        return True

    def _set_dates_by_calendar(self, tgt, page, df, dt):
        """antd RangePicker 日历点选(实测唯一有效方式):
        input readonly, 面板挂在 kunce-store 子 frame 的 .ant-picker-dropdown;
        坐标 mouse.click 跨 frame 会错位 → 必须 locator('td[title=日期]') 点击(自动换算)。

        2026-10-08 真机失败 `Locator.click: Timeout 30000ms exceeded` → 改成**轮询等待控件**,
        找不到时打印现场取证(有哪些控件)而不是闷等 30 秒; 若落在「下载列表」tab 先切回「数据下载」。
        """
        def _find_visible_pick():
            """逐个找**可见的**日期控件: 页面可能同时存在多份 .ant-picker-range(隐藏 tab 里
            留着一份隐藏孪生), 老写法取 .first —— 若恰好选中隐藏的那份, is_visible() 永远
            False 闷等到超时(2026-10-08 真机 `element is not visible` 同类坑) → 改为遍历
            所有候选, 返回真正可见的那一个。"""
            try:
                cands = tgt.locator(".ant-picker-range")
                n = cands.count()
            except Exception:
                return None
            for _i in range(n):
                try:
                    c = cands.nth(_i)
                    if c.is_visible():
                        return c
                except Exception:
                    continue
            return None

        pick = None
        for _try in range(2):
            for _ in range(8):
                pick = _find_visible_pick()
                if pick is not None:
                    break
                time.sleep(2)
            if pick is not None:
                break
            # 可能是停在「下载列表」tab → 切回「数据下载」再找一次
            if _try == 0:
                try:
                    self._tab_click(tgt, "数据下载")
                except Exception:
                    pass
                time.sleep(2)
        if pick is None:
            try:
                diag = tgt.evaluate("""() => ({
                    ranges: document.querySelectorAll('.ant-picker-range').length,
                    visibleRanges: [...document.querySelectorAll('.ant-picker-range')]
                        .filter(e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; }).length,
                    pickers: document.querySelectorAll('.ant-picker').length,
                    inputs: [...document.querySelectorAll('input')].slice(0, 8).map(e => e.placeholder || '')})""")
            except Exception as e:
                diag = f"(读取失败 {str(e)[:40]})"
            raise RuntimeError(f"京东日期控件(.ant-picker-range)没有可见的 → 现场: {diag}")
        # 子应用(kunce)刚挂载会持续重渲染 → 元素不稳定, 单次点击可能 8s 超时(2026-10-09 实测);
        # 改 3 次重试: 每次重新找「可见的那份」→ 短超时点击, 都失败才报错。
        clicked, last_err = False, ""
        for _a in range(3):
            if _a:
                time.sleep(3)
                pick = _find_visible_pick() or pick
            try:
                pick.scroll_into_view_if_needed(timeout=3000)
            except Exception:
                pass
            try:
                pick.click(timeout=10000)
                clicked = True
                break
            except Exception as e:
                last_err = str(e)[:70]
        if not clicked:
            raise RuntimeError(f"京东日期控件点不动(重试3次后): {last_err}")
        time.sleep(2)
        panel_fr = None
        for fr in page.frames:
            try:
                n = fr.evaluate(
                    "() => { const dd = document.querySelector('.ant-picker-dropdown:not(.ant-picker-dropdown-hidden)');"
                    " return dd ? dd.querySelectorAll('td[title]').length : 0; }")
                if n > 20:
                    panel_fr = fr
                    break
            except Exception:
                pass
        if not panel_fr:
            raise RuntimeError("京东日期面板未弹出")
        # 2026-10-09 修复(跨月/跨年方向): 旧版只会点「下一页」→ 目标在过去时越翻越远
        # (真机翻到 2027-04 后 30s 超时, 用户报"卡在选日期")。新逻辑: 读面板当月 →
        # 按方向翻月(1 次=1 月) → 目标格**在可视月份(in-view)且可见**才点;
        # 上限 36 次(≈3 年)兜底, 翻不到给现场取证。禁用格(超平台范围)直接报错。
        def _pick_one(target):
            ty, tm = (int(x) for x in target.split("-")[:2])
            fail_nav = 0
            for _n in range(36):
                try:
                    cell = panel_fr.locator(f"td[title='{target}']").first
                    if cell.count() > 0 and cell.is_visible():
                        cls = cell.get_attribute("class") or ""
                        if "ant-picker-cell-disabled" in cls:
                            raise JDDateNotAvailable(f"京东日历: 目标日 {target} 被平台禁用(可能超出可选范围)")
                        if "ant-picker-cell-in-view" in cls:
                            cell.click(timeout=6000)
                            return True
                except RuntimeError:
                    raise
                except Exception:
                    pass
                cur = self._read_panel_ym(panel_fr)
                if cur is None:
                    time.sleep(0.8)
                    continue
                if (ty, tm) == cur:
                    time.sleep(0.7)   # 月份已对但格子还没渲染/在动画 → 稍等重试
                    continue
                if (ty, tm) < cur:
                    btn = panel_fr.locator(".ant-picker-header-prev-btn").first
                else:
                    btn = panel_fr.locator(".ant-picker-header-next-btn").last
                try:
                    btn.click(timeout=4000)
                except Exception:
                    fail_nav += 1
                    if fail_nav >= 3:
                        break
                time.sleep(0.7)
            head = ""
            try:
                head = panel_fr.evaluate("""() => {
                  const dd = document.querySelector('.ant-picker-dropdown:not(.ant-picker-dropdown-hidden)');
                  return dd ? dd.innerText.slice(0, 130) : '(无面板)';
                }""")
            except Exception:
                pass
            raise RuntimeError(f"京东日历翻不到 {target}(±3年内), 现场: {head}")

        try:
            _pick_one(df)
            time.sleep(1.4)
            _pick_one(dt)
            time.sleep(2.0)
        except JDDateNotAvailable:
            raise
        except RuntimeError as e:
            # 兜底(2026-10-09): antd 日期输入框支持键盘直填(真实按键事件, 与 value setter 不同不会回弹);
            # 成败交给调用方既有读回校验裁判 —— 失败会报「日期区间设置未生效」, 绝不带错区间下载。
            self.sink.log(f"（京东日历点选未过, 兜底尝试键盘直填: {str(e)[:60]}）")
            for i, d in enumerate((df, dt)):
                try:
                    inp = tgt.locator("input[placeholder*='日期']").nth(i)
                    inp.click(timeout=4000)
                    page.keyboard.press("Control+A")
                    page.keyboard.type(d, delay=50)
                    page.keyboard.press("Enter")
                    time.sleep(0.8)
                except Exception as ex:
                    self.sink.log(f"⚠️ 京东键盘直填失败({d}): {str(ex)[:60]}", level="warn")
            time.sleep(1.2)

    @staticmethod
    def _parse_ym(s):
        """'2026-10-01' / '2026年10月' / '2026年 10月' → (2026, 10); 解析不出返回 None。"""
        if not s:
            return None
        t = str(s).replace("\n", "").replace(" ", "").replace("\t", "")
        m = re.search(r"(20[0-9]{2})[-年/]?(1[0-2]|0?[1-9])(?![0-9])", t)
        if not m:
            return None
        return (int(m.group(1)), int(m.group(2)))

    def _read_panel_ym(self, panel_fr):
        """面板当前第一个可见月的 (年,月): 优先首个 in-view 格子的 title, 退回表头文本。"""
        try:
            t = panel_fr.evaluate("""() => {
              const dd = document.querySelector('.ant-picker-dropdown:not(.ant-picker-dropdown-hidden)');
              if (!dd) return null;
              const c = dd.querySelector('.ant-picker-cell-in-view[title]');
              if (c) return c.getAttribute('title');
              const h = dd.querySelector('.ant-picker-header-view');
              return h ? (h.innerText || '') : null;
            }""")
        except Exception:
            return None
        return self._parse_ym(t)

    def capture_stats(self, date_from=None, date_to=None) -> dict:
        """解析已下载的京东门店报表 → {日期: 聚合行}"""
        from . import jd_mapping
        ddir = getattr(self, "download_dir", None)
        if not ddir:
            self.sink.log("⚠️ 京东抓取: 未指定报表目录", level="warn")
            return {}
        f = self._find_jd_file(ddir, date_from, date_to)
        if not f:
            self.sink.log(f"⚠️ 京东抓取: 目录无 {date_from}~{date_to} 报表({ddir})", level="warn")
            return {}
        rows = self._parse_jd_file(f)
        self.sink.log(f"🔎 京东报表解析: {len(rows)}天 ({f.split('/')[-1][:40]})")
        return {"platform": "jd", "rows": rows, "file": f}

    @staticmethod
    def _find_jd_file(ddir, date_from, date_to):
        import glob
        import os
        k = f"京东_门店_{(date_from or '').replace('-', '')}_{(date_to or date_from or '').replace('-', '')}"
        hits = glob.glob(os.path.join(str(ddir), f"{k}*.xlsx"))
        return hits[0] if hits else None

    @staticmethod
    def _read_jd_rows(path):
        """读京东门店报表 → (表头, [行dict...]) 逐行返回(不聚合)。"""
        import openpyxl
        wb = openpyxl.load_workbook(path, data_only=True)
        ws = wb["数据"] if "数据" in wb.sheetnames else wb.active
        rows_iter = ws.iter_rows(values_only=True)
        headers = [str(c) if c is not None else None for c in next(rows_iter)]
        out = []
        for r in rows_iter:
            if not r or r[0] in (None, ""):
                continue
            out.append({h: v for h, v in zip(headers, r) if h})
        return headers, out

    @staticmethod
    def _parse_jd_rows(path):
        """门店报表 → {日期: [每店一行的 dict, ...]}, **保留门店名称**。

        京东报表本来就是"每店每天一行"(22 行 = 多店 × 多天), 聚合版(_parse_jd_file)会把
        它们加成一个数; 这里保留逐店行, 供"一店一 Sheet"写入各自 Sheet。
        """
        _headers, rows = JDAdapter._read_jd_rows(path)
        out = {}
        for row in rows:
            raw = row.get("日期")
            if raw in (None, ""):
                continue
            out.setdefault(str(raw)[:10], []).append(row)
        return out

    @staticmethod
    def _parse_jd_file(path):
        """门店报表(每店每天一行) → {日期: 数值列求和聚合}"""
        _headers, rows = JDAdapter._read_jd_rows(path)

        def _num(v):
            if v in (None, ""):
                return None
            try:
                s = str(v).replace(",", "")
                if s.endswith("%"):
                    return None
                return float(s)
            except (TypeError, ValueError):
                return None

        out = {}
        for row in rows:
            raw = row.get("日期")
            if raw in (None, ""):
                continue
            d = str(raw)[:10]
            cur = out.get(d)
            if cur is None:
                out[d] = dict(row)
                continue
            for h, v in row.items():
                if not h:
                    continue
                nv, ov = _num(v), _num(cur.get(h))
                if nv is not None and ov is not None:
                    cur[h] = ov + nv
                elif cur.get(h) in (None, "") and v not in (None, ""):
                    cur[h] = v
        return out
