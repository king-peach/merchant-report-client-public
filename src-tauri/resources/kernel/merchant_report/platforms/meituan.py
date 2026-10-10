# -*- coding: utf-8 -*-
"""美团外卖商家后台适配器 (https://e.waimai.meituan.com/)。
登录: e.waimai 账号密码表单(平台: 美团统一登录)。
报表: 脑图记录 营业分析→报表下载(全部业务+拼好饭); 经营数据→流量; 推广→消费记录。
状态: 登录已实现并实测入口; 报表/抓取待用真实账号实测页面结构后完善(M5)。"""
import datetime
import json
import os
import re
import time

from ..events import EventSink, HumanBridge
from .. import packs

MEITUAN_HOME = "https://e.waimai.meituan.com/"
# 报表页可独立打开(无需主站外壳); 生成走 UI(裸 fetch 直调被签名/时效挡: code:500), 取文件走 ctx.request
REPORT_URL = "https://waimaieapp.meituan.com/igate/bizdata/report/download"
# SPA 壳体入口(/igate 是 iframe 端点: 未认证时只回 {'method':'logon'} 脚本, 页面会白屏)
SPA_URL = "https://waimaieapp.meituan.com/bizdata_pc/report/download"
HISTORY_URL = ("https://waimaieapp.meituan.com/gw/bizdata/report/download/history"
               "?pageSize=20&pageNum=1")
BIZ_ALL, BIZ_BAO = "全部", "拼好饭"

# 锚点包(改版对抗 L1): 常量为兜底默认值; adapters/meituan.json 覆盖后以包为准
_PACK = packs.load_pack("meituan")
BIZ_ALL = packs.get(_PACK, "selectors", "business_tabs", "all", default=BIZ_ALL)
BIZ_BAO = packs.get(_PACK, "selectors", "business_tabs", "bao", default=BIZ_BAO)
REPORT_URL = packs.get(_PACK, "selectors", "report_page", "url", default=REPORT_URL)
HISTORY_URL = packs.get(_PACK, "selectors", "report_page", "history_url", default=HISTORY_URL)


