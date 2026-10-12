# -*- coding: utf-8 -*-
"""浏览器被连关、自愈耗尽后的收尾: 必须报真实原因 + 操作指引, 不许盖成"平台没生成报表"(2026-10-12 真机)。

现场: Windows 真机淘宝闪购, 自动化浏览器被连关 3 次(重开后又被关), 自愈循环耗尽。
旧收尾报 E_NO_REPORT「平台未生成报表」→ 用户被引去怀疑平台/日期(方向错)。
本文件锁三件事:
  ① root_cause 把真机原始错误文本判成 E_BROWSER_DEAD(且可自愈);
  ② 收尾文案 helper 给出三条可操作指引(别关窗/退出双开实例含托盘/放行安全软件) + 重开次数;
  ③ run_task 里真的接线: 浏览器类末次失败 → 真实原因收尾, 且接线点在笼统 E_NO_REPORT 收尾之前。
"""
import inspect
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "kernel"))
from merchant_report.diagnose import root_cause      # noqa: E402
from merchant_report import orchestrator as O        # noqa: E402


def main():
    # ① 截图里的原始错误文本 → 必须判为 E_BROWSER_DEAD 且可自愈
    c = root_cause("Frame.evaluate: Target page, context or browser has been closed", "Error")
    assert c.get("code") == "E_BROWSER_DEAD", c
    assert c.get("auto_recover") is True, c
    print("✅ 真机错误文本 → E_BROWSER_DEAD(可自愈)")

    # ② 收尾文案: 三条指引齐全, 带重开次数; 迭代次数为 0 时也不出"重开 0 次"的怪句
    note = O._browser_dead_exhausted_note(2)
    for kw in ("已自动重开 2 次", "手动关闭", "托盘", "安全软件", "生成日报"):
        assert kw in note, (kw, note)
    assert "平台未生成" not in note
    note0 = O._browser_dead_exhausted_note(0)
    assert "重开 0 次" not in note0 and "托盘" in note0
    print("✅ 收尾文案含三条操作指引 + 重开次数")

    # ③ run_task 接线: 自愈耗尽分支出现在笼统 E_NO_REPORT 收尾之前(防回退成"被盖掉")
    src = inspect.getsource(O.run_task)
    i_note = src.find("_browser_dead_exhausted_note(relaunched)")
    i_generic = src.find("平台未生成 {date_from}~{date} 的报表")
    assert i_note != -1, "run_task 必须引用 _browser_dead_exhausted_note(relaunched)"
    assert i_generic != -1, "笼统 E_NO_REPORT 收尾必须还在(非浏览器类失败仍走它)"
    assert i_note < i_generic, "浏览器类收尾必须接在笼统 E_NO_REPORT 之前"
    assert "_lc.get(\"auto_recover\")" in src, "耗尽收尾必须按末次失败根因分流"
    print("✅ run_task 接线: 浏览器类末次失败 → 真实原因收尾")

    print("\n✅ 浏览器被连关收尾(真实原因+操作指引)全绿")


if __name__ == "__main__":
    main()
