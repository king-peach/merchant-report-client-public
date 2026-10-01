# -*- coding: utf-8 -*-
"""美团外卖商家后台适配器 (https://e.waimai.meituan.com/)。
登录: e.waimai 账号密码表单(平台: 美团统一登录)。
报表: 脑图记录 营业分析→报表下载(全部业务+拼好饭); 经营数据→流量; 推广→消费记录。
状态: 登录已实现并实测入口; 报表/抓取待用真实账号实测页面结构后完善(M5)。"""
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
        self.page = page = self.ctx.new_page()
        page.goto(MEITUAN_HOME, wait_until="commit")
        time.sleep(6)

        def logged():
            try:
                return "账号登录" not in (page.evaluate("() => document.body.innerText.slice(0, 500)") or "")
            except Exception:
                return False

        if logged():
            self.sink.log("✅ 美团登录成功")
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

        # 轮询登录结果
        end = time.time() + timeout_s
        while time.time() < end:
            time.sleep(4)
            if logged():
                self.sink.log("✅ 美团登录成功")
                return True
        self.sink.step_fail("login", "登录", "美团登录超时(含人工等待)", "E_LOGIN_TIMEOUT")
        return False

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
    _DAY_JS = """(d) => {
      const out = [];
      for (const el of document.querySelectorAll('a,td,span,div')) {
        if ((el.textContent || '').trim() !== d) continue;
        if (/prev|next|other|disabled|outside/i.test(String(el.className || ''))) continue;
        if (el.children.length > 1) continue;
        const r = el.getBoundingClientRect();
        if (r.width <= 0 || r.height <= 0 || r.width > 70 || r.height > 60 || r.y < 330) continue;
        out.push({x: r.x + r.width / 2, y: r.y + r.height / 2, a: r.width * r.height});
      }
      out.sort((p, q) => p.a - q.a);
      return out[0] || null;
    }"""
    _MONTH_JS = """() => {
      for (const el of document.querySelectorAll('[class*=picker],[class*=calendar],[class*=month]')) {
        const r = el.getBoundingClientRect();
        if (r.width < 100 || r.height < 100) continue;
        const t = (el.innerText || el.textContent || '').slice(0, 200);
        const g = t.match(/(20\\d{2})\\s*年\\s*(\\d{1,2})\\s*月/);
        if (g) return {y: +g[1], m: +g[2]};
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
        for i, d in enumerate((df, dt)):
            try:
                loc = page.locator("input.roo-input").nth(i)
                loc.click()
                loc.fill("")
                loc.type(d, delay=40)
                page.keyboard.press("Enter")
                time.sleep(0.8)
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
        """点日历格; 面板月份不对时先翻月(实测面板 class 是 roo-date-time-picker*)"""
        y, m, d = (int(x) for x in target.split("-"))
        for attempt in range(28):
            cur = self._panel_month(page)
            if cur is None:
                if attempt >= 1:
                    return self._try_day(page, d)
                time.sleep(0.6)
                continue
            if cur == (y, m):
                return self._try_day(page, d)
            if not self._nav_month(page, (y, m) > cur):
                return False
            time.sleep(0.7)
        return False

    def _panel_month(self, page):
        try:
            g = page.evaluate(self._MONTH_JS)
            return (g["y"], g["m"]) if g else None
        except Exception:
            return None

    def _nav_month(self, page, forward):
        box = page.evaluate("""(fwd) => {
          const ps = [...document.querySelectorAll('[class*=picker],[class*=calendar]')]
            .filter(e => { const r = e.getBoundingClientRect(); return r.width > 100 && r.height > 100; });
          const roots = ps.length ? [ps[ps.length - 1]] : [document.body];
          for (const root of roots) {
            const out = [];
            for (const el of root.querySelectorAll('button,span,i,a,div')) {
              const c = String(el.className || '').toLowerCase();
              if (fwd ? !/next|right|after|forward/.test(c) : !/prev|left|before|back/.test(c)) continue;
              const r = el.getBoundingClientRect();
              if (r.width <= 0 || r.height <= 0 || r.width > 60) continue;
              out.push({x: r.x + r.width / 2, y: r.y + r.height / 2});
            }
            if (out.length) return out[0];
          }
          return null;
        }""", bool(forward))
        if not box:
            return False
        page.mouse.click(box["x"], box["y"])
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
                time.sleep(3)
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
            # 外卖 = 流量转化页的默认视图(2026-09-28 用户截图确认: 该页就是外卖口径,
            # 对比基准下拉就在漏斗右上角)。旧代码把「业务切换成功」当成前置条件, 切不过就
            # continue → 整店数据全丢(真机 31 店 TOP 全空就是这个原因)。
            bench = self._set_bench_top10(page)
            if bench:
                result["bench"] = bench
            got = {}
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
                    got[grp] = side
            if got:
                _bench = result["bench"] or ""
                if self._bench_is_circle(_bench):
                    result["main"] = got
                    self.sink.log(f"📊 商圈({_bench}): "
                                  + " ".join(f"{g}曝光{v.get('曝光')}" for g, v in got.items()))
                else:
                    # 硬门禁: 对比基准若还是「比前一日/比上周」这类**别的口径**, 右值不是
                    # 商圈同行数据 → 一律不写(宁可留空, 也绝不把别的口径写进客户表)。
                    self.sink.log(f"⚠️ 商圈: 对比基准为「{_bench or '未知'}」而非商圈同行 → "
                                  f"本次不写 TOP 列(避免把别的口径当商圈数据)", level="warn")
            else:
                self.sink.log("⚠️ 商圈: 三组(全部/新客/老客)都没读到右值 —— 见上方取证",
                              level="warn")
            # 拼好饭: 截图显示它在外卖商家中心是**独立顶部板块**(顶栏「拼好饭 新」), 不是本页
            # 的业务下拉 → 本页取不到。保持留空并写明, 绝不拿外卖数据冒充拼好饭。
            result["bao"] = {}
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

    def _set_flow_date(self, page, date):
        """把流量页日期设为指定日(「自定义」+ 两个日期输入框) —— **必须拿到硬证据才算生效**。

        ⚠️ 2026-10-02 真机(用户报"美团没按报表区间处理, 默认选的昨日"): 流量页(商圈采集用的
        页面)快捷组默认「昨日」; 旧实现设完只做一次很弱的文本包含判断(`date in body`), 而且
        **调用方忽略了返回值** → 日志里 `📅 流量页日期 → …（未确认）` 之后照抓 → 商圈TOP 列
        很可能是**昨日**的数(数字看着正常、日期是错的 = 最危险的那种错)。
        现在: ① 直接读两个输入框的 value(最硬); ② 再看页面有没有「已选时间：<date>」这类标记;
        两种日期写法都试(2026-09-30 / 2026/09/30 / 2026.09.30 / 09-30)。都不成立 → 返回 False,
        由 capture_circle **放弃该店**(留空), 不再拿错日期的数往表里写。
        """
        try:
            page.evaluate("""() => { for (const el of document.querySelectorAll('.selector-item')) {
                if ((el.innerText||'').trim() === '自定义') { el.click(); return true; } } return false; }""")
            time.sleep(3)
            ins = page.locator("input[placeholder='开始时间'], input[placeholder='结束时间']")
            n = ins.count()
            if n >= 2:
                ins.nth(0).fill(""); ins.nth(0).type(date, delay=40)
                ins.nth(1).fill(""); ins.nth(1).type(date, delay=40)
                page.keyboard.press("Enter")
                time.sleep(3)
            # ① 输入框 value(硬证据)
            vals = []
            for i in range(min(ins.count(), 2)):
                try:
                    vals.append((ins.nth(i).input_value() or "").strip())
                except Exception:
                    vals.append("")
            hit_input = len(vals) == 2 and all(v == date for v in vals)
            # ② 页面标记(硬证据: 「已选时间：<date>」等)
            txt = page.evaluate("() => (document.body?document.body.innerText:'')") or ""
            marks = [f"已选时间：{d}" for d in self._date_variants(date)]
            hit_text = any(m in txt for m in marks)
            ok = hit_input or hit_text
            self.sink.log(f"📅 流量页日期 → {date}"
                          f"（{'已生效' if ok else '未确认'}"
                          f"{'·输入框=' + str(vals) if vals else ''}）")
            if not ok:
                # 留证据给下一轮: 页面上所有含数字/日期的文本片段
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

    def _set_flow_biz(self, page, biz):
        """切业务: 全部 / 外卖 / 拼好饭(顶部业务下拉)。"""
        try:
            box = page.evaluate("""() => { for (const el of document.querySelectorAll('.dropdown')) {
                const r = el.getBoundingClientRect();
                if (r.width > 0 && r.top > 80 && r.top < 140 && r.left > 900)
                  return {x: r.x + r.width/2, y: r.y + r.height/2}; } return null; }""")
            if not box:
                self._dump_flow_candidates(page, "没找到业务下拉(位置启发式落空)")
                return False
            page.mouse.click(box["x"], box["y"])
            time.sleep(3)
            hit = bool(page.evaluate("""(lab) => { const norm = s => (s||'').replace(/\\s+/g,'');
                for (const el of document.querySelectorAll('li,div,span')) {
                  const r = el.getBoundingClientRect();
                  if (r.width > 0 && el.children.length === 0 && norm(el.innerText) === norm(lab))
                    { el.click(); return true; } } return false; }""", biz))
            if not hit:
                self._dump_flow_candidates(page, f"下拉展开后没有「{biz}」选项")
            return hit
        except Exception:
            return False

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
