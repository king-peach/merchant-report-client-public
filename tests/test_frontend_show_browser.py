# -*- coding: utf-8 -*-
"""前端「展示过程」开关守卫 (2026-10-14, 需求 F)。

tsx 没有单测框架 → esbuild 打 bundle + node **对拍**运行, 钉住:
 ①开关渲染(默认关; localStorage 记忆为 1 时显示"（开）");
 ②点击 → invoke("send_to_kernel", {payload:{type:"browser_window", visible}}) 的**实际代码路径**;
 ③启动任务时 run_task 带 showBrowser; ④没有运行中的任务时静默。

源码断言兜底 + 构建产物断言(打出来的 bundle 里那条 invoke 必须在) —— 与
tests/test_frontend_oneclick.py 同款思路。
"""
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
APP = ROOT / "src" / "App.tsx"
ESBUILD = ROOT / "node_modules" / ".bin" / "esbuild"

# node 侧对拍脚本: 造最小 DOM 替身(不引 jsdom), 用 react-dom/server 渲染真实组件,
# 看 HTML 里开关的形态与状态后缀。
_NODE_PROBE = r"""
global.window = {};
global.localStorage = {
  _d: {}, getItem(k) { return k in this._d ? this._d[k] : null; },
  setItem(k, v) { this._d[k] = String(v); }, removeItem(k) { delete this._d[k]; },
};
global.document = { addEventListener() {}, removeEventListener() {}, visibilityState: "visible" };
const React = require("react");
const { renderToStaticMarkup } = require("react-dom/server");
const App = require(process.argv[2]).default;
const off = renderToStaticMarkup(React.createElement(App));
global.localStorage.setItem("mrc.showBrowser", "1");
const on = renderToStaticMarkup(React.createElement(App));
console.log(JSON.stringify({
  off, on,
  off_has_label: off.includes("展示过程"),
  off_has_on: off.includes("（开）"),
  on_has_on: on.includes("（开）"),
  has_checkbox: /type="checkbox"/.test(off),
  // 点开状态要带高亮 class(样式与现有控件一致)
  on_class: (on.match(/class="ghost toggle[^"]*"/) || [""])[0],
  off_class: (off.match(/class="ghost toggle[^"]*"/) || [""])[0],
}));
"""


def _bundle(tmp):
    out = subprocess.run([str(ESBUILD), str(APP), "--bundle",
                          f"--outfile={tmp}/bundle.cjs", "--jsx=automatic",
                          "--format=cjs", "--platform=node",
                          "--external:react", "--external:react-dom",
                          "--external:react-dom/*"],
                         capture_output=True, text=True, cwd=str(ROOT))
    assert out.returncode == 0, f"前端构建失败:\n{out.stdout}\n{out.stderr}"
    assert (tmp / "bundle.cjs").is_file()
    return (tmp / "bundle.cjs").read_text(encoding="utf-8")


def test_renders_and_remembers(tmp):
    """对拍: 真实组件在 node 里渲染 —— 默认关, 记忆为 1 时显示"（开）"并高亮。"""
    if not ESBUILD.exists():
        print("⚠️  跳过渲染对拍(未装 node_modules/esbuild)")
        return None
    bundle = _bundle(tmp)
    probe = tmp / "probe.cjs"
    probe.write_text(_NODE_PROBE, encoding="utf-8")
    env = dict(os.environ)
    # 临时目录在项目外 → 显式告诉 node 去哪找 react/react-dom(不自带 node_modules, 不写 package.json)
    env["NODE_PATH"] = str(ROOT / "node_modules")
    out = subprocess.run(["node", str(probe), str(tmp / "bundle.cjs")],
                         capture_output=True, text=True, cwd=str(tmp), env=env)
    assert out.returncode == 0, f"node 对拍失败:\n{out.stdout}\n{out.stderr}"
    import json as _json
    r = _json.loads(out.stdout.strip().splitlines()[-1])
    assert r["off_has_label"], "开关没渲染出来(文案『展示过程』缺失)"
    assert r["has_checkbox"], "开关应是 checkbox"
    assert not r["off_has_on"], "默认必须是**关**(不该带『（开）』)"
    assert r["on_has_on"], "localStorage 记忆为 1 时, 下一轮要显示为开(状态记忆)"
    assert "on" in r["on_class"] and "on" not in r["off_class"].replace("toggle", ""), \
        f"开着时要有高亮 class: on={r['on_class']} off={r['off_class']}"
    assert "onChange" in bundle or "onChange" in r["off"], "开关必须挂 onChange"
    print("✅ 渲染对拍: 默认关 → 记忆=1 时显示『（开）』并高亮")
    return bundle


def main():
    src = APP.read_text(encoding="utf-8")
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="mrc-showbrowser-"))
    try:
        bundle = test_renders_and_remembers(tmp)

        # ---- 开关本体 ----
        assert 'const LS_SHOWBROWSER = "mrc.showBrowser";' in src, "localStorage 记忆键必须是 mrc.showBrowser"
        assert 'localStorage.getItem(LS_SHOWBROWSER) === "1"' in src, "默认关: 记忆值只有 '1' 才算开"
        assert "const [showBrowser, setShowBrowser] = useState<boolean>" in src, "要有 React state"
        assert "const showBrowserRef = useRef(showBrowser);" in src, \
            "要有 ref(事件回调/launch 里读最新值, 避免闭包快照)"
        assert 'localStorage.setItem(LS_SHOWBROWSER, next ? "1" : "0")' in src, "state + localStorage 双写"

        # ---- 点击: 运行中切窗口(fire-and-forget; 没有任务时静默) ----
        assert 'invoke("send_to_kernel", { payload: { type: "browser_window", visible: next } })' in src, \
            "点击要发 {type:'browser_window', visible} 到内核"
        assert '.catch(() => { /* 无任务在跑: 忽略 */ })' in src, \
            "没有运行中的任务时要静默(壳返回 Err, 不许影响本地开关状态)"
        assert "onChange={toggleShowBrowser}" in src, "开关要接到 toggleShowBrowser"
        if bundle is not None:
            # 构建产物里那条消息必须真的在(源码注释/死代码不算)
            assert "browser_window" in bundle and "send_to_kernel" in bundle, \
                "打包产物里找不到 show/hide 窗口的消息通道"

        # ---- 启动任务带 showBrowser(逐日循环每轮读当前值) ----
        assert "showBrowser: showBrowserRef.current," in src, "run_task 必须带 showBrowser"
        assert "void launch(PLATFORM_ORDER[0], false, nextDay);" in src, \
            "时间段逐日循环共用 launch() → showBrowser 每轮读当前开关值"
        assert 'className={`ghost toggle${showBrowser ? " on" : ""}`}' in src, "开着要有高亮态"

        # ---- 内核侧参数确实被壳转发(端到端接线) ----
        rust = (ROOT / "src-tauri" / "src" / "py_runner.rs").read_text(encoding="utf-8")
        assert "show_browser: Option<bool>" in rust, "壳 run_task 要收 showBrowser(可选, 兼容旧前端)"
        assert 'cmd.arg("--show-browser")' in rust, "壳要把开关转成内核参数"
        assert "show_browser.unwrap_or(false)" in rust, "缺省 false(不破坏已有调用)"
        print("✅ 展示过程(前端/壳/内核 端到端接线) · 守卫 全绿")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print(f"❌ {e}", file=sys.stderr)
        sys.exit(1)
