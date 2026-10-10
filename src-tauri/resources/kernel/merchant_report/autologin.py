# -*- coding: utf-8 -*-
"""自动登录: 打开登录页 → 自动填账密(商家平台配置) → 点登录 → 智能处理验证:
- 直接成功 → 返回
- 出滑块 → 人类轨迹自动拖(贝塞尔+抖动+变速), 失败重试(最多3次)
- 出短信/滚验证 → need_human 提醒人工
- 反复失败 → need_human 兜底人工
"""
import math
import random
import time

from .events import EventSink, HumanBridge


def _human_drag(page, slider_xy, track_width, total_w):
    """人类轨迹拖滑块: 从滑块当前位置拖到目标(贝塞尔缓动 + 随机抖动 + 变速)。
    滑块需拖动的距离 = 缺口位置随机化(淘宝缺口前端可见, 这里用轨道满程的 0.42~0.58 模拟)"""
    x0, y0 = slider_xy
    dist = int(total_w * random.uniform(0.42, 0.58))
    # 轨迹: 先快后慢(人手特征) + 轻微 y 抖动
    points = []
    steps = random.randint(28, 42)
    for i in range(steps):
        t = i / steps
        # easeOutCubic 缓动
        e = 1 - (1 - t) ** 3
        px = x0 + dist * e + random.uniform(-1.5, 1.5)
        py = y0 + math.sin(t * math.pi) * random.uniform(-3, 3)
        points.append((px, py))
    points.append((x0 + dist, y0))
    page.mouse.move(x0, y0)
    page.mouse.down()
    for px, py in points:
        page.mouse.move(px, py)
        time.sleep(random.uniform(0.008, 0.022))   # 变速
    time.sleep(random.uniform(0.1, 0.3))           # 末段停顿
    page.mouse.up()


def _detect_slider(page):
    """检测滑块轨道(返回拖动所需坐标) 或 None。"""
    try:
        return page.evaluate("""() => {
          const track = document.querySelector('[class*="nc-lang-cnt"], [class*="slider-track"], [class*="nc_iconfont"], [class*="scale_text"], [id*="nc_"], [class*="verify-track"]');
          if (!track) return null;
          const r = track.getBoundingClientRect();
          if (r.width < 100) return null;
          const handle = document.querySelector('[class*="nc_iconfont"], [class*="slider-handle"], [class*="btn_slide"], [class*="nc_"] [class*="icon"]');
          const hr = (handle || track).getBoundingClientRect();
          return {hx: hr.x + hr.width/2, hy: hr.y + hr.height/2, trackW: r.width};
        }""")
    except Exception:
        return None


def _needs_human_verify(page):
    """页面是否在要求人工验证(滑块/短信/滚动验证/空间推理…)。"""
    try:
        return bool(page.evaluate("""() => {
          const t = document.body.innerText || '';
          return /滑动解锁|身份验证|短信|验证码|滚动验证|空间推理|请输入验证|人工审核|安全验证/.test(t);
        }"""))
    except Exception:
        return False


VERIFY_HINT = ("平台要求身份验证：请在弹出的浏览器窗口里手动完成"
               "（推荐点『扫码登录』用手机扫，比滑块稳），完成后点『已完成登录』。"
               "请不要刷新页面——刷新会让风控重新评分。")