class MeituanAdapter:
    platform = "meituan"

    def __init__(self, ctx, sink: EventSink, human_bridge, store_id="", keyword=""):
        self.ctx = ctx
        self.sink = sink
        self.human = human_bridge
        self.page = None
        self.store_id = store_id or ""
        self.keyword = keyword or ""
        self.nav_override = None
        self.wait_override_ms = None

    # ---------- 登录 ----------
    def ensure_login(self, username="", password="", timeout_s=300):
        page = self._find_logged_in()
        if page:
            self.page = page
            self.sink.log("✅ 复用美团登录会话")
            return True
        # 2026-10-09: 先直连报表页验证会话 —— 实测会话有效时 e.waimai 首页也会间歇翻出登录/验证墙
        # (t+15s 变登录态), 而下载真正依赖的报表页(waimaieapp)对有效 cookie 会话稳定可用 →
        # 报表页直连成功即算登录成功, 根本不再走首页账密(从源头消掉"每次都触发登录+滑块")。
        # 直连失败(会话真失效) → 回退下方原首页登录流程。
        self.page = page = self.ctx.new_page()
        try:
            self._report_page(timeout_s=45)
            self.sink.log("✅ 美团会话有效(报表页直连) — 无需登录")
            return True
        except Exception as e:
            self.sink.log(f"（报表页快捷校验未过, 转入常规登录流程: {str(e)[:60]}）")
        page.goto(MEITUAN_HOME, wait_until="commit")

        def _state():
            return self._login_state(page)

        # 2026-10-09 修复"明明已登录却总走登录+滑块": 旧版只在第 6 秒采样一次 body 前 500 字,
        # 页面慢加载/入口闪现会被误判"未登录" → 白白触发一次账密登录(风控视为异常 → 弹滑块)。
        # 现改为轮询判定: 确认已登录 → 立即成功(绝不提交账密); 连续 4 次(≈10s)都是登录页才
        # 算真未登录; 30s 无结论 → 重载复核一次, 仍非登录页则按已登录继续。
        t0 = time.time()
        login_streak = 0
        while time.time() - t0 < 30:
            time.sleep(2.5)
            st = _state()
            if st == "in":
                self.sink.log("✅ 美团登录成功")
                return True
            if st == "login":
                login_streak += 1
                if login_streak >= 4:
                    break
            else:
                login_streak = 0
        if login_streak < 4 and _state() != "login":
            try:
                page.reload(wait_until="commit")
            except Exception:
                pass
            time.sleep(6)
            if _state() != "login":
                self.sink.log("✅ 美团登录成功(重载复核)")
                return True
        if not (username and password):
            self.sink.need_human("login", "未配置美团账号密码, 请在浏览器手动登录", platform="meituan")
        else:
            self.sink.log("📝 自动填充美团账号密码…")
            try:
                # 账号密码 (Playwright fill 走真实输入事件)
                acc = page.locator("input[placeholder*='账号']").first
                acc.click()
                acc.fill(username)
                pw = page.locator("input[type='password']").first
                pw.click()
                pw.fill(password)
                time.sleep(1)
                # ① 勾选《美团外卖商家隐私协议》(checkbox 在文字左侧, 点文字行可切换)
                self._check_agreement(page)
                time.sleep(1)
                # ② 点登录
                btn = page.locator("button:has-text('登录')").first
                btn.click()
                self.sink.log("已点击登录, 检测验证方式…")
                time.sleep(4)
                # ③ 短信验证: 自动点「发送验证码」, 人工输码
                if self._handle_sms(page, timeout_s):
                    pass  # 登录结果由下方轮询判定
            except Exception as e:
                self.sink.log(f"⚠️ 自动登录异常: {str(e)[:80]}, 转人工")

        # 轮询登录结果(滑块/短信由人工处理后, 页面进入工作台即成功)
        end = time.time() + timeout_s
        while time.time() < end:
            time.sleep(4)
            if _state() == "in":
                self.sink.log("✅ 美团登录成功")
                return True
        self.sink.step_fail("login", "登录", "美团登录超时(含人工等待)", "E_LOGIN_TIMEOUT")
        return False

    def _login_state(self, page):
        """页面状态: 'in'=已登录 / 'login'=登录页或滑块弹层 / 'loading'=尚未渲染完(2026-10-09)。

        登录页含「账号登录」; 滑块弹层含「滑块」+「拖动/验证」——两者都算未登录, 避免
        被页面慢加载/内容不足骗成"已登录"(旧版单次 6s 采样就栽在这)。
        """
        try:
            t = page.evaluate("() => document.body ? document.body.innerText : ''") or ""
        except Exception:
            return "loading"
        if len(t) < 30:
            return "loading"
        head = t[:800]
        if "账号登录" in head:
            return "login"
        if "滑块" in head and ("拖动" in head or "验证" in head):
            return "login"
        return "in"

    def _check_agreement(self, page):
        """勾选『我已阅读并同意《美团外卖商家隐私协议》』。
        checkbox 常为自绘方块(无 input), 点击协议文字行切换; 找不到就点文字左侧位置。"""
        try:
            el_box = page.evaluate("""() => {
              const els = [...document.querySelectorAll('*')].filter(el => {
                const own = Array.from(el.childNodes).filter(n=>n.nodeType===3)
                  .map(n=>n.textContent).join('');
                return own.includes('我已阅读');
              });
              if (!els.length) return null;
              const r = els[0].getBoundingClientRect();
              return {x: r.x, y: r.y, w: r.width, h: r.height};
            }""")
            if not el_box:
                self.sink.log("⚠️ 未找到隐私协议勾选行")
                return
            # 优先点 checkbox(文字左侧 12px); 若本来就是 checkbox 区域则点中心
            cx = el_box["x"] - 12 if el_box["x"] > 20 else el_box["x"] + 8
            cy = el_box["y"] + el_box["h"] / 2
            page.mouse.click(cx, cy)
            self.sink.log("☑️ 已勾选《美团外卖商家隐私协议》")
        except Exception as e:
            self.sink.log(f"⚠️ 勾选协议失败: {str(e)[:60]}")

    def _handle_sms(self, page, timeout_s=240) -> bool:
        """检测短信验证弹窗: 自动点『发送验证码』→ need_human 提醒人工输码 → 轮询完成。
        返回是否进入了短信流程。"""
        def sms_present():
            try:
                t = page.evaluate("() => document.body.innerText") or ""
                # 明确的发送按钮文案才算弹窗; 登录页的「验证码登录」tab 不算
                return ("发送验证码" in t or "获取验证码" in t
                        or ("请输入验证码" in t and ("短信" in t or "手机" in t)))
            except Exception:
                return False

        # 等短信弹窗出现(最多 12s; 不出=可能直接登录成功/滑块)
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
                loc = page.locator(sel).first
                if loc.count() > 0:
                    loc.click()
                    break
            self.sink.log("✅ 已点击发送验证码")
        except Exception as e:
            self.sink.log(f"⚠️ 点击发送验证码失败: {str(e)[:60]}")
        self.sink.need_human("sms_code", "请查收手机短信, 在浏览器中输入验证码并确认", platform="meituan")
        # 轮询人工完成(短信流程通常 2 分钟内)
        end = time.time() + timeout_s
        while time.time() < end:
            time.sleep(4)
            try:
                t = page.evaluate("() => document.body.innerText.slice(0, 500)") or ""
                if "账号登录" not in t:
                    return True
            except Exception:
                pass
        return False

    def _find_logged_in(self):
        for p in self.ctx.pages:
            try:
                if "e.waimai.meituan.com" in p.url and \
                   "账号登录" not in (p.evaluate("() => document.body.innerText.slice(0, 400)") or ""):
                    return p
            except Exception:
                pass
        return None

    # ---------- 报表下载: UI 设参 + API 取文件 ----------
    # 生成必须走 UI(裸 fetch 直调被签名/时效挡: code:500 服务器开小差了); 取文件走 ctx.request.get(带会话)
    def download_reports(self, ddir, timeout_s=300, date_from=None, date_to=None):
        """下「全部业务」+「拼好饭」两份 CSV。全部业务必需; 拼好饭取不到只告警(部分门店无该业务)。"""
        if not (date_from and date_to):
            raise ValueError("美团报表下载需要 date_from/date_to")
        from . import meituan_mapping as mt_map
        os.makedirs(ddir, exist_ok=True)
        # 门店上下文必须先复位成「全部门店」(-1), 否则平台只导出**当前单店视角**的 1 家店:
        # 2026-10-01 真机 —— 上一次逐店抓商圈崩在半路(browser closed), cookie 停在某店,
        # 于是报表里只有 1 家门店, 其余门店 Sheet 全空(用户报"美团外卖的数据并没有写入")。
        try:
            self._set_poi_cookie(self._POI_ALL)
            self.sink.log(f"🪪 下载前复位门店上下文: {self._POI_COOKIE}={self._POI_ALL}(全部门店)")
        except Exception as e:
            self.sink.log(f"⚠️ 复位门店上下文失败: {str(e)[:60]}", level="warn")
        self.download_dir = str(ddir)
        f, t = date_from.replace("-", ""), date_to.replace("-", "")
        saved = {}
        cols = {}
        for biz, tpl in ((BIZ_ALL, mt_map.FILE_ALL), (BIZ_BAO, mt_map.FILE_BAO)):
            # 每个业务都重新加载报表页: 实测切 tab 后上一业务的指标勾选仍在,
            # 带着「全部」的选点去下拼好饭, 会拿到与全部业务逐字节相同的文件(2026-09-18 实测)
            page = self._report_page()
            self._set_date_range(page, date_from, date_to)
            self._select_business(page, biz)
            self._select_all_metrics(page)
            before = self._history_names(page)
            self._click_download(page)
            entry = self._wait_new_file(page, before, f, t, timeout_s=min(timeout_s, 240))
            if not entry:
                self.sink.log(f"⚠️ {biz} 报表未生成(下载历史无新文件): 跳过", level="warn")
                continue
            path = os.path.join(str(ddir), tpl.format(f=f, t=t))
            if not self._fetch_file(entry, path):
                continue
            n = self._csv_cols(path)
            if biz == BIZ_BAO:
                # 实测(2026-09-18): 拼好饭报表与「全部业务」**同名**(均不含「拼好饭」), 只能按内容判
                all_p = saved.get(BIZ_ALL)
                if all_p and open(path, "rb").read() == open(all_p, "rb").read():
                    self.sink.log("⚠️ 拼好饭报表与全部业务逐字节相同(业务切换未生效), 弃用以免错填拼好饭列",
                                  level="warn")
                    os.remove(path)
                    continue
                if n <= 0 or n >= 60:
                    self.sink.log(f"⚠️ 拼好饭报表列数异常({n} 列, 实测应约 31 列), 弃用以免错列", level="warn")
                    os.remove(path)
                    continue
            saved[biz] = path
            cols[biz] = n
        if not saved.get(BIZ_ALL):
            self.sink.step_fail("download", "下载报表", "美团「全部业务」报表未取到", "E_NO_REPORT")
            return False
        bao_txt = f"✓({cols.get(BIZ_BAO)}列)" if saved.get(BIZ_BAO) else "✗(未取到, 该区留空)"
        self.sink.log(f"📄 美团报表: 全部业务 ✓({cols.get(BIZ_ALL)}列) 拼好饭 {bao_txt}")
        return True

    @staticmethod
    def _csv_cols(path):
        """读 CSV 表头列数(美团导出 gb18030; 全部业务 83 列 / 拼好饭 31 列)"""
        try:
            with open(path, "rb") as fh:
                raw = fh.read(65536)
            try:
                text = raw.decode("gb18030")
            except UnicodeDecodeError:
                text = raw.decode("gb18030", "ignore")
            first = next((l for l in text.splitlines() if l.strip()), "")
            return len(first.split(","))
        except Exception:
            return 0

    # ---- acctId(账号ID) ----
    # 裸链 /igate/bizdata/report/download 在部分账号上返回 {"code":30000,"msg":"acctId不存在"}
    # (会话里没有账号上下文时后端要显式 ?acctId=...)。这里从门户页/存储/cookie/链接里把它捞出来。
    _ACCT_JS = """() => {
      const ID = /^\\d{5,}$/;
      try { const m = location.href.match(/[?&#]acct[_-]?id=(\\d+)/i); if (m) return m[1]; } catch (e) {}
      for (const st of [window.localStorage, window.sessionStorage]) {
        try {
          for (let i = 0; i < st.length; i++) {
            const k = st.key(i) || '';
            const v = st.getItem(k) || '';
            if (!/acct/i.test(k)) continue;
            const m = v.match(/"?acct[_-]?id"?\\s*[:=]\\s*"?([0-9]{5,})/i) || v.match(/^\\s*"?([0-9]{5,})"?\\s*$/);
            if (m && ID.test(m[1])) return m[1];
          }
        } catch (e) {}
      }
      try {
        const c = (document.cookie || '').match(/acct[_-]?id=([0-9]{5,})/i);
        if (c) return c[1];
      } catch (e) {}
      try {
        for (const a of document.querySelectorAll('a[href]')) {
          const m = (a.href || '').match(/[?&#]acct[_-]?id=([0-9]{5,})/i);
          if (m) return m[1];
        }
      } catch (e) {}
      try {
        const h = document.documentElement.innerHTML || '';
        const m = h.match(/acct[_-]?id["']?\\s*[:=]\\s*["']?([0-9]{5,})/i);
        if (m) return m[1];
      } catch (e) {}
      return '';
    }"""

    def _scan_acct_id(self):
        """找 acctId。① 先查 cookie(Playwright 可跨域读; 页面 JS 的 document.cookie 只能看当前域,
        实测登录后 acctId 只落在 waimaieapp 域 → 在 e.waimai 页面上扫永远扫不到) ② 再扫页面。"""
        try:
            for c in self.ctx.cookies():
                v = str(c.get("value") or "")
                if re.fullmatch(r"\d{5,}", v) and re.search(r"acct[_-]?id", str(c.get("name") or ""), re.I):
                    self.sink.log(f"🆔 从 cookie 取到账号ID acctId={v}（{c.get('domain')}）")
                    return v
        except Exception:
            pass
        for p in list(self.ctx.pages):
            try:
                if "meituan.com" not in p.url:
                    continue
                v = p.evaluate(self._ACCT_JS)
                if v:
                    self.sink.log(f"🆔 从页面取到账号ID acctId={v}（{p.url[:60]}）")
                    return str(v)
            except Exception:
                continue
        return ""

    def _discover_acct_id(self, page):
        """取 acctId: 先扫现有页面, 没有再回门户页重扫(门户页通常会把 acctId 落在存储/链接里)。"""
        v = self._scan_acct_id()
        if v:
            return v
        try:
            self.sink.log("🔍 报表页需要账号ID(acctId), 回门户页提取…")
            page.goto(MEITUAN_HOME, wait_until="commit", timeout=60000)
            time.sleep(8)
            v = self._scan_acct_id()
        except Exception as e:
            self.sink.log(f"⚠️ 门户页提取 acctId 失败: {str(e)[:80]}", level="warn")
        if not v:
            self.sink.log("⚠️ 没能提取到 acctId（已尝试 页面URL/本地存储/cookie/页面链接/门户页）", level="warn")
        return v

    # 报表页从 **cookie** 取门店上下文: acctId(账号) + wmPoiId(门店, -1=全部门店)
    # 实测(删掉 wmPoiId cookie → 报表页不再渲染表单/不再跳转 SPA, 页面报
    # 「参数异常:wmPoid,当前wmPoid为:null」)。新 profile 从没选过门店时就会缺 → 这里补上。
    _POI_COOKIE = "wmPoiId"
    _POI_ALL = "-1"
    _COOKIE_DOMAINS = ("waimaieapp.meituan.com", "e.waimai.meituan.com")

    def _ensure_store_cookies(self, acct=""):
        """确保 acctId / wmPoiId 两个 cookie 存在(缺啥补啥)。"""
        try:
            have = {c["name"]: c for c in self.ctx.cookies()}
        except Exception:
            return
        add = []
        if acct and str((have.get("acctId") or {}).get("value") or "") != str(acct):
            for d in self._COOKIE_DOMAINS:
                add.append({"name": "acctId", "value": str(acct), "domain": d, "path": "/"})
        if not ((have.get(self._POI_COOKIE) or {}).get("value") or ""):
            for d in self._COOKIE_DOMAINS:
                add.append({"name": self._POI_COOKIE, "value": self._POI_ALL, "domain": d, "path": "/"})
            self.sink.log(f"🪪 门店上下文缺失 → 补 cookie {self._POI_COOKIE}={self._POI_ALL}(全部门店)")
        if add:
            try:
                self.ctx.add_cookies(add)
                self.sink.log("🪪 已写入报表页所需的 cookie(acctId/wmPoiId)")
            except Exception as e:
                self.sink.log(f"⚠️ 写 cookie 失败(继续尝试, 页面可能仍可用): {str(e)[:70]}", level="warn")

    def _report_page(self, timeout_s=90):
        page = self.page
        if page is None or page.is_closed():
            page = self.ctx.new_page()
        self.page = page
        # 实测(真账号对照): 报表页 /igate/... 需要**会话里有账号上下文**,
        # 未登录 → {"code":30000,"msg":"token不存在"}; 登录了但缺账号上下文 → "...acctId不存在"。
        # 所以先保证门户页访问过(上下文+acctId 落在 cookie/存储里), 已登录时四种 URL 变体均可用。
        acct = getattr(self, "acct_id", "") or self._discover_acct_id(page)
        self.acct_id = acct
        self._ensure_store_cookies(acct)
        tries = []
        if acct:
            tries.append((f"{REPORT_URL}?acctId={acct}", f"带 acctId={acct}"))
        tries.append((REPORT_URL, "裸链"))
        tries.append((SPA_URL, "SPA入口"))     # /igate 是 iframe 端, 未认证时只回 logon 信号; SPA 壳体可兜底
        last_json = ""
        for round_no in (1, 2):
            for url, label in tries:
                page.goto(url, wait_until="domcontentloaded", timeout=90000)
                try:
                    txt = (page.evaluate("() => (document.body ? document.body.innerText : '')") or "").strip()
                    raw = (page.evaluate("() => document.documentElement.innerHTML") or "")[:400]
                except Exception:
                    txt, raw = "", ""
                bad = ""
                if txt.startswith("{") and '"code"' in txt:
                    bad = txt                                     # 后端 JSON(acctId不存在 / token不存在 …)
                elif "postMessage" in raw and "logon" in raw:
                    bad = "logon信号(该会话未被报表应用认证)"
                elif "wmPoid" in txt and "null" in txt:
                    bad = txt.strip()[:180]                      # 参数异常:wmPoid…null(缺门店上下文)
                if bad:
                    last_json = bad
                    self.sink.log(f"⚠️ 报表页({label})未通过: {bad[:110]}", level="warn")
                    continue
                last_json = ""
                break
            if not last_json or round_no == 2:
                break
            # 会话上下文缺失时的自愈: 先访问门户页(把 acctId/上下文重新落到会话), 再试一次
            self.sink.log("ℹ️ 报表页被拒 → 访问门户页刷新会话上下文后重试一次…")
            try:
                page.goto(MEITUAN_HOME, wait_until="commit", timeout=60000)
                time.sleep(8)
                got = self._scan_acct_id()
                if got:
                    acct = self.acct_id = got
            except Exception as e:
                self.sink.log(f"⚠️ 门户页访问失败: {str(e)[:70]}", level="warn")
            tries = ([(f"{REPORT_URL}?acctId={acct}", f"带 acctId={acct}")] if acct else []) \
                + [(REPORT_URL, "裸链")]
        if last_json:
            if "wmPoid" in last_json:
                raise RuntimeError(
                    f"美团报表页缺少门店上下文: {last_json[:160]}"
                    "｜判定: 页面从 cookie 读 wmPoiId, 缺失/为 null 时前端弹「参数异常:wmPoid」"
                    "｜处理: 已自动补 cookie wmPoiId=-1(全部门店); 若仍失败, 请在弹出的浏览器里手动选一次"
                    "「门店 → 全部门店」再重试")
            if "logon" in last_json:
                raise RuntimeError(
                    "美团报表页返回 logon 信号 → **登录态没有覆盖报表应用**(会话失效): 请重新登录"
                    "美团外卖商家后台（e.waimai.meituan.com；若出现短信验证码需人工输入），"
                    "已尝试 带账号ID / 裸链 / SPA 三种入口")
            # 后端 JSON 错误 = 会话问题(不是 URL 问题): token不存在/未登录, acctId不存在=缺账号上下文
            raise RuntimeError(
                f"美团报表页被后端拒绝: {last_json[:180]}"
                "｜判定: **登录态失效或缺少账号上下文**（未登录时返回 token不存在, 登录但缺上下文返回 acctId不存在）"
                "｜处理: 请重新登录美团外卖商家后台（已尝试 带账号ID / 裸链 / SPA 三种入口）")
        end = time.time() + timeout_s
        while time.time() < end:
            time.sleep(3)
            try:
                if page.evaluate("() => !!document.querySelector('span.custom-checkbox-label') "
                                 "|| document.body.innerText.includes('下载数据')"):
                    self.sink.log("✅ 美团报表页已就绪")
                    return page
            except Exception:
                pass
        # 仍未就绪: 把页面上的**实际提示**带出来(门店上下文 toast / 认证失败 / 裸 JSON)
        try:
            body = (page.evaluate("() => (document.body ? document.body.innerText : '')") or "")
        except Exception:
            body = ""
        m = re.search(r"[^\n]{0,40}(参数异常|wmPoid|acctId|认证失败|token)[^\n]{0,60}", body)
        hint = f"｜页面提示: {m.group(0).strip()}" if m else ""
        if "wmPoid" in body or "参数异常" in body:
            hint += "｜判定: 报表页缺少门店上下文(wmPoid); 已自动补 wmPoiId=-1(全部门店)仍失败 → 需人工在页面选一次门店"
        raise RuntimeError(f"美团报表页未就绪(可能登录态失效){hint}: {page.url[:80]}")

    # ---- 日期区间(实测 DOM: 2 个可见 input.roo-input, 值形如 2026-09-17; 点开始框→点格→点结束框→点格) ----
    _DATES_JS = """() => {
      const vis = e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
      const isd = e => vis(e) && /^\\d{4}-\\d{1,2}-\\d{1,2}$/.test((e.value || '').trim());
      return [...document.querySelectorAll('input')].filter(isd).map(e => {
        const r = e.getBoundingClientRect();
        return {x: r.x + r.width / 2, y: r.y + r.height / 2, cx: r.x, cy: r.y, v: e.value.trim()};
      }).sort((a, b) => (a.cy - b.cy) || (a.cx - b.cx));
    }"""
    # roo 日期格子(2026-10-09 实测): 可点格 <td>(有时无 class)/禁用格 td.day.disabled; 双面板取最左。
    _DAY_JS = """(d) => {
      const out = [];
      for (const td of document.querySelectorAll('.roo-datepicker-body td')) {
        if ((td.textContent || '').trim() !== d) continue;
        if (String(td.className).indexOf('disabled') >= 0) continue;
        const r = td.getBoundingClientRect();
        if (r.width <= 0 || r.height <= 0) continue;
        out.push({x: r.x + r.width / 2, y: r.y + r.height / 2, lx: r.x});
      }
      out.sort((p, q) => p.lx - q.lx);
      return out[0] || null;
    }"""
    # 面板头文本(如「2026 十月」); 月份解析在 _panel_month(Python 侧)。
    _MONTH_JS = """() => {
      const vis = e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
      const hs = [...document.querySelectorAll('.roo-datepicker-header')].filter(vis);
      if (hs.length) return (hs[0].innerText || '').slice(0, 60);
      for (const el of document.querySelectorAll('[class*=picker],[class*=calendar],[class*=month]')) {
        if (!vis(el)) continue;
        const t = (el.innerText || '').slice(0, 200);
        if (t && t.indexOf('20') >= 0 && t.length < 80) return t;
      }
      return null;
    }"""

    def _date_inputs(self, page):
        try:
            return page.evaluate(self._DATES_JS) or []
        except Exception:
            return []

    def _read_dates(self, page):
        return [str(x["v"]) for x in self._date_inputs(page)][:2]

    def _set_date_range(self, page, df, dt):
        """设日期区间; 网格点选失败→键盘输入; 都不生效则中止(绝不带错误区间下载)"""
        ins = self._date_inputs(page)
        if len(ins) < 2:
            raise RuntimeError(f"美团报表页未找到日期区间控件(input数={len(ins)})")
        ok = False
        try:
            page.mouse.click(ins[0]["x"], ins[0]["y"])
            time.sleep(1.5)
            ok = self._click_day(page, df)
            if ok:
                ins = self._date_inputs(page) or ins
                page.mouse.click(ins[-1]["x"], ins[-1]["y"])
                time.sleep(1.0)
                ok = self._click_day(page, dt)
        except Exception as e:
            self.sink.log(f"⚠️ 日历点选异常: {str(e)[:70]}", level="warn")
            ok = False
        if not ok:
            self.sink.log("⚠️ 日历网格点选未成功, 改键盘输入日期", level="warn")
            self._type_date(page, df, dt)
        got = self._read_dates(page)
        if got[:2] != [df, dt]:
            raise RuntimeError(f"日期区间设置未生效: 期望 {df}~{dt}, 实际 {got[:2]} (已中止, 不用错误区间下载)")
        self.sink.log(f"📅 日期区间 = {got[0]} ~ {got[1]}")

    def _type_date(self, page, df, dt):
        """键盘直填(roo-input 可编辑): 按"值形如日期"定位输入框 → focus/select → 键入 → Enter。"""
        for i, d in enumerate((df, dt)):
            try:
                ok = page.evaluate("""(i) => {
                  const els = [...document.querySelectorAll('input')].filter(e => {
                    const v = (e.value || '').trim();
                    return v.length >= 8 && v.split('-').length === 3;
                  });
                  const el = els[i];
                  if (!el) return false;
                  el.focus(); el.select();
                  return true;
                }""", i)
                if not ok:
                    continue
                page.keyboard.type(d, delay=40)
                time.sleep(0.4)
                page.keyboard.press("Enter")
                time.sleep(0.9)
            except Exception as e:
                self.sink.log(f"⚠️ 键盘输入日期失败({d}): {str(e)[:60]}", level="warn")

    def _try_day(self, page, d):
        try:
            box = page.evaluate(self._DAY_JS, str(d))
        except Exception:
            box = None
        if not box:
            return False
        page.mouse.click(box["x"], box["y"])
        return True

    def _click_day(self, page, target):
        """点日历格; 月份不对先翻月; 面板未挂载/重渲染时耐心重试(2026-10-09 实测)。"""
        y, m, d = (int(x) for x in target.split("-"))
        for attempt in range(40):
            cur = self._panel_month(page)
            if cur is None:
                time.sleep(0.6)
                continue
            if cur == (y, m):
                if self._try_day(page, d):
                    return True
                time.sleep(0.5)
                continue
            if not self._nav_month(page, (y, m) > cur):
                time.sleep(0.5)
                continue
            time.sleep(0.9)
        return False

    _CN_MONTH = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6,
                 "七": 7, "八": 8, "九": 9, "十": 10, "十一": 11, "十二": 12}

    def _panel_month(self, page):
        """面板头(如「2026 十月」)→ (年, 月); 兼容 2026年10月 / 2026-10 / 中文数字月(2026-10-09)。"""
        try:
            t = page.evaluate(self._MONTH_JS)
        except Exception:
            t = None
        if not t:
            return None
        t2 = t.replace("\n", "").replace(" ", "")
        mm = re.search(r"(20[0-9]{2})[年/-]?(十[一二]?|[一二三四五六七八九]|1[0-2]|0?[1-9])", t2)
        if not mm:
            return None
        tok = mm.group(2)
        m = int(tok) if tok.isdigit() else self._CN_MONTH.get(tok)
        return (int(mm.group(1)), m) if m else None

    def _nav_month(self, page, forward):
        """翻月: roo 双面板 → 取**最左**面板的 chevron 图标(左=上月, 右=下月)点击。"""
        box = page.evaluate("""(fwd) => {
          const want = fwd ? 'chevron-right-new' : 'chevron-left-new';
          const cands = [...document.querySelectorAll('i.roo-icon')]
            .filter(e => String(e.className).indexOf(want) >= 0)
            .filter(e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; });
          cands.sort((a, b) => a.getBoundingClientRect().x - b.getBoundingClientRect().x);
          const el = cands[0];
          if (!el) return null;
          const r = el.getBoundingClientRect();
          return {x: r.x + r.width / 2, y: r.y + r.height / 2};
        }""", forward)
        if not box:
            return False
        page.mouse.click(box["x"], box["y"])
        time.sleep(0.9)
        return True

    def _select_business(self, page, biz):
        try:
            box = page.evaluate("""(biz) => {
              const group = ['全部','外卖','拼好饭'], hits = {};
              for (const el of document.querySelectorAll('span,li,label,div,a')) {
                const t = Array.from(el.childNodes).filter(n => n.nodeType === 3)
                  .map(n => n.textContent).join('').trim();
                if (!group.includes(t)) continue;
                const r = el.getBoundingClientRect();
                if (r.width <= 0 || r.height <= 0 || r.width > 90 || r.height > 40) continue;
                (hits[t] = hits[t] || []).push({x: r.x + r.width / 2, y: r.y + r.height / 2});
              }
              // 要求与同组其它 tab 处于同一行(防止误点别处同名文本)
              for (const c of (hits[biz] || [])) {
                if (group.filter(g => (hits[g] || []).some(o => Math.abs(o.y - c.y) < 8)).length >= 2)
                  return c;
              }
              return null;
            }""", biz)
            if not box:
                self.sink.log(f"⚠️ 未找到业务选择「{biz}」(沿用当前业务)", level="warn")
                return False
            page.mouse.click(box["x"], box["y"])
            time.sleep(1.5)
            self.sink.log(f"✔ 业务选择 → {biz}")
            return True
        except Exception as e:
            self.sink.log(f"⚠️ 业务选择失败({biz}): {str(e)[:60]}", level="warn")
            return False

    def _checked_count(self, page):
        try:
            return int(page.evaluate(
                "() => [...document.querySelectorAll('input[type=checkbox]')].filter(c => c.checked).length"))
        except Exception:
            return 0

    def _select_all_metrics(self, page):
        """点「全选」(脑图: 可选内容勾选全部); 无全选控件则逐个勾选指标"""
        try:
            box = page.evaluate("""() => {
              for (const el of document.querySelectorAll('span.custom-checkbox-label,label,span')) {
                if ((el.textContent || '').trim() !== '全选') continue;
                const r = el.getBoundingClientRect();
                if (r.width <= 0 || r.height <= 0) continue;
                const root = el.closest('label') || el.parentElement || el;
                const cb = root.querySelector('input[type=checkbox]');
                return {x: r.x + r.width / 2, y: r.y + r.height / 2, checked: cb ? !!cb.checked : null};
              }
              return null;
            }""")
            if box:
                if box.get("checked") is True:
                    self.sink.log(f"☑️ 可选内容「全选」已是勾选态(指标 {self._checked_count(page)} 项), 跳过点击")
                    return True
                page.mouse.click(box["x"], box["y"])
                time.sleep(2)
                n = self._checked_count(page)
                self.sink.log(f"☑️ 可选内容「全选」(已选指标 {n} 项)")
                return n > 0
            rects = page.evaluate("""() => {
              const out = [];
              for (const el of document.querySelectorAll('label')) {
                const t = (el.textContent || '').trim();
                const r = el.getBoundingClientRect();
                if (!t || t.length > 24 || r.width <= 0 || r.height <= 0) continue;
                const cb = el.querySelector('input[type=checkbox]');
                if (cb && cb.checked) continue;
                out.push({x: r.x + r.width / 2, y: r.y + r.height / 2});
              }
              return out.slice(0, 400);
            }""")
            for r in rects:
                page.mouse.click(r["x"], r["y"], delay=5)
            time.sleep(2)
            n = self._checked_count(page)
            self.sink.log(f"☑️ 逐个勾选指标 {len(rects)} 项(已选 {n})")
            return n > 0
        except Exception as e:
            self.sink.log(f"⚠️ 指标勾选异常: {str(e)[:70]}", level="warn")
            return False

    # ---- 生成 + 取文件 ----
    def _history(self, page):
        # 带上 acctId: 后端在会话缺账号上下文时会拒(裸链症状是 code:30000 acctId不存在)
        acct = getattr(self, "acct_id", "")
        url = HISTORY_URL + (f"&acctId={acct}" if acct else "")
        raw = page.evaluate("""async (u) => {
          const r = await fetch(u, {credentials: 'include'});
          return await r.text();
        }""", url)
        return (json.loads(raw).get("data") or {}).get("list") or []

    def _history_names(self, page):
        try:
            return {str(e.get("name")) for e in self._history(page)}
        except Exception:
            return set()

    def _click_download(self, page):
        box = page.evaluate("""() => {
          const out = [];
          for (const el of document.querySelectorAll('button,div,span,a')) {
            if ((el.textContent || '').trim() !== '下载数据') continue;
            const r = el.getBoundingClientRect();
            if (r.width <= 0 || r.height <= 0 || r.width > 220) continue;
            out.push({x: r.x + r.width / 2, y: r.y + r.height / 2, a: r.width * r.height});
          }
          out.sort((p, q) => p.a - q.a);
          return out[0] || null;
        }""")
        if not box:
            raise RuntimeError("美团报表页未找到「下载数据」按钮")
        page.mouse.click(box["x"], box["y"])
        self.sink.log("⬇️ 已点「下载数据」, 等待平台生成…")

    def _wait_new_file(self, page, before, f, t, timeout_s=240):
        """轮询下载历史, 取本次新出现的文件条目(区间日期标签匹配优先)"""
        end = time.time() + timeout_s
        while time.time() < end:
            time.sleep(6)
            try:
                lst = self._history(page)
            except Exception:
                continue
            fresh = [e for e in lst if str(e.get("name")) not in before and e.get("url")]
            if not fresh:
                continue
            tagged = [e for e in fresh if f in str(e.get("name")) and t in str(e.get("name"))]
            pick = tagged or fresh
            pick.sort(key=lambda e: str(e.get("operTime") or e.get("name")), reverse=True)
            if str(pick[0].get("status")) != "2":
                continue
            return pick[0]
        return None

    def _fetch_file(self, entry, path):
        """下载历史里的链接 → 落盘。

        ⚠️ 2026-10-01 真机诊断包(diag_下载报表_20261001-230733.json): 报
        `取文件失败 HTTP 403: distribute-platform-pub.sankuai.com/epassport/download…`
        → 报表没落到目录 → 整个任务失败("目录内无 2026-09-30 全部业务报表")。
        CDN 的签名下载链接对**直连请求**(APIRequestContext)判定 403 —— 它要的是浏览器现场
        发起的请求(cookie/Referer/UA 齐全)。所以改成:
          ① **浏览器原生下载**(锚点点击 + expect_download) —— 最稳, 走 Chromium 自己的请求;
          ② 接口直取降级为兜底, 并补上 Referer(缺它 CDN 常判 403)。
        两条路都以"落盘的是不是真 xlsx(zip 魔数 PK)"为准, 绝不把错误页当报表存下。
        """
        url = str(entry.get("url") or "")
        if not url:
            return False
        # ① 浏览器原生下载(需要活着的 page; 页面未就绪就直接走 ②)
        page = self.page
        try:
            if page is None:
                raise RuntimeError("页面未就绪")
            with page.expect_download(timeout=180000) as _dl:
                page.evaluate("""(u) => {
                    const a = document.createElement('a');
                    a.href = u; a.download = ''; a.rel = 'noopener';
                    document.body.appendChild(a); a.click();
                    setTimeout(() => a.remove(), 0); }""", url)
            _dl.value.save_as(path)
            if os.path.exists(path) and os.path.getsize(path) > 0:
                with open(path, "rb") as fh:
                    magic = fh.read(2)
                if magic == b"PK":
                    self.sink.log(f"💾 已保存 {os.path.basename(path)} "
                                  f"({os.path.getsize(path) / 1024:.0f}KB · 浏览器下载)")
                    return True
                self.sink.log(f"ℹ️ 浏览器下载到的不是 xlsx(魔数 {magic!r}) → 改试接口直取",
                              level="warn")
        except Exception as e:
            self.sink.log(f"ℹ️ 浏览器下载未成功({str(e)[:50]}) → 改试接口直取", level="warn")
        # ② 兜底: APIRequestContext 直取(补 Referer)
        try:
            r = self.ctx.request.get(url, timeout=180000,
                                     headers={"Referer": (page.url if page else "") or
                                                          "https://waimaieapp.meituan.com/"})
            body = r.body()
            if not r.ok or body[:1] == b"<":
                self.sink.log(f"⚠️ 取文件失败 HTTP {r.status}: {url[:60]}", level="warn")
                return False
            with open(path, "wb") as fh:
                fh.write(body)
            self.sink.log(f"💾 已保存 {os.path.basename(path)} ({len(body) / 1024:.0f}KB)")
            return True
        except Exception as e:
            self.sink.log(f"⚠️ 取文件异常: {str(e)[:70]}", level="warn")
            return False

    # ---------- 抓取(解析已下载 CSV; 失败返回 {} 由编排层硬失败) ----------
    def capture_stats(self, date_from=None, date_to=None, directory=None) -> dict:
        from . import meituan_mapping as mt_map
        ddir = directory or getattr(self, "download_dir", None)
        if not ddir:
            self.sink.log("⚠️ 美团抓取: 未指定报表目录", level="warn")
            return {}
        all_p, bao_p = mt_map.find_files(ddir, date_from, date_to)
        if not all_p:
            self.sink.log(f"⚠️ 美团抓取: 目录内无 {date_from}~{date_to} 全部业务报表({ddir})", level="warn")
            return {}
        all_rows = mt_map.rows_by_date(all_p)
        bao_rows = mt_map.rows_by_date(bao_p) if bao_p else {}
        self.sink.log(f"🔎 美团报表解析: 全部业务 {len(all_rows)}天(每日期行=门店数) / "
                      f"拼好饭 {len(bao_rows)}天{'' if bao_p else ' [报表缺失]'}")
        out = {"platform": "meituan", "all": all_rows, "bao": bao_rows,
               "files": {"all": all_p, "bao": bao_p}}
        # 推广(CE~CG): 独立数据域, 失败只降级(留空)不硬失败 —— 数据在报表外, 属增强列
        try:
            promo = self.capture_promo()
            if promo:
                out["promo"] = promo
        except Exception as e:
            self.sink.log(f"⚠️ 推广数据抓取失败(列留空): {str(e)[:90]}", level="warn")
        return out

    # ---------- 推广(经营数据→推广页, 跨域iframe独立直开) ----------
    PROMO_URL = ("https://waimaieapp.meituan.com/ad/v1/rpc?jumpAuthorize=true"
                 "#/subapp/isomor_commonpage/pages/mulEffectData/index")

    _PROMO_READ_JS = """() => {
      const t = document.body.innerText;
      if (t.indexOf('总推广花费') < 0) return null;
      const pick = (label) => {
        const i = t.indexOf(label);
        const seg = t.slice(i + label.length, i + label.length + 60).replace(/\\n/g, ' ');
        const m = seg.match(/[\\d,.]+/);
        return m ? parseFloat(m[0].replace(/,/g, '')) : null;
      };
      const date = (t.match(/2026[.\\-]\\d{2}[.\\-]\\d{2}/) || [null])[0];
      if (!date) return null;
      return {date: date.replace(/\\./g, '-'), spend: pick('总推广花费'),
              expose: pick('总曝光量'), visit: pick('总进店量')};
    }"""

    # ---- 商圈Top(仅单门店视角可得) ----
    # 实测路径(用户截图确认): 报表页右上角切单店 → 经营数据/流量 → 「流量转化」漏斗
    #   .funnel_tabs-warpper .selector-item          全部顾客 / 新客 / 老客
    #   .funnel_indicator-wrapper .dropdown input    对比基准(默认「商圈同行均值」, 可切「商圈同行前10%均值」)
    #   .funnel_ladder-diagram.reverse_left          本店(level1=曝光/level2=入店/level3=下单)
    #   .funnel_ladder-diagram.reverse_right         对比(商圈) —— 这才是模板要的「商圈TOP」值
    # 「切单店」用 cookie wmPoiId=<门店编号>(等于右上角门店选择器), 抓完恢复 -1(全部门店)。
    FLOW_URL = "https://waimaieapp.meituan.com/igate/bizdata/flowrate"
    _CIRCLE_JS = """() => {
      const wrap = document.querySelector('.flowrate_funnel-warpper');
      if (!wrap) return null;
      const pick = (cls, rateCls) => {
        const lad = wrap.querySelector('.funnel_ladder-diagram.' + cls);
        if (!lad) return null;
        const lv = Array.from(lad.querySelectorAll('.funnel_ladder-diagram-single')).map(el => {
          const uv = el.querySelector('.funnel_item-uv span');
          const pv = el.querySelector('.funnel_item-pv span');
          return [(uv ? uv.textContent : '').trim(), (pv ? pv.textContent : '').trim()];
        });
        const rw = wrap.querySelector('.funnel_conversion-rate.' + rateCls);
        const rates = rw ? Array.from(rw.querySelectorAll('.data__item--number')).map(e => e.textContent.trim()) : [];
        return {lv, rates};
      };
      const dd = wrap.querySelector('.funnel_indicator-wrapper .dropdown input');
      const active = wrap.querySelector('.funnel_tabs-warpper .selector-item.active');
      return {bench: dd ? dd.value : '', tab: active ? (active.textContent || '').trim() : '',
              left: pick('reverse_left', 'conversion-rate-left'),
              right: pick('reverse_right', 'conversion-rate-right')};
    }"""

    def _set_poi_cookie(self, value):
        for d in ("waimaieapp.meituan.com", "waimaie.meituan.com", "e.waimai.meituan.com"):
            try:
                self.ctx.add_cookies([{"name": "wmPoiId", "value": str(value), "domain": d, "path": "/"}])
            except Exception:
                pass

    @staticmethod
    def _num(s):
        try:
            return float(str(s).replace(",", "").replace("%", "").strip())
        except Exception:
            return None

    def _funnel_side(self, raw, prefix=""):
        """漏斗一侧 → {曝光,进店,下单,进店转化率,下单转化率}(取人数 uv, 不是次数 pv)"""
        if not raw:
            return {}
        lv = raw.get("lv") or []
        rates = raw.get("rates") or []
        out = {}
        for idx, key in enumerate(("曝光", "进店", "下单")):
            if idx < len(lv) and lv[idx]:
                out[key] = self._num(lv[idx][0])
        if len(rates) >= 1:
            out["进店转化率"] = self._num(rates[0])
        if len(rates) >= 2:
            out["下单转化率"] = self._num(rates[1])
        return out

    def capture_circle(self, store_id, date=None, timeout_s=75):
        """抓该店(单门店视角)的「商圈同行前10%均值」流量值。

        返回 {"bench": 口径, "date": 日期, "main": {整体/新客/老客: {...}}, "bao": {...}};
        抓不到返回 None(绝不臆造)。业务=外卖→main, 业务=拼好饭→bao。
        """
        page = self.ctx.new_page()
        saved = [(c["domain"], c["path"], c["value"]) for c in self.ctx.cookies() if c["name"] == "wmPoiId"]
        result = {"bench": "", "date": date, "main": {}, "bao": {}}
        try:
            self._set_poi_cookie(store_id)
            page.goto(self.FLOW_URL, wait_until="domcontentloaded", timeout=90000)
            end = time.time() + timeout_s
            raw = None
            while time.time() < end:
                time.sleep(0.8)                         # 0.8s 轮询(原 3s): 漏斗出现即走, 上限不变
                try:
                    raw = page.evaluate(self._CIRCLE_JS)
                except Exception:
                    raw = None
                if raw and raw.get("right"):
                    break
            if not raw or not raw.get("right"):
                # 现场取证: 「落在登录页(没登录)」和「页面结构变了」必须能区分, 否则远程没法定位
                try:
                    _t = (page.evaluate("() => document.body.innerText") or "").replace("\n", " ")
                except Exception:
                    _t = "(页面读取失败)"
                _login = (("登录" in _t) and ("验证码" in _t or "密码" in _t or "注册" in _t)) \
                    or ("login" in (page.url or ""))
                self.sink.log(
                    f"⚠️ 商圈: 未渲染出流量转化漏斗"
                    f"({'疑似未登录 —— 该店跳过, 请先在客户端里登录美团外卖' if _login else '页面结构可能变化'})"
                    f"｜{page.url[:90]}｜页面文本: {_t[:160]}", level="warn")
                return None
            if date:
                if not self._set_flow_date(page, date):
                    # ⚠️ 日期没确认 = 页面很可能还停在默认「昨日」→ 这份数不是该日的商圈值。
                    # 2026-10-02 用户报"美团没按报表区间处理, 默认选的昨日" → 宁可留空, 不写错日期。
                    self.sink.log(f"⚠️ 商圈: 流量页日期未能确认({date}) → 该店商圈列留空"
                                  f"(不用默认日期的数据冒充)", level="warn")
                    return None
                time.sleep(3)
            # ⚠️ 2026-10-08 真机: 漏斗上方有「业务」下拉, 选项 = **全部 / 外卖 / 拼好饭**(默认值「全部」✗)。
            #    旧实现假设"该页默认就是外卖口径"(依据 09-28 的用户截图 ✗) → 实际默认「全部」
            #    = 外卖+拼好饭 → 写进**主站 TOP 列**的偏高(全部口径), 而**拼好饭那组一直空**。
            #    现在: 先切「外卖」读主站, 再切「拼好饭」读拼好饭; 切换拿不到硬证据就不写那一组。
            bench = self._set_bench_top10(page)
            if bench:
                result["bench"] = bench

            def _read_groups():
                got_g = {}
                for tab in ("全部顾客", "新客", "老客"):
                    if not self._click_funnel_tab(page, tab):
                        self.sink.log(f"ℹ️ 商圈: 漏斗标签「{tab}」没找到, 跳过该组", level="warn")
                        continue
                    time.sleep(5)
                    r2 = page.evaluate(self._CIRCLE_JS)
                    if not r2:
                        continue
                    grp = {"全部顾客": "整体", "新客": "新客", "老客": "老客"}[tab]
                    side = self._funnel_side(r2.get("right"))
                    if side:
                        got_g[grp] = side
                return got_g

            # ① 外卖 → 主站 TOP 列
            got = {}
            if self._set_flow_biz(page, "外卖"):
                got = _read_groups()
            else:
                self.sink.log("⚠️ 商圈: 业务未能切到「外卖」→ 主站 TOP 列留空"
                              "(不拿「全部」口径冒充外卖)", level="warn")
            if got:
                _bench = result["bench"] or ""
                if self._bench_is_circle(_bench):
                    result["main"] = got
                    self.sink.log(f"📊 商圈-外卖({_bench}): "
                                  + " ".join(f"{g}曝光{v.get('曝光')}" for g, v in got.items()))
                else:
                    # 硬门禁: 对比基准若还是「比前一日/比上周」这类**别的口径**, 右值不是
                    # 商圈同行数据 → 一律不写(宁可留空, 也绝不把别的口径写进客户表)。
                    self.sink.log(f"⚠️ 商圈: 对比基准为「{_bench or '未知'}」而非商圈同行 → "
                                  f"本次不写 TOP 列(避免把别的口径当商圈数据)", level="warn")
            else:
                self.sink.log("⚠️ 商圈: 三组(全部/新客/老客)都没读到右值 —— 见上方取证",
                              level="warn")
            # ② 拼好饭 → 拼好饭 TOP 列(同一个漏斗, 只切业务)
            # ⚠️ 2026-10-08 真机更正: 拼好饭**不是**"独立板块、本页取不到" —— 它就是漏斗上方
            #    「业务」下拉里的第三项(全部/外卖/拼好饭)。切过去后同一组漏斗的右列即拼好饭商圈值。
            if self._set_flow_biz(page, "拼好饭"):
                _bao = _read_groups()
                _bench2 = result["bench"] or ""
                if _bao and self._bench_is_circle(_bench2):
                    result["bao"] = _bao
                    self.sink.log(f"📊 商圈-拼好饭({_bench2}): "
                                  + " ".join(f"{g}曝光{v.get('曝光')}" for g, v in _bao.items()))
                elif _bao:
                    self.sink.log(f"⚠️ 商圈-拼好饭: 对比基准为「{_bench2 or '未知'}」而非商圈同行"
                                  f" → 不写(避免把别的口径当商圈数据)", level="warn")
            else:
                self.sink.log("ℹ️ 商圈: 业务未能切到「拼好饭」→ 拼好饭 TOP 列留空", level="warn")
            return result if result["main"] else None
        except Exception as e:
            self.sink.log(f"⚠️ 商圈抓取异常(该店留空): {str(e)[:110]}", level="warn")
            return None
        finally:
            try:
                if saved:
                    self.ctx.add_cookies([{"name": "wmPoiId", "value": v, "domain": d, "path": p}
                                          for d, p, v in saved])
                else:
                    self._set_poi_cookie("-1")
            except Exception:
                pass
            try:
                page.close()
            except Exception:
                pass

    def _set_flow_biz(self, page, biz):
        """把流量转化页的「业务」切到 外卖 / 拼好饭（选项：全部 / 外卖 / 拼好饭）。

        2026-10-08 真机: 该下拉默认值是「全部」(= 外卖+拼好饭), 老实现把它当外卖口径 →
        主站 TOP 列偏高 ✗、拼好饭 TOP 列一直空 ✗。验收与日期一致: 切完**直读 input.value**,
        对不上返回 False → 调用方留空, 宁可不写也不写错。
        """
        try:
            cur = self._flow_biz(page)
            if cur == biz:
                return True
            marked = page.evaluate("""() => {
                const ins = [...document.querySelectorAll('input.customRoo-input, input[class*=customRoo]')]
                  .filter(el => { const r = el.getBoundingClientRect(); return r.width > 8 && r.height > 8; });
                document.querySelectorAll('[data-hermes-biz]').forEach(e => e.removeAttribute('data-hermes-biz'));
                if (!ins.length) return 0;
                ins[0].setAttribute('data-hermes-biz', '1');
                return ins.length; }""")
            if not marked:
                return False
            page.locator('[data-hermes-biz="1"]').first.click(timeout=6000)
            time.sleep(2)
            opt = page.evaluate("""(v) => {
                const els = [...document.querySelectorAll('.customRoo-selector-option-default, li, [class*=option]')]
                  .filter(el => { const r = el.getBoundingClientRect();
                                  return r.width > 8 && r.height > 8
                                         && (el.innerText || '').trim() === v; });
                document.querySelectorAll('[data-hermes-biz-opt]').forEach(e => e.removeAttribute('data-hermes-biz-opt'));
                if (!els.length) return 0;
                els[0].setAttribute('data-hermes-biz-opt', '1');
                return els.length; }""", biz)
            if not opt:
                return False
            page.locator('[data-hermes-biz-opt="1"]').first.click(timeout=6000)
            time.sleep(4)
            ok = self._flow_biz(page) == biz
            self.sink.log(f"🔀 流量页业务 → {biz}（{'已生效' if ok else '未确认'}）")
            return ok
        except Exception as e:
            self.sink.log(f"⚠️ 切换流量页业务失败({str(e)[:60]})", level="warn")
            return False

    @staticmethod
    def _flow_biz(page):
        """读流量页「业务」下拉的当前值(全部 / 外卖 / 拼好饭)。"""
        try:
            return (page.evaluate("""() => {
                const el = document.querySelector('input.customRoo-input, input[class*=customRoo]');
                return el ? (el.value || '') : ''; }""") or "").strip()
        except Exception:
            return ""

    _FLOW_HEADS_JS = """() => [...document.querySelectorAll('.roo-datepicker-header')]
        .filter(e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; })
        .map(e => (e.innerText || '').trim())"""

    def _flow_visible_month(self, page):
        """流量页「可见」面板的月标题(如「2026十月」, 中文数字月) → (年, 月)。
        隐藏年图层有同名 header(w=0, 内容如「2021-2032」)是干扰 → 按可见性过滤, 解析不了就跳过。"""
        try:
            heads = page.evaluate(self._FLOW_HEADS_JS) or []
        except Exception:
            heads = []
        for t in heads:
            t2 = (t or "").replace(" ", "").replace("\n", "")
            mm = re.search(r"(20\d{2})\s*(十[一二]?|[一二三四五六七八九]|1[0-2]|0?[1-9])", t2)
            if mm:
                tok = mm.group(2)
                mval = int(tok) if tok.isdigit() else self._CN_MONTH.get(tok)
                if mval:
                    return (int(mm.group(1)), mval)
        return None

    def _set_flow_date(self, page, date):
        """把流量页日期设为指定日 —— **必须拿到硬证据才算生效**。

        2026-10-08 真机破案(用户报"美团商圈TOP一直没写入"): 本方法**两个** bug 叠加, 日期根本没设进去,
        而我上次加的验收把 18 家店全拦下(证据: 输入框=['',''], 已选时间=默认的 2026-10-07) —— 验收是对的,
        设置是坏的:
          ① 「自定义」元素 innerText 实际是 `自定义\n~`(带分隔符), 用 `=== '自定义'` 精确匹配 → 永远点不到;
          ② roo 的 range 日期选择器**不吃 input.value/键入** → 必须在它自己的面板里**点两次同一格**
             (第1次=区间起点, 第2次=区间终点; 实测点1次后仍 10-07, 点2次后变 10-06 且两个输入框都填上)。
        现在: 归一化匹配点「自定义」→ 面板里打标记 → locator 点两次目标日格子(跳过 disabled/old)
        → 再读输入框 + 「已选时间：」双证据验收。任一步不成立 → False(调用方放弃该店, 不用默认日期的数冒充)。

        2026-10-10 跨月修复: 旧翻月用 `document.querySelector('.roo-datepicker-data-panel...')` 抓到的是
        **隐藏年图层**(头「2021-2032」, 翻页=年跳) → 跨月目标 18/18 全挂(读回全停在默认日)。改法:
        可见月标题读月份(「2026十月」中文数字月) + **可见面板里的单月 chevron-left/right** 逐月逼近;
        另修: 打开面板偶发首点不生效 → 以可见头为准重试点击。真机验证: probe_flow_date6。
        """
        try:
            # ① 点「自定义」: 归一化(去空白/去 ~ 分隔符)匹配 + JS 打标记 + locator 点击
            n = page.evaluate("""() => {
                const norm = s => (s || '').replace(/\s+/g, '').replace(/[~～]/g, '');
                const els = [...document.querySelectorAll('.selector-item')]
                  .filter(e => norm(e.innerText) === '自定义');
                document.querySelectorAll('[data-hm-custom]').forEach(e => e.removeAttribute('data-hm-custom'));
                if (!els.length) return 0;
                els[0].setAttribute('data-hm-custom', '1'); return els.length; }""")
            if n:
                try:
                    page.locator('[data-hm-custom="1"]').first.click(timeout=8000)
                except Exception:
                    pass
            # ② 等面板打开(2026-10-10 真机: 首点偶发不生效 → 以「可见月标题」为准重试;
            #    页面里有多层同名 .roo-datepicker-header, 隐藏年图层头(w=0, 如「2021-2032」)是干扰,
            #    必须按可见性过滤; 可见后左面板头形如「2026十月」=中文数字月)
            ins = page.locator("input[placeholder='开始时间'], input[placeholder='结束时间']")
            _heads = []
            for _try in range(5):
                time.sleep(1.5)
                _heads = page.evaluate(self._FLOW_HEADS_JS) or []
                if _heads:
                    break
                n2 = page.evaluate("""() => {
                    const norm = s => (s || '').replace(/\\s+/g, '').replace(/[~～]/g, '');
                    const els = [...document.querySelectorAll('.selector-item')].filter(e => norm(e.innerText) === '自定义');
                    document.querySelectorAll('[data-hm-custom]').forEach(e => e.removeAttribute('data-hm-custom'));
                    if (!els.length) return 0;
                    els[0].setAttribute('data-hm-custom', '1'); return els.length; }""")
                if n2:
                    try:
                        page.locator('[data-hm-custom="1"]').first.click(timeout=8000)
                    except Exception:
                        pass
            if not _heads:
                self.sink.log("   ⚠️ 流量页「自定义」面板未能打开 → 该店商圈列留空", level="warn")
                return False
            # ③ 翻月到目标月: 只能点**可见面板里的单月 chevron**(chevron-left/right);
            #    double-left/right 是年跳(2026-10-10 实测点它会跳错年), 不能用
            _y, _m, _d = date.split("-")
            _day = str(int(_d))
            _want = (int(_y), int(_m))
            for _step in range(36):
                cur = self._flow_visible_month(page)
                if cur is None or cur == _want:
                    break
                _box = page.evaluate("""(fwd) => {
                    const want = fwd ? 'chevron-right' : 'chevron-left';
                    const cands = [...document.querySelectorAll('i.roo-icon')]
                      .filter(e => { const c = String(e.className);
                                     return c.indexOf(want) >= 0 && c.indexOf('double') < 0; })
                      .filter(e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; });
                    if (!cands.length) return null;
                    cands.sort((a, b) => a.getBoundingClientRect().x - b.getBoundingClientRect().x);
                    const el = fwd ? cands[cands.length - 1] : cands[0];
                    const r = el.getBoundingClientRect();
                    return {x: r.x + r.width / 2, y: r.y + r.height / 2}; }""", _want > cur)
                if not _box:
                    break
                page.mouse.click(_box["x"], _box["y"])
                time.sleep(1.0)
            # ④ 点两次目标日格子(range: 第1次起点, 第2次终点)。
            #    ⚠️ old/disabled 必须看 **td 自身**的 class(A 元素 class 为空, 看 A 会点到邻月的灰格)
            for _pass in range(3):
                _marked = page.evaluate("""(o) => {
                    const day = o.day;
                    const panels = [...document.querySelectorAll('.roo-datepicker-data-panel.datepicker-days')]
                      .filter(p => p.offsetParent !== null && p.getBoundingClientRect().width > 80);
                    let cells = [];
                    for (const p of panels) {
                        cells = [...p.querySelectorAll('td')].filter(td => {
                            const a = td.querySelector('a'); if (!a) return false;
                            const t = (a.innerText || '').trim();
                            const cls = (td.className || '').toString();
                            return t === day && td.offsetParent !== null
                                   && !cls.includes('disabled') && !cls.includes('old')
                                   && !cls.includes('month'); });
                        if (cells.length) break;
                    }
                    document.querySelectorAll('[data-hm-day]').forEach(e => e.removeAttribute('data-hm-day'));
                    if (!cells.length) return 0;
                    cells[0].setAttribute('data-hm-day', '1');
                    return cells.length; }""", {"day": _day})
                if not _marked:
                    break
                try:
                    page.locator('[data-hm-day="1"]').first.click(timeout=6000)
                except Exception:
                    pass
                time.sleep(1.6)
            # ⑤ 硬证据验收: 输入框 value + 「已选时间：<date>」
            vals = []
            for i in range(min(ins.count(), 2)):
                try:
                    vals.append((ins.nth(i).input_value() or "").strip())
                except Exception:
                    vals.append("")
            hit_input = len(vals) == 2 and all(v == date for v in vals)
            txt = page.evaluate("() => (document.body ? document.body.innerText : '')") or ""
            hit_text = any(f"已选时间：{d}" in txt for d in self._date_variants(date))
            ok = hit_input or hit_text
            self.sink.log(f"📅 流量页日期 → {date}"
                          f"（{'已生效' if ok else '未确认'}"
                          f"{'·输入框=' + str(vals) if vals else ''}）")
            if not ok:
                frag = [x.strip() for x in txt.replace("\n", "|").split("|")
                        if x.strip() and any(ch.isdigit() for ch in x)][:12]
                self.sink.log(f"   🔎 流量页日期取证(未确认): {frag}", level="warn")
            return ok
        except Exception as e:
            self.sink.log(f"⚠️ 流量页日期设置失败(该店商圈列将留空): {str(e)[:70]}", level="warn")
            return False

    @staticmethod
    def _date_variants(date):
        """同一日期的常见写法(页面可能用 / 或 . 分隔, 或用 月日)。"""
        out = [date, date.replace("-", "/"), date.replace("-", ".")]
        try:
            y, m, d = date.split("-")
            out += [f"{m}-{d}", f"{int(m)}月{int(d)}日"]
        except Exception:
            pass
        return out

    def _dump_flow_candidates(self, page, why):
        """业务切换失败时的**现场取证**: 把所有 frame 里像「业务/外卖/拼好饭」的控件列出来。

        为什么要它: 现场(2026-09-28)是「业务『外卖』切换失败, 该项留空」——原因可能是
        下拉控件改版(老代码靠 top/left 位置启发式找 .dropdown, 页面一改就找不到)。远程
        没有真实 DOM 就只能靠猜, 那是把错误数据写进客户表的前奏。这里把候选控件(文案/
        类名/坐标)打进日志, 用户跑一次我们就能按证据改。
        """
        try:
            info = page.evaluate("""() => {
              const seen = new Set(), out = [];
              for (const el of document.querySelectorAll('span,div,li,button,label,input')) {
                const t = (el.textContent || el.value || '').trim();
                if (!t || t.length > 14) continue;
                if (!/外卖|拼好饭|全部业务|业务|商圈|同行|前10/.test(t)) continue;
                const r = el.getBoundingClientRect();
                if (r.width <= 0 || r.height <= 0) continue;
                const k = t + '|' + String(el.className || '').slice(0, 40);
                if (seen.has(k)) continue;
                seen.add(k);
                out.push({t: t.slice(0, 14), tag: el.tagName,
                          cls: String(el.className || '').slice(0, 50),
                          x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width)});
                if (out.length >= 18) break;
              }
              return out;
            }""")
        except Exception as e:
            self.sink.log(f"ℹ️ 商圈取证失败: {str(e)[:60]}", level="warn")
            return
        self.sink.log(f"🔎 商圈取证({why}): 共 {len(info)} 个候选控件", level="warn")
        for c in info:
            self.sink.log(f"    · {c['tag']:5s} @({c['x']},{c['y']}) w={c['w']} "
                          f"| {c['t']!r} | {c['cls']}", level="warn")

    # 注: 旧的 `_set_flow_biz` 已删除(2026-10-08) —— 它用位置启发式找 .dropdown + mouse.click 坐标点击,
    # 选项还要求 children.length===0(真机 li 内含 span → 恒 false) → 永远切不动业务。新实现见本文件
    # 靠前的同名方法: JS 打标记 + locator 点击 + **读 input.value 验收**。

    @staticmethod
    def _bench_is_circle(bench):
        """对比基准是不是「商圈同行」口径 —— **只有这种口径的右值才能当商圈TOP写进表**。
        对比基准若是「比前一日/比上周」这类, 右值是别的语义, 写进去就是把错误数据交给客户。"""
        return ("商圈同行" in (bench or "")) or ("前10" in (bench or ""))

    def _bench_now(self, page):
        """当前对比基准文案: 先按旧内部选择器读, 读不到就**按文案**扫
        (平台对外文案比内部类名稳 —— 2026-09-28 用户截图确认文案是「商圈同行前10%均值」)。"""
        try:
            v = page.evaluate("""() => { const el = document.querySelector(
                '.flowrate_funnel-warpper .funnel_indicator-wrapper .dropdown input');
                return el ? (el.value || el.innerText || '') : ''; }""")
            if v and str(v).strip():
                return str(v).strip()
        except Exception:
            pass
        try:
            return str(page.evaluate("""() => {
              for (const el of document.querySelectorAll('span,div,button,input')) {
                const t = ((el.value || el.textContent) || '').trim();
                if (!t || t.length > 20) continue;
                if (!/^(商圈同行.*均值|比前一日|比上周)/.test(t)) continue;
                const r = el.getBoundingClientRect();
                if (r.width > 0 && r.height > 0) return t;
              }
              return '';
            }""") or "").strip()
        except Exception:
            return ""

    def _set_bench_top10(self, page):
        """把「流量转化」的对比基准切到**商圈同行前10%均值**; 没有该选项就保持默认并记下实际口径。

        2026-09-28 用户截图确认的路径: 漏斗右上角的下拉就是「对比基准」, 选项 = 商圈同行均值 /
        商圈同行前10%均值。旧实现只认一个很长的内部选择器(改版即失效) → 现在优先旧选择器,
        失败则**按文案**找控件与选项。
        """
        try:
            cur = self._bench_now(page)
            if "前10" in cur:
                return cur
            # ① 打开下拉: JS 打标记 + locator 点(坐标 mouse.click 在多层结构里会落错)
            trig_ok = page.evaluate("""() => {
              const cands = [...document.querySelectorAll('input,span,div')].filter(el => {
                const t = ((el.value || el.textContent) || '').trim();
                if (!t || t.length > 20 || !/^商圈同行.*均值/.test(t)) return false;
                const r = el.getBoundingClientRect();
                return r.width > 0 && r.height > 0 && r.width < 400;
              });
              const inner = cands.filter(el => !cands.some(o => o !== el && el.contains(o)));
              document.querySelectorAll('[data-hermes-bench]').forEach(e => e.removeAttribute('data-hermes-bench'));
              if (!inner.length) return 0;
              inner[0].setAttribute('data-hermes-bench', '1');
              return inner.length;
            }""")
            if not trig_ok:
                self.sink.log(f"ℹ️ 商圈: 没找到对比基准下拉, 用当前口径「{cur or '未知'}」", level="warn")
                self._dump_flow_candidates(page, "没找到对比基准下拉")
                return cur
            page.locator('[data-hermes-bench="1"]').first.click(timeout=6000)
            time.sleep(3)
            # ② 点「商圈同行前10%均值」——
            #    ⚠️ 2026-10-01 真机: 旧判断要求 `el.children.length === 0`(无子节点), 但真机的选项
            #    LI **内部还有 span**(文本在孙子节点里) → 判断恒 false → 每次都报「该店无前10%均值选项」,
            #    于是抓回来的是默认口径「商圈同行均值」→ 用户指出: 美团外卖的商圈TOP 就是"前10%均值"。
            #    改法与淘宝一致: 只筛"最内层"候选(去掉还包含其它候选的元素), 再用 locator 点。
            hit = page.evaluate("""() => { const norm = s => (s||'').replace(/\\s+/g,'');
                const want = norm('商圈同行前10%均值');
                const cands = [...document.querySelectorAll('li,div,span')].filter(el => {
                  const r = el.getBoundingClientRect();
                  return r.width > 0 && r.height > 0 && norm(el.innerText) === want; });
                const inner = cands.filter(el => !cands.some(o => o !== el && el.contains(o)));
                document.querySelectorAll('[data-hermes-bench-opt]').forEach(e => e.removeAttribute('data-hermes-bench-opt'));
                if (!inner.length) return false;
                inner[0].setAttribute('data-hermes-bench-opt', '1');
                return true; }""")
            if hit:
                try:
                    page.locator('[data-hermes-bench-opt="1"]').first.click(timeout=6000)
                except Exception:
                    hit = False
            time.sleep(4)
            now = self._bench_now(page)
            if not hit:
                self.sink.log(f"ℹ️ 商圈: 该店无「商圈同行前10%均值」选项, 用当前口径「{now or cur}」",
                              level="warn")
                self._dump_flow_candidates(page, "对比基准下拉里没有前10%均值选项")
            else:
                self.sink.log(f"📊 商圈口径 → {now or '商圈同行前10%均值'}")
            return now or cur
        except Exception as e:
            self.sink.log(f"ℹ️ 商圈: 对比基准设置异常 {str(e)[:60]}", level="warn")
            return ""

    def _click_funnel_tab(self, page, tab):
        try:
            return bool(page.evaluate("""(lab) => { const norm = s => (s||'').replace(/\\s+/g,'');
                for (const el of document.querySelectorAll('.funnel_tabs-warpper .selector-item')) {
                  if (norm(el.innerText) === norm(lab)) { el.click(); return true; } } return false; }""", tab))
        except Exception:
            return False

    # ---------- 推广账户流水(消费记录) : CJ 充值金额 + CE 推广消耗 + 账户余额 ----------
    # 真机取样(2026-10-08, 单店 高桥 55555555): 页面 /ad/v1/pc#/account?scrollConsume=true
    #   表头: 时间 | 类型 | 变化金额(元) | 余额 | 操作
    #   2026-10-07 00:00:00~23:59:59 | 推广消费        | -237.16 | 109.09
    #   2026-10-07 14:54:46          | 推广账户自动充值 | +200.00 | 346.25
    # 用户/脑图口径: 充值金额 CJ = 当日「推广账户自动充值」合计; 推广消耗 CE = 当日「推广消费」。
    # 固定基址 + hash 路由, **不带** bsid/device_uuid/time 这类会话参数(用户明确提醒 URL 不能写死)。
    PROMO_LEDGER_URL = "https://waimaieapp.meituan.com/ad/v1/pc#/account?scrollConsume=true"

    @staticmethod
    def parse_ledger(rows, date):
        """流水行(文本列表的列表) → {'recharge': 当日充值合计, 'consume': 当日消耗, 'balance': 最新余额}。

        只认**当日**的行(`时间`以 date 开头即可 —— 消费行是区间格式 "2026-10-07 00:00:00~23:59:59",
        充值行是时间戳 "2026-10-07 14:54:46")。金额里的 "预扣款" 之类后缀会被剥掉。
        读不到就给 None —— **不臆造 0**(0 是"确实没充值"的真实值, 拿不到数据是另一回事)。
        """
        def _num(x):
            m = re.search(r"[-+]?\d+(?:\.\d+)?", str(x or "").replace(",", ""))
            return float(m.group()) if m else None

        rec, con, bal, n_rec, n_con = 0.0, 0.0, None, 0, 0
        for r in (rows or []):
            # 脏数据防线: DOM 取回来的行可能是 None / 字符串 / 列数不足 → 直接跳过, 不许崩
            if not isinstance(r, (list, tuple)) or len(r) < 4:
                continue
            cells = [str(c).strip() for c in r]
            if not cells[0].startswith(str(date)):
                continue
            typ, amt = cells[1], cells[2]
            v = _num(amt)
            if v is None:
                continue
            if "充值" in typ:
                rec += v
                n_rec += 1
            elif "消费" in typ:
                con += abs(v)
                n_con += 1
            b = _num(cells[3])
            if b is not None and bal is None:
                bal = b                      # 表格按时间倒序 → 第一条即最新余额
        return {"recharge": (round(rec, 2) if n_rec else None),
                "consume": (round(con, 2) if n_con else None),
                "balance": bal,
                "n_recharge": n_rec, "n_consume": n_con}

    # 对账页(推广费财务对账, 支持自定义时间区间): 2026-10-10 用户指路 ——
    # 「推广充值页面选历史时间时, 消费记录要翻页找不好找 → 点消费记录进推广费财务对账页, 可自定义时间」。
    # 直接把 wmPoiId 放 URL 里(从点击实测的落地 URL 取得, 无 bsid 等会话参数)。
    BILL_RECON_URL = ("https://waimaieapp.meituan.com/finance/static/html_pc/billReconciliation.html"
                      "#/account-flow?wmPoiId={store_id}")

    @staticmethod
    def parse_bill_ledger(rows, date):
        """对账页「推广费流水记录」表 → 当日充值(CJ)合计 + 余额。

        真机表(2026-10-10, 高桥 55555555): 表头 日期|类型|金额(元)|现有余额|状态|交易号
            ["2026-10-06 20:51:05", "推广账户自动充值", "+200.00", "￥396.25", "交易成功", "..."]
        口径: 只认「类型含充值」且金额带 '+' 的行; 同日多笔累加; 该日无行 → None(留空, 不臆造 0)。
        ⚠️ 消耗行「推广订单扣款」**不并入**: 其实测归属日与旧账户页口径相差一天(2026-10-10 三组
           对账: 旧页"10-06 推广消费 -250" = 对账页"10-07 00:22:29 推广订单扣款 -250"),
           消耗(CE)仍走旧页 parse_ledger —— 此处不读、也不做换算, 防口径漂移。
        """
        total, hits, bal = None, 0, None
        for r in (rows or []):
            if not isinstance(r, (list, tuple)) or len(r) < 3:
                continue
            t = str(r[0]).strip()
            if not t.startswith(str(date)):
                continue
            typ = str(r[1]).replace("\n", " ").strip()
            amt = str(r[2]).strip().replace(",", "").replace("￥", "")
            if "充值" in typ and amt.startswith("+"):
                try:
                    v = float(amt[1:])
                except ValueError:
                    continue
                total = (total or 0.0) + v
                hits += 1
                if len(r) > 3 and str(r[3]).strip():
                    try:
                        bal = float(str(r[3]).replace(",", "").replace("￥", ""))
                    except ValueError:
                        pass
        return {"recharge": round(total, 2) if (hits and total is not None) else None,
                "balance": bal, "n_recharge": hits}

    def _click_by_text(self, page, text, timeout_ms=6000):
        """精确文本点击(打标记 + locator, 只取最内层)。成功返回 True。"""
        try:
            n = page.evaluate("""(kw) => {
                const els = [...document.querySelectorAll('a,span,div,li,button,label')]
                  .filter(e => (e.innerText || '').trim() === kw);
                const inner = els.filter(e => !els.some(o => o !== e && e.contains(o)));
                document.querySelectorAll('[data-hm-bill]').forEach(e => e.removeAttribute('data-hm-bill'));
                if (!inner.length) return 0;
                inner[0].setAttribute('data-hm-bill', '1'); return inner.length; }""", text)
            if n:
                page.locator('[data-hm-bill="1"]').first.click(timeout=timeout_ms)
                return True
        except Exception:
            pass
        return False

    @staticmethod
    def _bill_rows(page, expect_col=None):
        """对账页流水数据行: 选带「日期」表头的那张表; 给了 expect_col 则表头还须含该列名
        (推广费 tab=「交易号」, 余额 tab=「操作」—— 用来识别 tab 是否真的切过去了)。读不到 → None。"""
        try:
            tbls = page.evaluate("""() => [...document.querySelectorAll('table')].map(tb =>
                [...tb.querySelectorAll('tr')].map(tr =>
                  [...tr.querySelectorAll('td,th')].map(td => (td.innerText||'').trim())))""")
        except Exception:
            return None
        for tb in (tbls or []):
            if not tb:
                continue
            head = next((r for r in tb if r and str(r[0]).startswith("日期")), None)
            if head is None:
                continue
            if expect_col and not any(expect_col in str(c) for c in head):
                continue
            return [r for r in tb if r and not str(r[0]).startswith("日期")]
        return None

    def _set_bill_inputs(self, page, date):
        """对账页两个日期框: Ctrl+A 全选替换 + 回车 ×2 + **点空白失焦提交**(2026-10-10 实测:
        仅回车表格不刷新, 失焦后才提交过滤)。置好后读回校验两框 == date。"""
        for i in (0, 1):
            try:
                page.evaluate("""(i) => {
                    const ins = [...document.querySelectorAll('input')].filter(x => x.getBoundingClientRect().width > 30);
                    if (ins[i]) { ins[i].click(); ins[i].focus(); } }""", i)
                time.sleep(0.4)
                page.keyboard.press("ControlOrMeta+a")
                time.sleep(0.15)
                page.keyboard.type(date, delay=30)
                time.sleep(0.3)
                page.keyboard.press("Enter")
                time.sleep(0.5)
            except Exception:
                return False
        try:
            page.mouse.click(600, 30)          # 失焦提交(点页头空白); 不行再 Tab 兜底
        except Exception:
            try:
                page.keyboard.press("Tab")
            except Exception:
                pass
        time.sleep(1.0)
        try:
            vals = page.evaluate("() => [...document.querySelectorAll('input')]"
                                 ".filter(x => x.getBoundingClientRect().width > 30).map(i => (i.value || '').trim())")
        except Exception:
            return False
        return sum(1 for v in (vals or []) if v == date) >= 2

    def _read_recharge_bill(self, store_id, date):
        """对账页(推广费财务对账)按日期过滤 → 当日充值(CJ) + 余额。失败返回 None(调用方留空)。

        2026-10-10 真机配方: 开页 → 「推广费流水记录」tab → 「自定义」 → 两个输入框
        Ctrl+A 全选替换 + 回车×2 → 点空白失焦 → 轮询校验(**所有数据行==目标日 或 「暂无数据」**
        才算过滤生效)。过滤未得证 → None —— 宁可不写, 不拿未过滤的残表冒充。
        """
        page = None
        try:
            page = self.ctx.new_page()
            page.goto(self.BILL_RECON_URL.format(store_id=store_id), wait_until="domcontentloaded", timeout=60000)
            ready = False
            for _ in range(20):
                time.sleep(0.5)
                try:
                    if "余额流水记录" in (page.evaluate("() => document.body ? document.body.innerText : ''") or ""):
                        ready = True
                        break
                except Exception:
                    pass
            if not ready:
                self.sink.log("   ⚠️ 对账页未就绪 → 充值留空", level="warn")
                return None
            # 切「推广费流水记录」tab: 首点可能因页面未渲染好落空 → 校验表头(交易号)重试。
            # 2026-10-10 实测: tab 没切过去时会误读「余额流水记录」的表 → 必须验证, 否则宁可不写
            tab_ok = False
            for _ in range(6):
                self._click_by_text(page, "推广费流水记录")
                time.sleep(1.0)
                if self._bill_rows(page, "交易号") is not None:
                    tab_ok = True
                    break
            if not tab_ok:
                self.sink.log("   ⚠️ 对账页「推广费流水记录」tab 未能就绪 → 充值留空", level="warn")
                return None
            if not self._click_by_text(page, "自定义"):
                self.sink.log("   ⚠️ 对账页未找到「自定义」→ 充值留空", level="warn")
                return None
            time.sleep(1.0)
            if not self._set_bill_inputs(page, date):
                self.sink.log("   ⚠️ 对账页日期设置未生效 → 充值留空", level="warn")
                return None
            # 轮询等过滤生效(上限 ~10s):
            #   · 数据行全==date → 立即接受;
            #   · 「暂无数据」必须**持续 ≥2.5s** 才认 —— 查询期间表格会闪一下空态,
            #     第一眼就信会把"有数据的日子"误判成"该日无充值"(2026-10-10 实测 10-05 踩到)
            empty_since = None
            for _ in range(20):
                time.sleep(0.5)
                body = self._bill_rows(page, "交易号")
                if body and all(str(r[0]).startswith(str(date)) for r in body):
                    return self.parse_bill_ledger(body, date)
                try:
                    t = page.evaluate("() => document.body ? document.body.innerText : ''") or ""
                except Exception:
                    t = ""
                if (not body) and ("暂无数据" in t):
                    empty_since = empty_since or time.time()
                    if time.time() - empty_since >= 2.5:
                        return {"recharge": None, "balance": None, "n_recharge": 0}
                else:
                    empty_since = None
            self.sink.log("   ⚠️ 对账页日期过滤未能验证(行既非目标日也非持续为空) → 充值留空", level="warn")
            return None
        except Exception as e:
            self.sink.log(f"   ⚠️ 对账页读取失败: {str(e)[:70]} → 充值留空", level="warn")
            return None
        finally:
            if page is not None:
                try:
                    page.close()
                except Exception:
                    pass

    def capture_promo_ledger(self, store_id, date):
        """抓该店(单门店视角)推广账户流水 → CJ/CE/余额。失败返回 None(调用方留空)。

        2026-10-10 升级: 旧页对历史日期要翻页找(用户痛点) → 充值(CJ)在旧页没读到时
        回退「对账页」按自定义时间直取(见 _read_recharge_bill); 消耗(CE)仍以旧页为准
        (两源归属日口径差一天, 不换算)。
        """
        saved = [(c["domain"], c["path"], c["value"]) for c in self.ctx.cookies()
                 if c["name"] == self._POI_COOKIE]
        page = None
        try:
            self._set_poi_cookie(str(store_id))
            page = self.ctx.new_page()
            page.goto(self.PROMO_LEDGER_URL, wait_until="domcontentloaded", timeout=90000)
            rows = None
            for _ in range(40):                         # 1s 轮询(原 4s×10): 行出现即走, 上限同为 ~40s
                time.sleep(1)
                rows = page.evaluate("""() => {
                    const tb = document.querySelector('table');
                    if (!tb) return null;
                    return [...tb.querySelectorAll('tr')].map(tr =>
                        [...tr.querySelectorAll('td,th')].map(td => (td.innerText||'').trim())); }""")
                if rows and len(rows) > 1:
                    break
            if not rows or len(rows) <= 1:
                self.sink.log("⚠️ 推广流水: 表格未渲染出来(账户页结构可能变化) → 尝试对账页补充值", level="warn")
                out = None
            else:
                out = self.parse_ledger(rows[1:], date)       # 去掉表头
            # 充值没读到(历史日期在旧页被翻页挡住/表未渲染) → 对账页按自定义时间直取
            src = "账户页" if out is not None else "对账页"
            if out is None or out.get("recharge") is None:
                try:
                    bill = self._read_recharge_bill(str(store_id), date)
                except Exception:
                    bill = None
                if bill is not None:
                    if out is None:
                        out = {"recharge": None, "consume": None, "balance": None,
                               "n_recharge": 0, "n_consume": 0}
                    if bill.get("recharge") is not None:
                        out["recharge"] = bill["recharge"]
                        out["n_recharge"] = bill.get("n_recharge", 0)
                        src = ("对账页" if out.get("consume") is None else "账户页+对账页")
                    if out.get("balance") is None:
                        out["balance"] = bill.get("balance")
            if out is None:
                return None
            self.sink.log(f"📒 推广账户({store_id}) {date}: 充值 {out['recharge']} / 消耗 {out['consume']}"
                          f" / 余额 {out['balance']}（充值{out['n_recharge']}笔 消费{out['n_consume']}笔·{src}）")
            return out
        except Exception as e:
            self.sink.log(f"⚠️ 推广流水抓取失败(该店 CE/CJ 留空): {str(e)[:80]}", level="warn")
            return None
        finally:
            try:
                if page is not None:
                    page.close()
            except Exception:
                pass
            try:
                if saved:
                    self.ctx.add_cookies([{"name": self._POI_COOKIE, "value": v, "domain": d, "path": p}
                                          for d, p, v in saved])
                else:
                    self._set_poi_cookie("-1")
            except Exception:
                pass

    def capture_promo(self):
        """推广三大数值(昨日口径)。要点:
        - 内容在跨域 iframe, 直接独立打开 iframe URL(外壳页 iframe 懒加载极不稳)
        - 日期仅 昨日/近7日/近30日 快捷组, 无自定义区间 → 只支持昨日口径
        - 抓不到时抛异常由调用方降级(列留空), 不阻塞主流程
        """
        page = self.ctx.new_page()
        try:
            page.goto(self.PROMO_URL, wait_until="domcontentloaded", timeout=90000)
            data = None
            for _ in range(15):
                time.sleep(4)
                try:
                    data = page.evaluate(self._PROMO_READ_JS)
                except Exception:
                    data = None
                if data:
                    break
            if not data:
                raise RuntimeError("推广页未渲染出数据(iframe内容加载超时)")
            self.sink.log(f"📢 推广(昨日 {data['date']}): 花费 {data['spend']} / "
                          f"曝光 {data['expose']} / 进店 {data['visit']}")
            return data
        finally:
            try:
                alive = [p for p in self.ctx.pages if not p.is_closed()]
                if len(alive) > 1:
                    page.close()
                else:
                    # 关掉最后一个页面会连带关闭整个 persistent context(后续全部操作报
                    # "Target page, context or browser has been closed") → 只导航走, 不关
                    page.goto("about:blank")
            except Exception:
                pass