def auto_login(ad, username: str, password: str, sink: EventSink, human: HumanBridge,
               timeout_s=180, max_slider_rounds=3) -> bool:
    """自动登录全流程。ad: TaobaoAdapter(已 ensure_login 失败后调用)。返回最终是否登录。

    验证控件策略(L1 决策, 2026-09-22): **默认不程序化拖滑块**——合成鼠标轨迹几乎必被判失败,
    且每轮失败都推高风控评分, 会把用户手动通过的路也堵死(实测: 3 轮自动拖后手动滑也过不去,
    页面以 error 码结束)。改为检测到验证控件立即交人工(提示扫码登录), 之后不刷新页面只轮询。
    需要旧行为时显式设 ad.auto_slider=True。
    """
    page = ad.page or ad.ctx.pages[0]
    home = ad.STATS_HOME if hasattr(ad, "STATS_HOME") else "https://melody.shop.ele.me/"
    page.goto(home, wait_until="commit")
    time.sleep(5)

    # 已登录则直接返回
    if ad._logged_in():
        return True

    if not (username and password):
        sink.need_human("login", "未配置账号密码, 请在浏览器手动登录", platform="taobao",
                        timeout_s=300)
        end = time.time() + 300
        while time.time() < end:
            time.sleep(4)
            if ad._logged_in():
                return True
        return False

    # 填账密
    try:
        page.evaluate("""(args) => {
          const [u, p] = args;
          const set = (el, v) => {
            const s = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
            s.call(el, v);
            el.dispatchEvent(new Event('input', {bubbles: true}));
            el.dispatchEvent(new Event('change', {bubbles: true}));
          };
          const pw = [...document.querySelectorAll('input')].find(i => i.type === 'password' && i.getBoundingClientRect().width > 20);
          if (pw) set(pw, p);
          const acc = [...document.querySelectorAll('input')].find(i => i !== pw && (i.placeholder||'').match(/账号|手机|用户/) || (i.type === 'text' && i.getBoundingClientRect().width > 100));
          if (acc) set(acc, u);
        }""", [username, password])
        sink.log("📝 已自动填充账号密码")
    except Exception as e:
        sink.log(f"⚠️ 自动填充失败: {str(e)[:60]}, 请手动输入", level="warn")

    time.sleep(1)
    # 点登录
    try:
        page.evaluate("""() => {
          for (const btn of document.querySelectorAll('button, [class*="submit"]')) {
            const t = (btn.innerText||'').trim();
            const r = btn.getBoundingClientRect();
            if (r.width > 40 && t.replace(/\\s/g,'') === '登录') { btn.click(); return; }
          }
        }""")
    except Exception:
        pass
    time.sleep(4)

    # 验证处理(L1): 默认不自动拖滑块 → 立即交人工, 且此后**不刷新页面**只轮询登录态
    auto_slider = bool(getattr(ad, "auto_slider", False))
    if not auto_slider:
        if _detect_slider(page) or _needs_human_verify(page):
            sink.log("🧩 检测到身份验证控件 → 交人工处理（不自动拖动：合成轨迹会被判失败并推高风控评分）")
            sink.need_human("login", VERIFY_HINT, platform="taobao", timeout_s=timeout_s)
            end = time.time() + timeout_s
            while time.time() < end:
                time.sleep(4)
                if ad._logged_in():
                    sink.log("✅ 人工完成验证后登录成功")
                    return True
            sink.log("⚠️ 等待人工验证超时", level="warn")
            return False
        # 没有验证控件: 等一会儿看是否直接登录成功
        time.sleep(4)
        if ad._logged_in():
            sink.log("✅ 自动登录成功")
            return True

    # auto_slider=True 时才走旧的"合成轨迹"路径(保留但不默认启用)
    for round_ in range(max_slider_rounds):
        if ad._logged_in():
            sink.log("✅ 自动登录成功")
            return True
        slider = _detect_slider(page)
        if slider:
            sink.log(f"🧩 检测到滑块, 自动拖动 (第{round_+1}/{max_slider_rounds}轮)…")
            try:
                _human_drag(page, (slider["hx"], slider["hy"]), slider["trackW"], slider["trackW"])
            except Exception as e:
                sink.log(f"⚠️ 拖动异常: {str(e)[:60]}")
            time.sleep(3)
            continue
        if _needs_human_verify(page):
            sink.need_human("login", VERIFY_HINT, platform="taobao", timeout_s=timeout_s)
            end = time.time() + timeout_s
            while time.time() < end:
                time.sleep(4)
                if ad._logged_in():
                    sink.log("✅ 人工验证后登录成功")
                    return True
            return False
        time.sleep(3)
    # 兜底人工
    sink.need_human("login", VERIFY_HINT, platform="taobao", timeout_s=timeout_s)
    end = time.time() + timeout_s
    while time.time() < end:
        time.sleep(4)
        if ad._logged_in():
            return True
    return False
